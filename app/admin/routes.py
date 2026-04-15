from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from . import bp
from .forms import ClusterForm, TeamClusterPermissionForm, TeamForm, UserCreateForm, UserEditForm
from ..decorators import admin_required
from ..extensions import db
from ..models import Cluster, ClusterStatus, Node, TeamClusterPermission, Team, User, UserRole


@bp.route("/")
@admin_required
def dashboard():
    return render_template(
        "admin/dashboard.html",
        user_count=User.query.count(),
        team_count=Team.query.count(),
        admin_count=User.query.filter_by(role=UserRole.ADMIN.value).count(),
        cluster_count=Cluster.query.count(),
    )


@bp.route("/teams")
@admin_required
def teams():
    all_teams = Team.query.order_by(Team.name.asc()).all()
    return render_template("admin/teams.html", teams=all_teams)


@bp.route("/teams/new", methods=["GET", "POST"])
@admin_required
def create_team():
    form = TeamForm()
    if form.validate_on_submit():
        team_name = form.name.data.strip()
        existing_team = Team.query.filter(func.lower(Team.name) == team_name.lower()).first()
        if existing_team:
            flash("A team with this name already exists.", "danger")
            return render_template("admin/team_form.html", form=form, title="Create team")

        team = Team(name=team_name, description=form.description.data.strip() if form.description.data else None)
        db.session.add(team)
        db.session.commit()
        flash("Team created successfully.", "success")
        return redirect(url_for("admin.teams"))

    return render_template("admin/team_form.html", form=form, title="Create team")


