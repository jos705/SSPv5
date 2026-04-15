"""
ApprovalWorkflowService
========================
Enforces the permission matrix for database create/delete and drives the
DatabaseRequest state machine.

Permission matrix
-----------------
+------------------------+----------------+------------------------------+
| Scenario               | Permission     | Outcome                      |
+========================+================+==============================+
| Team creates DB        | direct         | Execute immediately          |
| Team creates DB        | request        | Pending approval request     |
| Team deletes team DB   | direct         | Execute immediately          |
| Team deletes team DB   | request        | Pending approval request     |
| Team deletes DBA DB    | any            | Always pending approval      |
+------------------------+----------------+------------------------------+
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..extensions import db
from ..models import (
    DatabaseAsset,
    DatabaseProvenance,
    DatabaseRequest,
    DbAssetStatus,
    PermissionLevel,
    PgInstance,
    RequestStatus,
    RequestType,
    TeamClusterPermission,
)
from .provisioning import ProvisioningService

if TYPE_CHECKING:
    from ..models import User

log = logging.getLogger(__name__)


class WorkflowError(Exception):
    """Raised when a workflow action is not permitted."""


class ApprovalWorkflowService:
    """Manage database request creation, approval, and rejection."""

    def __init__(self, acting_user: User) -> None:
        self.acting_user = acting_user

    # ------------------------------------------------------------------
    # Submit actions (called by users)
    # ------------------------------------------------------------------

    def submit_create(
        self,
        instance: PgInstance,
        database_name: str,
        reason: str | None = None,
    ) -> tuple[bool, str, DatabaseRequest | DatabaseAsset]:
        """
        Submit a request to create *database_name* on *instance*.

        Returns ``(executed_immediately, message, result_object)`` where
        *result_object* is the new ``DatabaseAsset`` (direct) or
        ``DatabaseRequest`` (approval needed).
        """
        team = self._require_team()
        perm = self._require_permission(instance)

        if perm.permission_level == PermissionLevel.DIRECT.value:
            asset = DatabaseAsset(
                name=database_name,
                instance_id=instance.id,
                team_id=team.id,
                created_by_id=self.acting_user.id,
                provenance=DatabaseProvenance.TEAM.value,
                status=DbAssetStatus.ACTIVE.value,
            )
            db.session.add(asset)
            db.session.flush()

            svc = ProvisioningService(instance, triggered_by=self.acting_user)
            ok, output = svc.create_database(database_name, asset=asset)

            if not ok:
                asset.status = DbAssetStatus.FAILED.value
                db.session.commit()
                return False, f"Provisioning failed: {output}", asset

            db.session.commit()
            log.info("Database '%s' created directly by %s", database_name, self.acting_user.email)
            return True, f"Database '{database_name}' created successfully.", asset

        # Request path
        req = DatabaseRequest(
            request_type=RequestType.CREATE.value,
            status=RequestStatus.PENDING.value,
            database_name=database_name,
            instance_id=instance.id,
            team_id=team.id,
            requested_by_id=self.acting_user.id,
            reason=reason or None,
        )
        db.session.add(req)
        db.session.commit()
        log.info(
            "Create request #%d submitted by %s for '%s'",
            req.id, self.acting_user.email, database_name
        )
        return False, f"Request submitted. Awaiting admin approval.", req

    def submit_delete(
        self,
        asset: DatabaseAsset,
        reason: str | None = None,
    ) -> tuple[bool, str, DatabaseRequest | DatabaseAsset]:
        """
        Submit a request to delete *asset*.

        DBA-provenance databases always require approval regardless of
        the team's permission level.
        """
        team = self._require_team()

        if asset.team_id != team.id:
            raise WorkflowError("You do not have permission to delete this database.")

        perm = self._require_permission(asset.instance)

        # DBA-created databases always require approval
        requires_approval = (
            asset.provenance == DatabaseProvenance.DBA.value
            or perm.permission_level == PermissionLevel.REQUEST.value
        )

        if not requires_approval:
            # Direct execution
            svc = ProvisioningService(asset.instance, triggered_by=self.acting_user)
            ok, output = svc.delete_database(asset.name, asset=asset)

            if ok:
                asset.status = DbAssetStatus.DELETED.value
                db.session.commit()
                log.info("Database '%s' deleted directly by %s", asset.name, self.acting_user.email)
                return True, f"Database '{asset.name}' deleted.", asset
            else:
                asset.status = DbAssetStatus.FAILED.value
                db.session.commit()
                return False, f"Deletion failed: {output}", asset

        # Request path
        asset.status = DbAssetStatus.DELETING.value
        req = DatabaseRequest(
            request_type=RequestType.DELETE.value,
            status=RequestStatus.PENDING.value,
            database_name=asset.name,
            instance_id=asset.instance_id,
            team_id=team.id,
            database_asset_id=asset.id,
            requested_by_id=self.acting_user.id,
            reason=reason or None,
        )
        db.session.add(req)
        db.session.commit()
        log.info(
            "Delete request #%d submitted by %s for '%s'",
            req.id, self.acting_user.email, asset.name
        )
        return False, "Deletion request submitted. Awaiting admin approval.", req

    # ------------------------------------------------------------------
    # Admin actions
    # ------------------------------------------------------------------

    def approve(self, request: DatabaseRequest, note: str = "") -> tuple[bool, str]:
        """
        Approve *request*, execute the provisioning action, and update
        the request status to completed or failed.
        """
        if request.status != RequestStatus.PENDING.value:
            raise WorkflowError(
                f"Request #{request.id} is not pending (status: {request.status})."
            )

        request.status = RequestStatus.EXECUTING.value
        request.reviewed_by_id = self.acting_user.id
        request.review_note = note or None
        request.reviewed_at = datetime.now(timezone.utc)
        db.session.flush()

        instance = request.instance
        svc = ProvisioningService(instance, triggered_by=self.acting_user)

        if request.request_type == RequestType.CREATE.value:
            ok, output = svc.create_database(
                request.database_name, request=request
            )
            if ok:
                asset = DatabaseAsset(
                    name=request.database_name,
                    instance_id=instance.id,
                    team_id=request.team_id,
                    provenance=DatabaseProvenance.TEAM.value,
                    status=DbAssetStatus.ACTIVE.value,
                )
                db.session.add(asset)
                db.session.flush()
                request.database_asset_id = asset.id
        else:
            asset = request.database_asset
            ok, output = svc.delete_database(
                request.database_name, asset=asset, request=request
            )
            if ok and asset:
                asset.status = DbAssetStatus.DELETED.value

        request.operation_output = output
        request.status = RequestStatus.COMPLETED.value if ok else RequestStatus.FAILED.value
        request.completed_at = datetime.now(timezone.utc)
        db.session.commit()

        result = "completed" if ok else "failed"
        log.info(
            "Request #%d %s by %s", request.id, result, self.acting_user.email
        )
        return ok, output

    def reject(self, request: DatabaseRequest, note: str) -> None:
        """Reject *request* with an admin note."""
        if request.status != RequestStatus.PENDING.value:
            raise WorkflowError(
                f"Request #{request.id} is not pending (status: {request.status})."
            )

        # If it was a delete request, revert the asset status
        if request.request_type == RequestType.DELETE.value and request.database_asset:
            request.database_asset.status = DbAssetStatus.ACTIVE.value

        request.status = RequestStatus.REJECTED.value
        request.reviewed_by_id = self.acting_user.id
        request.review_note = note
        request.reviewed_at = datetime.now(timezone.utc)
        db.session.commit()
        log.info("Request #%d rejected by %s", request.id, self.acting_user.email)

    # ------------------------------------------------------------------
    # Admin: register a DBA-created database
    # ------------------------------------------------------------------

    def register_dba_database(
        self,
        instance: PgInstance,
        database_name: str,
        team_id: int,
    ) -> DatabaseAsset:
        """
        Register a database that was created externally by a DBA.
        Deletion will always require admin approval.
        """
        asset = DatabaseAsset(
            name=database_name,
            instance_id=instance.id,
            team_id=team_id,
            created_by_id=None,
            provenance=DatabaseProvenance.DBA.value,
            status=DbAssetStatus.ACTIVE.value,
        )
        db.session.add(asset)
        db.session.commit()
        log.info(
            "DBA database '%s' registered on instance %s:%d for team %d",
            database_name, instance.hostname, instance.port, team_id
        )
        return asset

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_team(self):
        team = self.acting_user.team
        if not team:
            raise WorkflowError("You must belong to a team to manage databases.")
        return team

    def _require_permission(self, instance: PgInstance) -> TeamClusterPermission:
        team = self._require_team()
        perm = TeamClusterPermission.query.filter_by(
            team_id=team.id,
            cluster_id=instance.cluster_id,
        ).first()
        if not perm:
            raise WorkflowError(
                f"Your team does not have access to cluster "
                f"'{instance.cluster.name}'."
            )
        return perm
