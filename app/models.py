from __future__ import annotations

from enum import Enum

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


class ClusterStatus(str, Enum):
    PENDING = "pending"   # registered, discovery not yet run
    ACTIVE = "active"     # successfully discovered
    ERROR = "error"       # last discovery attempt failed


class PermissionLevel(str, Enum):
    DIRECT = "direct"     # team may create/delete without approval
    REQUEST = "request"   # actions require admin approval


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class Team(db.Model):
    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
        nullable=False,
    )

    users = db.relationship("User", back_populates="team")
    cluster_permissions = db.relationship(
        "TeamClusterPermission", back_populates="team", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Team {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    username = db.Column(db.String(100), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=UserRole.USER.value, index=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
        nullable=False,
    )

    team = db.relationship("Team", back_populates="users")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value

    @property
    def role_label(self) -> str:
        return "Admin" if self.is_admin else "User"

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class Cluster(db.Model):
    """A PostgreSQL cluster: 2-3 nodes behind a load balancer."""

    __tablename__ = "clusters"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    load_balancer = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    status = db.Column(
        db.String(20), nullable=False, default=ClusterStatus.PENDING.value, index=True
    )
    ssh_user = db.Column(db.String(100), nullable=False, default="postgres")
    # Path to the SSH private key; None means use the process default (~/.ssh/id_*)
    ssh_key_path = db.Column(db.String(500), nullable=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)
    sync_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
        nullable=False,
    )

    nodes = db.relationship("Node", back_populates="cluster", cascade="all, delete-orphan")
    instances = db.relationship(
        "PgInstance", back_populates="cluster", cascade="all, delete-orphan"
    )
    team_permissions = db.relationship(
        "TeamClusterPermission", back_populates="cluster", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Cluster {self.name}>"


class Node(db.Model):
    """An individual server that is part of a Cluster."""

    __tablename__ = "nodes"
    __table_args__ = (
        db.UniqueConstraint("cluster_id", "hostname", name="uq_node_cluster_hostname"),
    )

    id = db.Column(db.Integer, primary_key=True)
    cluster_id = db.Column(
        db.Integer, db.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hostname = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    cluster = db.relationship("Cluster", back_populates="nodes")

    def __repr__(self) -> str:
        return f"<Node {self.hostname}>"


class PgInstance(db.Model):
    """A single PostgreSQL instance (port + data dir) discovered on a node."""

    __tablename__ = "pg_instances"
    __table_args__ = (
        db.UniqueConstraint(
            "cluster_id", "hostname", "port", name="uq_instance_cluster_host_port"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    cluster_id = db.Column(
        db.Integer, db.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hostname = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    instance_name = db.Column(db.String(120), nullable=False)
    pgdata_dir = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    cluster = db.relationship("Cluster", back_populates="instances")

    def __repr__(self) -> str:
        return f"<PgInstance {self.hostname}:{self.port}>"


class TeamClusterPermission(db.Model):
    """Controls what actions a team may take on a given cluster."""

    __tablename__ = "team_cluster_permissions"
    __table_args__ = (
        db.UniqueConstraint("team_id", "cluster_id", name="uq_team_cluster"),
    )

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(
        db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cluster_id = db.Column(
        db.Integer, db.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission_level = db.Column(
        db.String(20), nullable=False, default=PermissionLevel.REQUEST.value
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
        nullable=False,
    )

    team = db.relationship("Team", back_populates="cluster_permissions")
    cluster = db.relationship("Cluster", back_populates="team_permissions")

    def __repr__(self) -> str:
        return f"<TeamClusterPermission team={self.team_id} cluster={self.cluster_id}>"

