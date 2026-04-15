"""
Phase 3 tests — Database lifecycle workflows and approval system.

SSH execution is mocked; no real servers or databases are required.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.extensions import db
from app.models import (
    Cluster, ClusterStatus, DatabaseAsset, DatabaseProvenance, DatabaseRequest,
    DbAssetStatus, Node, OperationLog, OperationStatus, PermissionLevel, PgInstance,
    RequestStatus, RequestType, Team, TeamClusterPermission, User, UserRole,
)
from app.services.approval_workflow import ApprovalWorkflowService, WorkflowError
from app.services.provisioning import ProvisioningError, ProvisioningService


from tests.conftest import TestConfig, safe_drop_all


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cluster(name="test-cluster"):
    cluster = Cluster(
        name=name, load_balancer="lb.test.local",
        ssh_user="postgres", status=ClusterStatus.ACTIVE.value,
        create_db_script="/scripts/create_db.sh",
        delete_db_script="/scripts/delete_db.sh",
    )
    db.session.add(cluster)
    db.session.flush()
    db.session.add(Node(cluster_id=cluster.id, hostname="pg1.test.local"))
    return cluster


def _make_instance(cluster):
    inst = PgInstance(
        cluster_id=cluster.id, hostname="pg1.test.local",
        port=5432, instance_name="main", pgdata_dir="/data",
    )
    db.session.add(inst)
    db.session.flush()
    return inst


def _make_team(name="ops-team"):
    team = Team(name=name)
    db.session.add(team)
    db.session.flush()
    return team


def _make_user(team, role=UserRole.USER.value):
    user = User(
        email=f"{role}_{team.id}@test.com",
        username=f"{role}_{team.id}",
        role=role,
        team_id=team.id,
    )
    user.set_password("Pass123!")
    db.session.add(user)
    db.session.flush()
    return user


def _grant(team, cluster, level=PermissionLevel.DIRECT.value):
    perm = TeamClusterPermission(
        team_id=team.id, cluster_id=cluster.id, permission_level=level
    )
    db.session.add(perm)
    db.session.flush()
    return perm


def _mock_ssh_success(output="Database created."):
    """Patch paramiko so exec_command returns a success with given output."""
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stdout.read.return_value = output.encode()
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    return patch("paramiko.SSHClient", return_value=mock_client)


def _mock_ssh_failure(exit_code=1, output="ERROR: database already exists"):
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.channel.recv_exit_status.return_value = exit_code
    mock_stdout.read.return_value = output.encode()
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    return patch("paramiko.SSHClient", return_value=mock_client)


# ---------------------------------------------------------------------------
# ProvisioningService unit tests
# ---------------------------------------------------------------------------

class TestProvisioningService(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app(TestConfig)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        cluster = _make_cluster()
        self.instance = _make_instance(cluster)
        self.team = _make_team()
        self.user = _make_user(self.team)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        safe_drop_all(db)
        self.ctx.pop()

    def test_create_database_success(self):
        with _mock_ssh_success("Database created OK"):
            svc = ProvisioningService(self.instance, triggered_by=self.user)
            ok, output = svc.create_database("my_db")
        self.assertTrue(ok)
        self.assertIn("Database created OK", output)
        self.assertEqual(OperationLog.query.count(), 1)
        log = OperationLog.query.first()
        self.assertEqual(log.status, OperationStatus.SUCCESS.value)
        self.assertEqual(log.action, "create_db")

    def test_create_database_script_failure(self):
        with _mock_ssh_failure():
            svc = ProvisioningService(self.instance, triggered_by=self.user)
            ok, output = svc.create_database("my_db")
        self.assertFalse(ok)
        self.assertEqual(OperationLog.query.first().status, OperationStatus.FAILURE.value)

    def test_delete_database_success(self):
        with _mock_ssh_success("Database dropped."):
            svc = ProvisioningService(self.instance, triggered_by=self.user)
            ok, output = svc.delete_database("my_db")
        self.assertTrue(ok)
        self.assertIn("Database dropped.", output)

    def test_invalid_db_name_raises(self):
        svc = ProvisioningService(self.instance)
        with self.assertRaises(ProvisioningError) as ctx:
            svc.create_database("123-invalid-name!")
        self.assertIn("Invalid database name", str(ctx.exception))

    def test_command_built_correctly(self):
        svc = ProvisioningService(self.instance)
        cmd = svc._build_command("/scripts/create.sh", "my_db")
        self.assertIn("-d my_db", cmd)
        self.assertIn("-p 5432", cmd)
        self.assertIn("-l lb.test.local", cmd)
        self.assertIn("/scripts/create.sh", cmd)

    def test_ssh_connection_failure_persists_failure_log(self):
        with patch("paramiko.SSHClient") as MockSSH:
            MockSSH.return_value.connect.side_effect = Exception("timeout")
            svc = ProvisioningService(self.instance, triggered_by=self.user)
            ok, output = svc.create_database("my_db")
        self.assertFalse(ok)
        self.assertIn("timeout", output)
        log = OperationLog.query.first()
        self.assertEqual(log.status, OperationStatus.FAILURE.value)


# ---------------------------------------------------------------------------
# ApprovalWorkflowService unit tests
# ---------------------------------------------------------------------------

class TestApprovalWorkflowService(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app(TestConfig)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.cluster = _make_cluster()
        self.instance = _make_instance(self.cluster)
        self.team = _make_team()
        self.user = _make_user(self.team)
        self.admin = _make_user(self.team, role=UserRole.ADMIN.value)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        safe_drop_all(db)
        self.ctx.pop()

    # --- submit_create ---

    def test_submit_create_direct_executes_immediately(self):
        _grant(self.team, self.cluster, PermissionLevel.DIRECT.value)
        db.session.commit()
        with _mock_ssh_success():
            svc = ApprovalWorkflowService(self.user)
            executed, msg, result = svc.submit_create(self.instance, "my_db")
        self.assertTrue(executed)
        self.assertIsInstance(result, DatabaseAsset)
        self.assertEqual(result.status, DbAssetStatus.ACTIVE.value)
        self.assertEqual(result.provenance, DatabaseProvenance.TEAM.value)

    def test_submit_create_request_creates_pending_request(self):
        _grant(self.team, self.cluster, PermissionLevel.REQUEST.value)
        db.session.commit()
        svc = ApprovalWorkflowService(self.user)
        executed, msg, result = svc.submit_create(self.instance, "my_db")
        self.assertFalse(executed)
        self.assertIsInstance(result, DatabaseRequest)
        self.assertEqual(result.status, RequestStatus.PENDING.value)
        self.assertEqual(result.request_type, RequestType.CREATE.value)

    def test_submit_create_no_permission_raises(self):
        # No grant at all
        svc = ApprovalWorkflowService(self.user)
        with self.assertRaises(WorkflowError) as ctx:
            svc.submit_create(self.instance, "my_db")
        self.assertIn("access", str(ctx.exception).lower())

    def test_submit_create_no_team_raises(self):
        self.user.team_id = None
        db.session.commit()
        svc = ApprovalWorkflowService(self.user)
        with self.assertRaises(WorkflowError):
            svc.submit_create(self.instance, "my_db")

    # --- submit_delete ---

    def test_submit_delete_direct_team_db(self):
        _grant(self.team, self.cluster, PermissionLevel.DIRECT.value)
        asset = DatabaseAsset(
            name="my_db", instance_id=self.instance.id,
            team_id=self.team.id, provenance=DatabaseProvenance.TEAM.value,
            status=DbAssetStatus.ACTIVE.value,
        )
        db.session.add(asset)
        db.session.commit()
        with _mock_ssh_success():
            svc = ApprovalWorkflowService(self.user)
            executed, msg, result = svc.submit_delete(asset)
        self.assertTrue(executed)
        db.session.refresh(asset)
        self.assertEqual(asset.status, DbAssetStatus.DELETED.value)

    def test_submit_delete_dba_db_always_requests(self):
        _grant(self.team, self.cluster, PermissionLevel.DIRECT.value)
        asset = DatabaseAsset(
            name="legacy_db", instance_id=self.instance.id,
            team_id=self.team.id, provenance=DatabaseProvenance.DBA.value,
            status=DbAssetStatus.ACTIVE.value,
        )
        db.session.add(asset)
        db.session.commit()
        svc = ApprovalWorkflowService(self.user)
        executed, msg, result = svc.submit_delete(asset)
        self.assertFalse(executed)
        self.assertIsInstance(result, DatabaseRequest)
        self.assertEqual(result.request_type, RequestType.DELETE.value)

    def test_submit_delete_wrong_team_raises(self):
        other_team = _make_team("other")
        asset = DatabaseAsset(
            name="their_db", instance_id=self.instance.id,
            team_id=other_team.id, provenance=DatabaseProvenance.TEAM.value,
            status=DbAssetStatus.ACTIVE.value,
        )
        db.session.add(asset)
        db.session.commit()
        svc = ApprovalWorkflowService(self.user)
        with self.assertRaises(WorkflowError):
            svc.submit_delete(asset)

    # --- approve ---

    def test_approve_create_request_creates_asset(self):
        _grant(self.team, self.cluster, PermissionLevel.REQUEST.value)
        db.session.commit()
        # Create a pending request
        req = DatabaseRequest(
            request_type=RequestType.CREATE.value,
            status=RequestStatus.PENDING.value,
            database_name="approved_db",
            instance_id=self.instance.id,
            team_id=self.team.id,
            requested_by_id=self.user.id,
        )
        db.session.add(req)
        db.session.commit()
        with _mock_ssh_success():
            svc = ApprovalWorkflowService(self.admin)
            ok, output = svc.approve(req, note="LGTM")
        self.assertTrue(ok)
        db.session.refresh(req)
        self.assertEqual(req.status, RequestStatus.COMPLETED.value)
        self.assertEqual(req.reviewed_by_id, self.admin.id)
        self.assertEqual(req.review_note, "LGTM")
        asset = DatabaseAsset.query.filter_by(name="approved_db").first()
        self.assertIsNotNone(asset)
        self.assertEqual(asset.status, DbAssetStatus.ACTIVE.value)

    def test_approve_delete_request_marks_asset_deleted(self):
        asset = DatabaseAsset(
            name="old_db", instance_id=self.instance.id,
            team_id=self.team.id, provenance=DatabaseProvenance.DBA.value,
            status=DbAssetStatus.DELETING.value,
        )
        db.session.add(asset)
        db.session.flush()
        req = DatabaseRequest(
            request_type=RequestType.DELETE.value,
            status=RequestStatus.PENDING.value,
            database_name="old_db",
            instance_id=self.instance.id,
            team_id=self.team.id,
            database_asset_id=asset.id,
            requested_by_id=self.user.id,
        )
        db.session.add(req)
        db.session.commit()
        with _mock_ssh_success():
            svc = ApprovalWorkflowService(self.admin)
            ok, _ = svc.approve(req)
        self.assertTrue(ok)
        db.session.refresh(asset)
        self.assertEqual(asset.status, DbAssetStatus.DELETED.value)

    def test_approve_non_pending_request_raises(self):
        req = DatabaseRequest(
            request_type=RequestType.CREATE.value,
            status=RequestStatus.REJECTED.value,
            database_name="x", instance_id=self.instance.id,
            team_id=self.team.id, requested_by_id=self.user.id,
        )
        db.session.add(req)
        db.session.commit()
        svc = ApprovalWorkflowService(self.admin)
        with self.assertRaises(WorkflowError):
            svc.approve(req)

    # --- reject ---

    def test_reject_request(self):
        req = DatabaseRequest(
            request_type=RequestType.CREATE.value,
            status=RequestStatus.PENDING.value,
            database_name="wanted_db",
            instance_id=self.instance.id,
            team_id=self.team.id,
            requested_by_id=self.user.id,
        )
        db.session.add(req)
        db.session.commit()
        svc = ApprovalWorkflowService(self.admin)
        svc.reject(req, note="Not allowed in prod.")
        db.session.refresh(req)
        self.assertEqual(req.status, RequestStatus.REJECTED.value)
        self.assertEqual(req.review_note, "Not allowed in prod.")
        self.assertEqual(req.reviewed_by_id, self.admin.id)

    def test_reject_reverts_deleting_asset(self):
        asset = DatabaseAsset(
            name="preserved_db", instance_id=self.instance.id,
            team_id=self.team.id, provenance=DatabaseProvenance.DBA.value,
            status=DbAssetStatus.DELETING.value,
        )
        db.session.add(asset)
        db.session.flush()
        req = DatabaseRequest(
            request_type=RequestType.DELETE.value,
            status=RequestStatus.PENDING.value,
            database_name="preserved_db",
            instance_id=self.instance.id, team_id=self.team.id,
            database_asset_id=asset.id, requested_by_id=self.user.id,
        )
        db.session.add(req)
        db.session.commit()
        ApprovalWorkflowService(self.admin).reject(req, note="No.")
        db.session.refresh(asset)
        self.assertEqual(asset.status, DbAssetStatus.ACTIVE.value)

    # --- register_dba_database ---

    def test_register_dba_database(self):
        svc = ApprovalWorkflowService(self.admin)
        asset = svc.register_dba_database(self.instance, "legacy", self.team.id)
        self.assertEqual(asset.provenance, DatabaseProvenance.DBA.value)
        self.assertEqual(asset.status, DbAssetStatus.ACTIVE.value)
        self.assertIsNone(asset.created_by_id)


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------

class TestDatabaseAdminUI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app(TestConfig)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.cluster = _make_cluster()
        self.instance = _make_instance(self.cluster)
        self.team = _make_team()
        self.admin = _make_user(self.team, role=UserRole.ADMIN.value)
        self.user = _make_user(self.team)
        db.session.commit()
        # Store emails now before any client requests expire the ORM objects
        self.admin_email = self.admin.email
        self.user_email = self.user.email

    def tearDown(self):
        db.session.remove()
        safe_drop_all(db)
        self.ctx.pop()

    def _admin_client(self):
        c = self.app.test_client()
        c.post("/auth/login", data={
            "email": self.admin_email, "password": "Pass123!"
        }, follow_redirects=True)
        return c

    def _user_client(self):
        c = self.app.test_client()
        c.post("/auth/login", data={
            "email": self.user_email, "password": "Pass123!"
        }, follow_redirects=True)
        return c

    def test_database_dashboard_no_team_permission(self):
        resp = self._user_client().get("/databases/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"does not have access to any cluster", resp.data)

    def test_database_dashboard_with_permission(self):
        _grant(self.team, self.cluster)
        db.session.commit()
        resp = self._user_client().get("/databases/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Create database", resp.data)

    def test_create_database_direct(self):
        _grant(self.team, self.cluster, PermissionLevel.DIRECT.value)
        db.session.commit()
        with _mock_ssh_success():
            resp = self._user_client().post("/databases/create", data={
                "instance_id": str(self.instance.id),
                "database_name": "test_db",
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"created successfully", resp.data)
        self.assertEqual(DatabaseAsset.query.filter_by(name="test_db").count(), 1)

    def test_create_database_request_path(self):
        _grant(self.team, self.cluster, PermissionLevel.REQUEST.value)
        db.session.commit()
        resp = self._user_client().post("/databases/create", data={
            "instance_id": str(self.instance.id),
            "database_name": "approval_db",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Awaiting admin approval", resp.data)
        self.assertEqual(
            DatabaseRequest.query.filter_by(database_name="approval_db").count(), 1
        )

    def test_admin_requests_queue(self):
        req = DatabaseRequest(
            request_type=RequestType.CREATE.value, status=RequestStatus.PENDING.value,
            database_name="queued_db", instance_id=self.instance.id,
            team_id=self.team.id, requested_by_id=self.user.id,
        )
        db.session.add(req)
        db.session.commit()
        resp = self._admin_client().get("/admin/requests")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"queued_db", resp.data)

    def test_admin_approve_request(self):
        req = DatabaseRequest(
            request_type=RequestType.CREATE.value, status=RequestStatus.PENDING.value,
            database_name="to_approve", instance_id=self.instance.id,
            team_id=self.team.id, requested_by_id=self.user.id,
        )
        db.session.add(req)
        db.session.commit()
        with _mock_ssh_success():
            resp = self._admin_client().post(
                f"/admin/requests/{req.id}/approve",
                data={"note": "Approved"},
                follow_redirects=True,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"approved and executed", resp.data)
        db.session.refresh(req)
        self.assertEqual(req.status, RequestStatus.COMPLETED.value)

    def test_admin_reject_request(self):
        req = DatabaseRequest(
            request_type=RequestType.CREATE.value, status=RequestStatus.PENDING.value,
            database_name="to_reject", instance_id=self.instance.id,
            team_id=self.team.id, requested_by_id=self.user.id,
        )
        db.session.add(req)
        db.session.commit()
        resp = self._admin_client().post(
            f"/admin/requests/{req.id}/reject",
            data={"note": "Not this time."},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"rejected", resp.data)
        db.session.refresh(req)
        self.assertEqual(req.status, RequestStatus.REJECTED.value)

    def test_admin_databases_inventory(self):
        asset = DatabaseAsset(
            name="visible_db", instance_id=self.instance.id,
            team_id=self.team.id, provenance=DatabaseProvenance.TEAM.value,
            status=DbAssetStatus.ACTIVE.value,
        )
        db.session.add(asset)
        db.session.commit()
        resp = self._admin_client().get("/admin/databases")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"visible_db", resp.data)

    def test_user_cannot_access_admin_requests(self):
        resp = self._user_client().get("/admin/requests")
        self.assertEqual(resp.status_code, 403)

    def test_delete_database_request_path(self):
        _grant(self.team, self.cluster, PermissionLevel.REQUEST.value)
        asset = DatabaseAsset(
            name="to_delete", instance_id=self.instance.id,
            team_id=self.team.id, provenance=DatabaseProvenance.TEAM.value,
            status=DbAssetStatus.ACTIVE.value,
        )
        db.session.add(asset)
        db.session.commit()
        resp = self._user_client().post(
            f"/databases/{asset.id}/delete", follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"approval", resp.data.lower())
        db.session.refresh(asset)
        self.assertEqual(asset.status, DbAssetStatus.DELETING.value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