@bp.route("/teams/<int:team_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_team(team_id: int):
    team = Team.query.get_or_404(team_id)
    form = TeamForm(obj=team)

    if form.validate_on_submit():
        team_name = form.name.data.strip()
        existing_team = (
            Team.query.filter(Team.id != team.id)
            .filter(func.lower(Team.name) == team_name.lower())
            .first()
        )
        if existing_team:
            flash("A team with this name already exists.", "danger")
            return render_template("admin/team_form.html", form=form, title=f"Edit {team.name}")

        team.name = team_name
        team.description = form.description.data.strip() if form.description.data else None
        db.session.commit()
        flash("Team updated successfully.", "success")
        return redirect(url_for("admin.teams"))

    return render_template("admin/team_form.html", form=form, title=f"Edit {team.name}")


@bp.route("/teams/<int:team_id>/delete", methods=["POST"])
@admin_required
def delete_team(team_id: int):
    team = Team.query.get_or_404(team_id)

    if team.users:
        flash("Cannot delete a team that still has users assigned.", "warning")
        return redirect(url_for("admin.teams"))

    db.session.delete(team)
    db.session.commit()
    flash("Team deleted.", "success")
    return redirect(url_for("admin.teams"))


@bp.route("/users")
@admin_required
def users():
    all_users = User.query.order_by(User.role.asc(), User.username.asc()).all()
    return render_template("admin/users.html", users=all_users)


@bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def create_user():
    form = UserCreateForm()
    teams = Team.query.order_by(Team.name.asc()).all()
    form.set_team_choices(teams)

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        username = form.username.data.strip()
        duplicate_user = User.query.filter(
            or_(func.lower(User.email) == email, func.lower(User.username) == username.lower())
        ).first()
        if duplicate_user:
            flash("A user with this email or username already exists.", "danger")
            return render_template("admin/user_form.html", form=form, title="Create user")

        assigned_team_id = None if form.team_id.data == 0 else form.team_id.data
        user = User(
            email=email,
            username=username,
            role=form.role.data,
            team_id=assigned_team_id,
        )
        user.set_password(form.password.data)

        db.session.add(user)
        db.session.commit()
        flash("User created successfully.", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/user_form.html", form=form, title="Create user")


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id: int):
    user = User.query.get_or_404(user_id)
    form = UserEditForm()
    teams = Team.query.order_by(Team.name.asc()).all()
    form.set_team_choices(teams)

    if request.method == "GET":
        form.email.data = user.email
        form.username.data = user.username
        form.role.data = user.role
        form.team_id.data = user.team_id or 0

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        username = form.username.data.strip()

        duplicate_user = (
            User.query.filter(User.id != user.id)
            .filter(or_(func.lower(User.email) == email, func.lower(User.username) == username.lower()))
            .first()
        )
        if duplicate_user:
            flash("A user with this email or username already exists.", "danger")
            return render_template("admin/user_form.html", form=form, title=f"Edit {user.username}")

        if user.id == current_user.id and form.role.data != UserRole.ADMIN.value:
            flash("You cannot remove your own admin role.", "warning")
            return render_template("admin/user_form.html", form=form, title=f"Edit {user.username}")

        user.email = email
        user.username = username
        user.role = form.role.data
        user.team_id = None if form.team_id.data == 0 else form.team_id.data

        if form.password.data:
            user.set_password(form.password.data)

        db.session.commit()
        flash("User updated successfully.", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/user_form.html", form=form, title=f"Edit {user.username}")


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account.", "warning")
        return redirect(url_for("admin.users"))

    db.session.delete(user)
    db.session.commit()
    flash("User deleted.", "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------------------
# Cluster management
# ---------------------------------------------------------------------------


@bp.route("/clusters")
@admin_required
def clusters():
    all_clusters = Cluster.query.order_by(Cluster.name.asc()).all()
    return render_template("admin/clusters.html", clusters=all_clusters)


@bp.route("/clusters/new", methods=["GET", "POST"])
@admin_required
def create_cluster():
    form = ClusterForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if Cluster.query.filter(func.lower(Cluster.name) == name.lower()).first():
            flash("A cluster with this name already exists.", "danger")
            return render_template("admin/cluster_form.html", form=form, title="Register cluster")

        cluster = Cluster(
            name=name,
            load_balancer=form.load_balancer.data.strip(),
            description=form.description.data.strip() if form.description.data else None,
            ssh_user=form.ssh_user.data.strip(),
            ssh_key_path=form.ssh_key_path.data.strip() or None,
            status=ClusterStatus.PENDING.value,
        )
        db.session.add(cluster)
        db.session.flush()  # get cluster.id before adding nodes

        for hostname in form.node_hostnames():
            db.session.add(Node(cluster_id=cluster.id, hostname=hostname))

        db.session.commit()
        flash(f"Cluster '{cluster.name}' registered.", "success")
        return redirect(url_for("admin.cluster_detail", cluster_id=cluster.id))

    return render_template("admin/cluster_form.html", form=form, title="Register cluster")


@bp.route("/clusters/<int:cluster_id>")
@admin_required
def cluster_detail(cluster_id: int):
    cluster = Cluster.query.get_or_404(cluster_id)
    perm_form = TeamClusterPermissionForm()
    teams_without_perm = (
        Team.query
        .filter(~Team.id.in_(
            db.session.query(TeamClusterPermission.team_id)
            .filter_by(cluster_id=cluster_id)
        ))
        .order_by(Team.name.asc())
        .all()
    )
    perm_form.set_team_choices(teams_without_perm)
    return render_template(
        "admin/cluster_detail.html",
        cluster=cluster,
        perm_form=perm_form,
        has_available_teams=bool(teams_without_perm),
    )


@bp.route("/clusters/<int:cluster_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_cluster(cluster_id: int):
    cluster = Cluster.query.get_or_404(cluster_id)
    form = ClusterForm(obj=cluster)

    if request.method == "GET":
        # Pre-fill node fields from existing nodes
        nodes = cluster.nodes
        if len(nodes) > 0:
            form.node1.data = nodes[0].hostname
        if len(nodes) > 1:
            form.node2.data = nodes[1].hostname
        if len(nodes) > 2:
            form.node3.data = nodes[2].hostname

    if form.validate_on_submit():
        name = form.name.data.strip()
        if (
            Cluster.query
            .filter(Cluster.id != cluster.id)
            .filter(func.lower(Cluster.name) == name.lower())
            .first()
        ):
            flash("A cluster with this name already exists.", "danger")
            return render_template(
                "admin/cluster_form.html", form=form, title=f"Edit {cluster.name}"
            )

        cluster.name = name
        cluster.load_balancer = form.load_balancer.data.strip()
        cluster.description = form.description.data.strip() if form.description.data else None
        cluster.ssh_user = form.ssh_user.data.strip()
        cluster.ssh_key_path = form.ssh_key_path.data.strip() or None

        # Replace nodes with whatever the form provides
        for node in list(cluster.nodes):
            db.session.delete(node)
        db.session.flush()
        for hostname in form.node_hostnames():
            db.session.add(Node(cluster_id=cluster.id, hostname=hostname))

        db.session.commit()
        flash("Cluster updated.", "success")
        return redirect(url_for("admin.cluster_detail", cluster_id=cluster.id))

    return render_template(
        "admin/cluster_form.html", form=form, title=f"Edit {cluster.name}"
    )


@bp.route("/clusters/<int:cluster_id>/delete", methods=["POST"])
@admin_required
def delete_cluster(cluster_id: int):
    cluster = Cluster.query.get_or_404(cluster_id)
    db.session.delete(cluster)
    db.session.commit()
    flash(f"Cluster '{cluster.name}' deleted.", "success")
    return redirect(url_for("admin.clusters"))


@bp.route("/clusters/<int:cluster_id>/sync", methods=["POST"])
@admin_required
def sync_cluster(cluster_id: int):
    from ..services.cluster_discovery import ClusterDiscoveryService

    cluster = Cluster.query.get_or_404(cluster_id)
    svc = ClusterDiscoveryService(cluster)
    success, message = svc.run()

    if success:
        flash(f"Sync complete. {message}", "success")
    else:
        flash(f"Sync failed: {message}", "danger")

    return redirect(url_for("admin.cluster_detail", cluster_id=cluster_id))


@bp.route("/clusters/<int:cluster_id>/permissions/grant", methods=["POST"])
@admin_required
def grant_cluster_permission(cluster_id: int):
    cluster = Cluster.query.get_or_404(cluster_id)
    teams_without_perm = (
        Team.query
        .filter(~Team.id.in_(
            db.session.query(TeamClusterPermission.team_id)
            .filter_by(cluster_id=cluster_id)
        ))
        .order_by(Team.name.asc())
        .all()
    )
    perm_form = TeamClusterPermissionForm()
    perm_form.set_team_choices(teams_without_perm)

    if perm_form.validate_on_submit():
        perm = TeamClusterPermission(
            team_id=perm_form.team_id.data,
            cluster_id=cluster.id,
            permission_level=perm_form.permission_level.data,
        )
        db.session.add(perm)
        db.session.commit()
        flash("Access granted.", "success")
    else:
        flash("Could not grant access. Please check the form.", "danger")

    return redirect(url_for("admin.cluster_detail", cluster_id=cluster_id))


@bp.route("/clusters/<int:cluster_id>/permissions/<int:perm_id>/revoke", methods=["POST"])
@admin_required
def revoke_cluster_permission(cluster_id: int, perm_id: int):
    perm = TeamClusterPermission.query.get_or_404(perm_id)
    db.session.delete(perm)
    db.session.commit()
    flash("Access revoked.", "success")
    return redirect(url_for("admin.cluster_detail", cluster_id=cluster_id))

