from __future__ import annotations

from flask import redirect, render_template, url_for
from flask_login import current_user, login_required

from . import bp
from ..models import DatabaseAsset, DatabaseRequest, DbAssetStatus, RequestStatus


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    team_id = current_user.team_id
    db_count = pending_count = 0
    recent_dbs = []

    if team_id:
        db_count = DatabaseAsset.query.filter_by(
            team_id=team_id, status=DbAssetStatus.ACTIVE.value
        ).count()
        pending_count = DatabaseRequest.query.filter_by(
            team_id=team_id, status=RequestStatus.PENDING.value
        ).count()
        recent_dbs = (
            DatabaseAsset.query
            .filter(
                DatabaseAsset.team_id == team_id,
                DatabaseAsset.status != DbAssetStatus.DELETED.value,
            )
            .order_by(DatabaseAsset.created_at.desc())
            .limit(5)
            .all()
        )

    return render_template(
        "main/dashboard.html",
        db_count=db_count,
        pending_count=pending_count,
        recent_dbs=recent_dbs,
    )

