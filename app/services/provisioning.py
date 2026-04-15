"""
ProvisioningService
===================
Executes DBA-managed shell scripts on PostgreSQL cluster nodes over SSH
to create or delete databases, then persists an immutable OperationLog.

Script interface (from spec)
-----------------------------
Both scripts accept positional flags::

    <script> -d <database_name> -p <port> -l <load_balancer>

Security
--------
Arguments are passed only from validated model fields — no user-supplied
strings reach the command line without going through the ORM first.
AutoAddPolicy is used for initial ease; Phase 4 will harden to RejectPolicy.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import paramiko

from ..extensions import db
from ..models import OperationLog, OperationStatus

if TYPE_CHECKING:
    from ..models import DatabaseAsset, DatabaseRequest, PgInstance, User

log = logging.getLogger(__name__)

SSH_TIMEOUT = 30  # seconds — scripts may take longer than discovery
# Allow only safe PostgreSQL identifier characters in database names
_DB_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")


class ProvisioningError(Exception):
    """Raised when a provisioning action cannot proceed."""


class ProvisioningService:
    """
    Execute create/delete scripts on a cluster node for a given PgInstance.

    Usage::

        svc = ProvisioningService(instance, triggered_by=current_user)
        ok, output = svc.create_database("my_db", request=db_request)
        ok, output = svc.delete_database("my_db", request=db_request)
    """

    def __init__(
        self,
        instance: PgInstance,
        triggered_by: User | None = None,
    ) -> None:
        self.instance = instance
        self.cluster = instance.cluster
        self.triggered_by = triggered_by

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_database(
        self,
        database_name: str,
        asset: DatabaseAsset | None = None,
        request: DatabaseRequest | None = None,
    ) -> tuple[bool, str]:
        """Run the create script. Returns (success, combined_output)."""
        return self._run(
            action="create_db",
            script=self.cluster.create_db_script,
            database_name=database_name,
            asset=asset,
            request=request,
        )

    def delete_database(
        self,
        database_name: str,
        asset: DatabaseAsset | None = None,
        request: DatabaseRequest | None = None,
    ) -> tuple[bool, str]:
        """Run the delete script. Returns (success, combined_output)."""
        return self._run(
            action="delete_db",
            script=self.cluster.delete_db_script,
            database_name=database_name,
            asset=asset,
            request=request,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(
        self,
        action: str,
        script: str,
        database_name: str,
        asset: DatabaseAsset | None,
        request: DatabaseRequest | None,
    ) -> tuple[bool, str]:
        self._validate_db_name(database_name)
        command = self._build_command(script, database_name)
        log.info(
            "Provisioning %s: instance=%s:%d db=%s",
            action, self.instance.hostname, self.instance.port, database_name
        )

        try:
            output = self._ssh_exec(command)
            success = True
            log.info("%s succeeded for '%s'", action, database_name)
        except Exception as exc:
            output = str(exc)
            success = False
            log.error("%s failed for '%s': %s", action, database_name, exc)

        self._persist_log(
            action=action,
            status=OperationStatus.SUCCESS.value if success else OperationStatus.FAILURE.value,
            output=output,
            asset=asset,
            request=request,
        )
        return success, output

    def _validate_db_name(self, name: str) -> None:
        if not _DB_NAME_RE.match(name):
            raise ProvisioningError(
                f"Invalid database name '{name}'. "
                "Must start with a letter or underscore and contain only "
                "alphanumeric characters and underscores (max 63 chars)."
            )

    def _build_command(self, script: str, database_name: str) -> str:
        """Build the allow-listed SSH command from stored model values only."""
        # All values come from ORM fields — not from raw user input
        return (
            f"{script}"
            f" -d {database_name}"
            f" -p {self.instance.port}"
            f" -l {self.cluster.load_balancer}"
        )

    def _ssh_exec(self, command: str) -> str:
        """
        Open an SSH connection to the first node of the cluster,
        execute *command*, and return the combined stdout+stderr.
        Raises on connection failure or non-zero exit code.
        """
        if not self.cluster.nodes:
            raise ProvisioningError("Cluster has no registered nodes.")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.load_system_host_keys()

        target_node = self.cluster.nodes[0]
        connect_kwargs: dict = {
            "hostname": target_node.hostname,
            "username": self.cluster.ssh_user,
            "timeout": SSH_TIMEOUT,
        }
        if self.cluster.ssh_key_path:
            connect_kwargs["key_filename"] = self.cluster.ssh_key_path

        try:
            client.connect(**connect_kwargs)
            stdin, stdout, stderr = client.exec_command(command, timeout=SSH_TIMEOUT)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            combined = (out + err).strip()

            if exit_code != 0:
                raise ProvisioningError(
                    f"Script exited with code {exit_code}. Output:\n{combined}"
                )
            return combined
        finally:
            client.close()

    def _persist_log(
        self,
        action: str,
        status: str,
        output: str,
        asset: DatabaseAsset | None,
        request: DatabaseRequest | None,
    ) -> None:
        log_entry = OperationLog(
            action=action,
            status=status,
            output=output,
            database_asset_id=asset.id if asset else None,
            database_request_id=request.id if request else None,
            triggered_by_id=self.triggered_by.id if self.triggered_by else None,
        )
        db.session.add(log_entry)
        db.session.flush()
