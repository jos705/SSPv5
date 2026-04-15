"""
Phase 2 tests — Cluster registration UI and SSH discovery service.

SSH connectivity is fully mocked: no real servers are required.
The discovery service's SSH layer (paramiko.SSHClient) is patched to
return configurable remote file contents so every code path can be
exercised deterministically.
"""

from __future__ import annotations

import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.extensions import db
from app.models import Cluster, ClusterStatus, Node, PgInstance, Team, User, UserRole
from app.services.cluster_discovery import ClusterDiscoveryService, DiscoveryError


# ---------------------------------------------------------------------------
# Shared test config
# ---------------------------------------------------------------------------

class TestConfig:
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    WTF_CSRF_ENABLED = False


# ---------------------------------------------------------------------------
# SSH mock helpers
# ---------------------------------------------------------------------------

def _make_sftp_mock(files: dict[str, str]) -> MagicMock:
    """Return a mock SFTP object whose open() yields the provided file contents."""
    sftp = MagicMock()

    def _open(path: str, mode: str = "r"):
        if path not in files:
            raise IOError(f"No such file: {path}")
        fh = MagicMock()
        fh.read.return_value = files[path].encode("utf-8")
        fh.__enter__ = lambda s: s
        fh.__exit__ = MagicMock(return_value=False)
        return fh

    sftp.open.side_effect = _open
    return sftp


def _make_ssh_mock(files: dict[str, str]) -> MagicMock:
    """Return a mock SSHClient backed by the given file map."""
    client = MagicMock()
    client.open_sftp.return_value = _make_sftp_mock(files)
    return client


POSTGRES_INFO = "/home/postgres/postgres.info"
POSTGRESTAB = "/home/postgres/beheer/scripts/postgrestab"


# ---------------------------------------------------------------------------
# Discovery service unit tests (no HTTP, no real SSH)
# ---------------------------------------------------------------------------

