from __future__ import annotations

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_

from . import bp
from .forms import CreateDatabaseForm
from ..extensions import db
from ..models import (
    DatabaseAsset,
    DatabaseRequest,
    DbAssetStatus,
    PgInstance,
    RequestStatus,
    TeamClusterPermission,
)
from ..services.approval_workflow import ApprovalWorkflowService, WorkflowError


def _accessible_instances() -> list[PgInstance]:
    """Return instances the current user's team may use (has any permission)."""
    if not current_user.team_id:
        return []
    perms = TeamClusterPermission.query.filter_by(team_id=current_user.team_id).all()
    cluster_ids = [p.cluster_id for p in perms]
    if not cluster_ids:
        return []
    return (
        PgInstance.query
        .filter(PgInstance.cluster_id.in_(cluster_ids))
        .order_by(PgInstance.instance_name.asc())
        .all()
    )


@bp.route("/")
@login_required
def dashboard():
    if not current_user.team_id:
        flash("You must belong to a team to use the database portal.", "warning")
        return redirect(url_for("main.dashboard"))

    assets = (
        DatabaseAsset.query
        .filter(
            DatabaseAsset.team_id == current_user.team_id,
            DatabaseAsset.status != DbAssetStatus.DELETED.value,
        )
        .order_by(DatabaseAsset.created_at.desc())
        .all()
    )
    pending_requests = (
        DatabaseRequest.query
        .filter_by(team_id=current_user.team_id, status=RequestStatus.PENDING.value)
        .order_by(DatabaseRequest.requested_at.desc())
        .all()
    )
    instances = _accessible_instances()
    form = CreateDatabaseForm()
    form.set_instance_choices(instances)

    return render_template(
        "databases/dashboard.html",
        assets=assets,
        pending_requests=pending_requests,
        form=form,
        has_instances=bool(instances),
    )


@bp.route("/create", methods=["POST"])
@login_required
def create_database():
    instances = _accessible_instances()
    form = CreateDatabaseForm()
    form.set_instance_choices(instances)

    if not form.validate_on_submit():
        for field_errors in form.errors.values():
            for err in field_errors:
                flash(err, "danger")
        return redirect(url_for("databases.dashboard"))

    instance = db.session.get(PgInstance, form.instance_id.data)
    if not instance:
        abort(404)

    svc = ApprovalWorkflowService(current_user)
    try:
        executed, message, _ = svc.submit_create(
            instance,
            form.database_name.data.strip(),
            reason=(form.reason.data or "").strip() or None,
        )
    except WorkflowError as e:
        flash(str(e), "danger")
        return redirect(url_for("databases.dashboard"))

    flash(message, "success" if executed else "info")
    return redirect(url_for("databases.dashboard"))


@bp.route("/<int:asset_id>/delete", methods=["POST"])
@login_required
def delete_database(asset_id: int):
    asset = db.session.get(DatabaseAsset, asset_id)
    if not asset or asset.team_id != current_user.team_id:
        abort(404)

    if asset.status in (DbAssetStatus.DELETED.value, DbAssetStatus.DELETING.value):
        flash("This database is already being deleted or has been deleted.", "warning")
        return redirect(url_for("databases.dashboard"))

    svc = ApprovalWorkflowService(current_user)
    try:
        executed, message, _ = svc.submit_delete(asset)
    except WorkflowError as e:
        flash(str(e), "danger")
        return redirect(url_for("databases.dashboard"))

    flash(message, "success" if executed else "info")
    return redirect(url_for("databases.dashboard"))


@bp.route("/requests")
@login_required
def my_requests():
    if not current_user.team_id:
        abort(403)
    requests = (
        DatabaseRequest.query
        .filter_by(team_id=current_user.team_id)
        .order_by(DatabaseRequest.requested_at.desc())
        .all()
    )
    return render_template("databases/requests.html", requests=requests)
