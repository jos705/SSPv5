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


class DatabaseProvenance(str, Enum):
    TEAM = "team"   # created by the team via this portal
    DBA = "dba"     # created externally by a DBA and registered here


class DbAssetStatus(str, Enum):
    ACTIVE = "active"
    DELETING = "deleting"   # delete approved, awaiting execution
    DELETED = "deleted"
    FAILED = "failed"       # provisioning or deletion script failed


class RequestType(str, Enum):
    CREATE = "create"
    DELETE = "delete"


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class OperationStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


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
    database_assets = db.relationship("DatabaseAsset", back_populates="team")
    database_requests = db.relationship("DatabaseRequest", back_populates="team")

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
    database_assets_created = db.relationship(
        "DatabaseAsset", foreign_keys="DatabaseAsset.created_by_id", back_populates="created_by"
    )

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
    # Shell scripts executed on the DB nodes for create/delete operations.
    # The scripts accept: -d <dbname> -p <port> -l <load_balancer>
    create_db_script = db.Column(
        db.String(500),
        nullable=False,
        default="/home/postgres/beheer/scripts/create_database.sh",
    )
    delete_db_script = db.Column(
        db.String(500),
        nullable=False,
        default="/home/postgres/beheer/scripts/delete_database.sh",
    )
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
    database_assets = db.relationship("DatabaseAsset", back_populates="instance")
    database_requests = db.relationship("DatabaseRequest", back_populates="instance")

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


class DatabaseAsset(db.Model):
    """A PostgreSQL database known to the portal."""

    __tablename__ = "database_assets"
    __table_args__ = (
        db.UniqueConstraint("instance_id", "name", name="uq_asset_instance_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(63), nullable=False)  # PostgreSQL identifier limit
    instance_id = db.Column(
        db.Integer, db.ForeignKey("pg_instances.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    team_id = db.Column(
        db.Integer, db.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provenance = db.Column(
        db.String(10), nullable=False, default=DatabaseProvenance.TEAM.value
    )
    status = db.Column(
        db.String(20), nullable=False, default=DbAssetStatus.ACTIVE.value, index=True
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
        nullable=False,
    )

    instance = db.relationship("PgInstance", back_populates="database_assets")
    team = db.relationship("Team", back_populates="database_assets")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    requests = db.relationship("DatabaseRequest", back_populates="database_asset")
    operation_logs = db.relationship("OperationLog", back_populates="database_asset")

    def __repr__(self) -> str:
        return f"<DatabaseAsset {self.name}>"


class DatabaseRequest(db.Model):
    """An approval-workflow request to create or delete a database."""

    __tablename__ = "database_requests"

    id = db.Column(db.Integer, primary_key=True)
    request_type = db.Column(db.String(10), nullable=False, index=True)  # RequestType
    status = db.Column(
        db.String(20), nullable=False, default=RequestStatus.PENDING.value, index=True
    )
    database_name = db.Column(db.String(63), nullable=False)
    instance_id = db.Column(
        db.Integer, db.ForeignKey("pg_instances.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    team_id = db.Column(
        db.Integer, db.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # For DELETE requests, links back to the asset being removed
    database_asset_id = db.Column(
        db.Integer, db.ForeignKey("database_assets.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason = db.Column(db.Text, nullable=True)
    review_note = db.Column(db.Text, nullable=True)
    operation_output = db.Column(db.Text, nullable=True)
    requested_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    instance = db.relationship("PgInstance", back_populates="database_requests")
    team = db.relationship("Team", back_populates="database_requests")
    database_asset = db.relationship("DatabaseAsset", back_populates="requests")
    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

    def __repr__(self) -> str:
        return f"<DatabaseRequest {self.request_type} {self.database_name} [{self.status}]>"


class OperationLog(db.Model):
    """Immutable audit record of a provisioning SSH command execution."""

    __tablename__ = "operation_logs"

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(50), nullable=False)          # e.g. 'create_db', 'delete_db'
    status = db.Column(db.String(20), nullable=False, index=True)  # OperationStatus
    output = db.Column(db.Text, nullable=True)
    database_asset_id = db.Column(
        db.Integer, db.ForeignKey("database_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    database_request_id = db.Column(
        db.Integer, db.ForeignKey("database_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    triggered_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    database_asset = db.relationship("DatabaseAsset", back_populates="operation_logs")
    database_request = db.relationship("DatabaseRequest", backref="operation_logs")
    triggered_by = db.relationship("User", foreign_keys=[triggered_by_id])

    def __repr__(self) -> str:
        return f"<OperationLog {self.action} [{self.status}]>"