class TestClusterDiscoveryService(unittest.TestCase):
    """Test ClusterDiscoveryService with mocked paramiko."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app(TestConfig)

    def setUp(self) -> None:
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.cluster = Cluster(
            name="dev-cluster",
            load_balancer="lb.example.com",
            ssh_user="postgres",
            status=ClusterStatus.PENDING.value,
        )
        db.session.add(self.cluster)
        db.session.flush()

        db.session.add(Node(cluster_id=self.cluster.id, hostname="node1.example.com"))
        db.session.add(Node(cluster_id=self.cluster.id, hostname="node2.example.com"))
        db.session.commit()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # --- helpers ---

    def _run_with_files(self, files: dict[str, str]) -> tuple[bool, str]:
        """Patch SSHClient and run discovery, returning (success, message)."""
        mock_client = _make_ssh_mock(files)
        with patch("paramiko.SSHClient", return_value=mock_client):
            svc = ClusterDiscoveryService(self.cluster)
            return svc.run()

    # --- tests ---

    def test_happy_path_two_nodes(self) -> None:
        """Both nodes reachable, names match, two instances discovered."""
        files = {
            POSTGRES_INFO: "clustername=dev-cluster\n",
            POSTGRESTAB: (
                "# header\n"
                "node1.example.com:5432:main:/var/lib/pgsql/main\n"
                "node2.example.com:5433:replica:/var/lib/pgsql/replica\n"
            ),
        }
        ok, msg = self._run_with_files(files)

        self.assertTrue(ok, msg)
        self.assertIn("2 instance", msg)

        db.session.refresh(self.cluster)
        self.assertEqual(self.cluster.status, ClusterStatus.ACTIVE.value)
        self.assertIsNone(self.cluster.sync_error)
        self.assertIsNotNone(self.cluster.last_synced_at)

        instances = PgInstance.query.filter_by(cluster_id=self.cluster.id).all()
        self.assertEqual(len(instances), 2)
        ports = {i.port for i in instances}
        self.assertEqual(ports, {5432, 5433})

    def test_cluster_name_mismatch_warns_but_succeeds(self) -> None:
        """Nodes report a different cluster name — warning stored, status still active."""
        files = {
            POSTGRES_INFO: "clustername=different-name\n",
            POSTGRESTAB: "node1.example.com:5432:main:/data\n",
        }
        ok, msg = self._run_with_files(files)

        self.assertTrue(ok, msg)
        db.session.refresh(self.cluster)
        self.assertEqual(self.cluster.status, ClusterStatus.ACTIVE.value)
        self.assertIsNotNone(self.cluster.sync_error)
        self.assertIn("mismatch", self.cluster.sync_error)

    def test_ssh_connection_failure_marks_error(self) -> None:
        """If SSH fails on all nodes, status becomes error."""
        with patch("paramiko.SSHClient") as MockSSH:
            MockSSH.return_value.connect.side_effect = Exception("Connection refused")
            svc = ClusterDiscoveryService(self.cluster)
            ok, msg = svc.run()

        self.assertFalse(ok)
        self.assertIn("Connection refused", msg)
        db.session.refresh(self.cluster)
        self.assertEqual(self.cluster.status, ClusterStatus.ERROR.value)

    def test_inconsistent_cluster_names_across_nodes(self) -> None:
        """Nodes that disagree on cluster name cause a fatal error."""
        call_count = 0

        def varying_info(path: str, mode: str = "r"):
            nonlocal call_count
            if path == POSTGRES_INFO:
                name = "alpha" if call_count == 0 else "beta"
                call_count += 1
                fh = MagicMock()
                fh.read.return_value = f"clustername={name}\n".encode()
                fh.__enter__ = lambda s: s
                fh.__exit__ = MagicMock(return_value=False)
                return fh
            raise IOError(f"Unexpected path: {path}")

        mock_client = MagicMock()
        mock_sftp = MagicMock()
        mock_sftp.open.side_effect = varying_info
        mock_client.open_sftp.return_value = mock_sftp

        with patch("paramiko.SSHClient", return_value=mock_client):
            svc = ClusterDiscoveryService(self.cluster)
            ok, msg = svc.run()

        self.assertFalse(ok)
        self.assertIn("Inconsistent", msg)

    def test_malformed_postgrestab_marks_error(self) -> None:
        """A line with the wrong number of fields causes a parse error."""
        files = {
            POSTGRES_INFO: "clustername=dev-cluster\n",
            POSTGRESTAB: "node1.example.com:5432:only_three_fields\n",
        }
        ok, msg = self._run_with_files(files)

        self.assertFalse(ok)
        self.assertIn("Malformed", msg)
        db.session.refresh(self.cluster)
        self.assertEqual(self.cluster.status, ClusterStatus.ERROR.value)

    def test_invalid_port_in_postgrestab(self) -> None:
        """A non-integer port value causes a parse error."""
        files = {
            POSTGRES_INFO: "clustername=dev-cluster\n",
            POSTGRESTAB: "node1.example.com:notaport:main:/data\n",
        }
        ok, msg = self._run_with_files(files)

        self.assertFalse(ok)
        self.assertIn("Invalid port", msg)

    def test_no_nodes_registered(self) -> None:
        """A cluster with no nodes returns an error immediately."""
        Node.query.filter_by(cluster_id=self.cluster.id).delete()
        db.session.commit()

        svc = ClusterDiscoveryService(self.cluster)
        ok, msg = svc.run()

        self.assertFalse(ok)
        self.assertIn("No nodes", msg)
        db.session.refresh(self.cluster)
        self.assertEqual(self.cluster.status, ClusterStatus.ERROR.value)

    def test_resync_replaces_old_instances(self) -> None:
        """Running sync twice replaces stale instance records cleanly."""
        files_v1 = {
            POSTGRES_INFO: "clustername=dev-cluster\n",
            POSTGRESTAB: "node1.example.com:5432:main:/data\n",
        }
        files_v2 = {
            POSTGRES_INFO: "clustername=dev-cluster\n",
            POSTGRESTAB: (
                "node1.example.com:5432:main:/data\n"
                "node1.example.com:5433:secondary:/data2\n"
            ),
        }

        self._run_with_files(files_v1)
        self.assertEqual(
            PgInstance.query.filter_by(cluster_id=self.cluster.id).count(), 1
        )

        self._run_with_files(files_v2)
        self.assertEqual(
            PgInstance.query.filter_by(cluster_id=self.cluster.id).count(), 2
        )

    def test_missing_postgres_info_file(self) -> None:
        """If postgres.info is absent on all nodes, sync fails with an error."""
        files = {
            # postgres.info is absent — only postgrestab is present
            POSTGRESTAB: "node1.example.com:5432:main:/data\n",
        }
        ok, msg = self._run_with_files(files)

        self.assertFalse(ok)
        db.session.refresh(self.cluster)
        self.assertEqual(self.cluster.status, ClusterStatus.ERROR.value)


# ---------------------------------------------------------------------------
# Cluster admin UI integration tests (HTTP, mocked SSH)
# ---------------------------------------------------------------------------

class TestClusterAdminUI(unittest.TestCase):
    """Test cluster CRUD and sync flow through the HTTP interface."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app(TestConfig)

    def setUp(self) -> None:
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        admin = User(
            email="admin@example.com",
            username="admin",
            role=UserRole.ADMIN.value,
        )
        admin.set_password("AdminPass123!")
        db.session.add(admin)
        db.session.commit()

        self.client = self.app.test_client()
        self.client.post(
            "/auth/login",
            data={"email": "admin@example.com", "password": "AdminPass123!"},
            follow_redirects=True,
        )

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_cluster_list_page_loads(self) -> None:
        resp = self.client.get("/admin/clusters")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"No clusters registered yet", resp.data)

    def test_register_cluster_creates_record_and_nodes(self) -> None:
        resp = self.client.post(
            "/admin/clusters/new",
            data={
                "name": "prod-pg",
                "load_balancer": "lb.prod.example.com",
                "description": "Production cluster",
                "ssh_user": "postgres",
                "ssh_key_path": "",
                "node1": "pg1.prod.example.com",
                "node2": "pg2.prod.example.com",
                "node3": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"registered", resp.data)

        cluster = Cluster.query.filter_by(name="prod-pg").first()
        self.assertIsNotNone(cluster)
        self.assertEqual(cluster.status, ClusterStatus.PENDING.value)
        self.assertEqual(len(cluster.nodes), 2)

    def test_cluster_detail_page_loads(self) -> None:
        cluster = Cluster(name="test-cl", load_balancer="lb", ssh_user="postgres")
        db.session.add(cluster)
        db.session.flush()
        db.session.add(Node(cluster_id=cluster.id, hostname="node1"))
        db.session.commit()

        resp = self.client.get(f"/admin/clusters/{cluster.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"test-cl", resp.data)
        self.assertIn(b"node1", resp.data)
        self.assertIn(b"Sync cluster", resp.data)

    def test_edit_cluster_updates_record(self) -> None:
        cluster = Cluster(name="old-name", load_balancer="lb", ssh_user="postgres")
        db.session.add(cluster)
        db.session.flush()
        db.session.add(Node(cluster_id=cluster.id, hostname="node1"))
        db.session.commit()

        resp = self.client.post(
            f"/admin/clusters/{cluster.id}/edit",
            data={
                "name": "new-name",
                "load_balancer": "new-lb",
                "description": "",
                "ssh_user": "postgres",
                "ssh_key_path": "",
                "node1": "node1",
                "node2": "",
                "node3": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"updated", resp.data)
        db.session.refresh(cluster)
        self.assertEqual(cluster.name, "new-name")

    def test_sync_cluster_success_via_http(self) -> None:
        """POST /clusters/<id>/sync with mocked SSH returns success flash."""
        cluster = Cluster(name="dev-cl", load_balancer="lb", ssh_user="postgres")
        db.session.add(cluster)
        db.session.flush()
        db.session.add(Node(cluster_id=cluster.id, hostname="node1"))
        db.session.commit()

        files = {
            POSTGRES_INFO: "clustername=dev-cl\n",
            POSTGRESTAB: "node1:5432:main:/var/lib/pgsql\n",
        }
        mock_client = _make_ssh_mock(files)
        with patch("paramiko.SSHClient", return_value=mock_client):
            resp = self.client.post(
                f"/admin/clusters/{cluster.id}/sync",
                follow_redirects=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Sync complete", resp.data)

        db.session.refresh(cluster)
        self.assertEqual(cluster.status, ClusterStatus.ACTIVE.value)
        self.assertEqual(
            PgInstance.query.filter_by(cluster_id=cluster.id).count(), 1
        )

    def test_sync_cluster_failure_shows_error_flash(self) -> None:
        """POST /clusters/<id>/sync with failing SSH shows error flash."""
        cluster = Cluster(name="broken-cl", load_balancer="lb", ssh_user="postgres")
        db.session.add(cluster)
        db.session.flush()
        db.session.add(Node(cluster_id=cluster.id, hostname="unreachable"))
        db.session.commit()

        with patch("paramiko.SSHClient") as MockSSH:
            MockSSH.return_value.connect.side_effect = Exception("Connection timed out")
            resp = self.client.post(
                f"/admin/clusters/{cluster.id}/sync",
                follow_redirects=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Sync failed", resp.data)

        db.session.refresh(cluster)
        self.assertEqual(cluster.status, ClusterStatus.ERROR.value)

    def test_delete_cluster_removes_record(self) -> None:
        cluster = Cluster(name="to-delete", load_balancer="lb", ssh_user="postgres")
        db.session.add(cluster)
        db.session.commit()
        cid = cluster.id

        resp = self.client.post(
            f"/admin/clusters/{cid}/delete",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"deleted", resp.data)
        self.assertIsNone(Cluster.query.get(cid))

    def test_grant_and_revoke_team_permission(self) -> None:
        cluster = Cluster(name="perm-cl", load_balancer="lb", ssh_user="postgres")
        team = Team(name="ops-team")
        db.session.add_all([cluster, team])
        db.session.commit()

        # Grant
        resp = self.client.post(
            f"/admin/clusters/{cluster.id}/permissions/grant",
            data={"team_id": str(team.id), "permission_level": "direct"},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Access granted", resp.data)

        db.session.refresh(cluster)
        self.assertEqual(len(cluster.team_permissions), 1)
        perm_id = cluster.team_permissions[0].id

        # Revoke
        resp = self.client.post(
            f"/admin/clusters/{cluster.id}/permissions/{perm_id}/revoke",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"revoked", resp.data)
        db.session.refresh(cluster)
        self.assertEqual(len(cluster.team_permissions), 0)

    def test_duplicate_cluster_name_rejected(self) -> None:
        db.session.add(Cluster(name="existing", load_balancer="lb", ssh_user="postgres"))
        db.session.commit()

        resp = self.client.post(
            "/admin/clusters/new",
            data={
                "name": "existing",
                "load_balancer": "lb2",
                "description": "",
                "ssh_user": "postgres",
                "ssh_key_path": "",
                "node1": "node1",
                "node2": "",
                "node3": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"already exists", resp.data)
        self.assertEqual(Cluster.query.filter_by(name="existing").count(), 1)


# ---------------------------------------------------------------------------
# Parser unit tests (pure Python, no DB, no SSH)
# ---------------------------------------------------------------------------

class TestParsers(unittest.TestCase):
    """Direct unit tests of the static parser methods."""

    def test_parse_postgres_info_basic(self) -> None:
        result = ClusterDiscoveryService._parse_postgres_info(
            "clustername=prod-pg-01\n", "host"
        )
        self.assertEqual(result, "prod-pg-01")

    def test_parse_postgres_info_with_spaces_and_comments(self) -> None:
        content = "# generated by DBA tooling\nclustername = my cluster\nother=value\n"
        self.assertEqual(
            ClusterDiscoveryService._parse_postgres_info(content, "host"),
            "my cluster",
        )

    def test_parse_postgres_info_case_insensitive_key(self) -> None:
        self.assertEqual(
            ClusterDiscoveryService._parse_postgres_info("CLUSTERNAME=UPPER\n", "h"),
            "UPPER",
        )

    def test_parse_postgres_info_missing_key_raises(self) -> None:
        with self.assertRaises(DiscoveryError):
            ClusterDiscoveryService._parse_postgres_info("foo=bar\n", "host")

    def test_parse_postgrestab_typical(self) -> None:
        content = (
            "# comment\n"
            "pg-node1:5432:main:/var/lib/pgsql/data\n"
            "pg-node2:5433:replica:/var/lib/pgsql/data2\n"
            "\n"
        )
        instances = ClusterDiscoveryService._parse_postgrestab(content, "host")
        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[0]["hostname"], "pg-node1")
        self.assertEqual(instances[0]["port"], 5432)
        self.assertEqual(instances[0]["instance_name"], "main")
        self.assertEqual(instances[0]["pgdata_dir"], "/var/lib/pgsql/data")
        self.assertEqual(instances[1]["port"], 5433)

    def test_parse_postgrestab_empty_returns_empty_list(self) -> None:
        self.assertEqual(
            ClusterDiscoveryService._parse_postgrestab("# only comments\n", "h"), []
        )

    def test_parse_postgrestab_bad_column_count(self) -> None:
        with self.assertRaises(DiscoveryError) as ctx:
            ClusterDiscoveryService._parse_postgrestab("host:5432:name\n", "h")
        self.assertIn("Malformed", str(ctx.exception))

    def test_parse_postgrestab_non_integer_port(self) -> None:
        with self.assertRaises(DiscoveryError) as ctx:
            ClusterDiscoveryService._parse_postgrestab("host:BADPORT:name:/data\n", "h")
        self.assertIn("Invalid port", str(ctx.exception))

    def test_parse_postgrestab_port_out_of_range(self) -> None:
        with self.assertRaises(DiscoveryError) as ctx:
            ClusterDiscoveryService._parse_postgrestab("host:99999:name:/data\n", "h")
        self.assertIn("out of range", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
