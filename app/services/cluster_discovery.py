"""
ClusterDiscoveryService
=======================
Connects to each registered node in a Cluster over SSH, reads the two
configuration files written by the DBA tooling, and persists the discovered
PostgreSQL instances into the database.

Remote files
------------
* ``/home/postgres/postgres.info``
  Key=value text file.  The only required key is::

      clustername=<name>

* ``/home/postgres/beheer/scripts/postgrestab``
  One line per PostgreSQL instance::

      Hostname:Port:Instancename:PGDATA_dir

  Blank lines and lines starting with ``#`` are ignored.

SSH security note
-----------------
Currently uses ``AutoAddPolicy`` so the app can connect to freshly
provisioned nodes without manual known_hosts population.  Phase 4 will
harden this to ``RejectPolicy`` backed by a pre-seeded known_hosts store.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import paramiko

from ..extensions import db
from ..models import ClusterStatus, PgInstance
from .ssh_client import open_ssh_client

if TYPE_CHECKING:
    from ..models import Cluster

log = logging.getLogger(__name__)

POSTGRES_INFO_PATH = "/home/postgres/postgres.info"
POSTGRESTAB_PATH = "/home/postgres/beheer/scripts/postgrestab"
SSH_TIMEOUT = 15  # seconds per connection attempt


class DiscoveryError(Exception):
    """Raised when discovery cannot proceed due to a fatal error."""


class ClusterDiscoveryService:
    """
    Discover PostgreSQL instances for a single ``Cluster`` record.

    Usage::

        svc = ClusterDiscoveryService(cluster)
        success, message = svc.run()
    """

    def __init__(self, cluster: Cluster) -> None:
        self.cluster = cluster

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> tuple[bool, str]:
        """
        Run a full SSH discovery cycle.

        Returns ``(True, summary)`` on success or ``(False, error)`` on
        failure.  The ``Cluster`` record is updated in-place and committed
        regardless of outcome.
        """
        if not self.cluster.nodes:
            return self._fail("No nodes registered for this cluster.")

        try:
            cluster_names: dict[str, str] = {}  # hostname -> clustername
            node_errors: list[str] = []

            # --- Step 1: validate cluster name on every node ----------
            for node in self.cluster.nodes:
                try:
                    cn = self._read_cluster_name(node.hostname)
                    cluster_names[node.hostname] = cn
                    log.info("Node %s reports clustername=%s", node.hostname, cn)
                except Exception as exc:
                    node_errors.append(f"{node.hostname}: {exc}")
                    log.warning("Failed to read postgres.info on %s: %s", node.hostname, exc)

            if node_errors:
                raise DiscoveryError(
                    "SSH errors reading postgres.info — "
                    + "; ".join(node_errors)
                )

            # --- Step 2: warn on cluster-name mismatch ---------------
            unique_names = set(cluster_names.values())
            warning: str | None = None
            if len(unique_names) > 1:
                raise DiscoveryError(
                    f"Inconsistent cluster names across nodes: {cluster_names}"
                )
            if unique_names:
                found = next(iter(unique_names))
                if found != self.cluster.name:
                    warning = (
                        f"Cluster name mismatch: registered as '{self.cluster.name}', "
                        f"nodes report '{found}'."
                    )
                    log.warning(warning)

            # --- Step 3: read postgrestab from the first reachable node
            instances_raw = self._read_instances_from_first_node()

            # --- Step 4: atomically replace PgInstance records -------
            PgInstance.query.filter_by(cluster_id=self.cluster.id).delete()
            db.session.flush()

            for inst in instances_raw:
                db.session.add(
                    PgInstance(
                        cluster_id=self.cluster.id,
                        hostname=inst["hostname"],
                        port=inst["port"],
                        instance_name=inst["instance_name"],
                        pgdata_dir=inst["pgdata_dir"],
                    )
                )

            self.cluster.status = ClusterStatus.ACTIVE.value
            self.cluster.last_synced_at = datetime.now(timezone.utc)
            self.cluster.sync_error = warning
            db.session.commit()

            summary = f"Discovered {len(instances_raw)} instance(s)."
            if warning:
                summary += f" Warning: {warning}"
            log.info("Cluster '%s' synced. %s", self.cluster.name, summary)
            return True, summary

        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            error_msg = str(exc)
            log.error("Discovery failed for cluster '%s': %s", self.cluster.name, error_msg)
            return self._fail(error_msg)

    def _fail(self, message: str) -> tuple[bool, str]:
        """Record a failure on the cluster and return (False, message)."""
        self.cluster.status = ClusterStatus.ERROR.value
        self.cluster.last_synced_at = datetime.now(timezone.utc)
        self.cluster.sync_error = message
        db.session.commit()
        return False, message

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _open_client(self, hostname: str) -> paramiko.SSHClient:
        """Return an open, authenticated SSHClient for *hostname*."""
        return open_ssh_client(
            hostname=hostname,
            username=self.cluster.ssh_user,
            key_path=self.cluster.ssh_key_path or None,
            timeout=SSH_TIMEOUT,
        )

    def _read_remote_file(self, client: paramiko.SSHClient, path: str) -> str:
        """Read the text content of *path* on the remote host via SFTP."""
        sftp = client.open_sftp()
        try:
            with sftp.open(path, "r") as fh:
                return fh.read().decode("utf-8", errors="replace")
        finally:
            sftp.close()

    def _read_cluster_name(self, hostname: str) -> str:
        """SSH to *hostname* and parse ``clustername`` from postgres.info."""
        client = self._open_client(hostname)
        try:
            content = self._read_remote_file(client, POSTGRES_INFO_PATH)
            return self._parse_postgres_info(content, hostname)
        finally:
            client.close()

    def _read_instances_from_first_node(self) -> list[dict]:
        """
        Try each node in order until one returns a valid postgrestab.
        Raises ``DiscoveryError`` if no node succeeds.
        """
        errors: list[str] = []
        for node in self.cluster.nodes:
            try:
                client = self._open_client(node.hostname)
                try:
                    content = self._read_remote_file(client, POSTGRESTAB_PATH)
                finally:
                    client.close()
                instances = self._parse_postgrestab(content, node.hostname)
                log.info(
                    "Read %d instance(s) from postgrestab on %s",
                    len(instances),
                    node.hostname,
                )
                return instances
            except Exception as exc:
                errors.append(f"{node.hostname}: {exc}")
                log.warning("Could not read postgrestab from %s: %s", node.hostname, exc)

        raise DiscoveryError(
            "Could not read postgrestab from any node — " + "; ".join(errors)
        )

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_postgres_info(content: str, hostname: str) -> str:
        """
        Extract the ``clustername`` value from postgres.info content.

        Expected format (one key=value per line)::

            clustername=prod-pg-01
        """
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                if key.strip().lower() == "clustername":
                    return value.strip()

        raise DiscoveryError(
            f"'clustername=' not found in {POSTGRES_INFO_PATH} on {hostname}"
        )

    @staticmethod
    def _parse_postgrestab(content: str, hostname: str) -> list[dict]:
        """
        Parse instances from postgrestab content.

        Expected format (one line per instance)::

            Hostname:Port:Instancename:PGDATA_dir

        Blank lines and ``#`` comment lines are skipped.
        """
        instances: list[dict] = []

        for lineno, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(":")
            if len(parts) != 4:
                raise DiscoveryError(
                    f"Malformed line {lineno} in {POSTGRESTAB_PATH} on {hostname}: "
                    f"'{line}' — expected Hostname:Port:Instancename:PGDATA_dir"
                )

            inst_host, port_str, instance_name, pgdata_dir = [p.strip() for p in parts]

            if not inst_host:
                raise DiscoveryError(
                    f"Empty hostname on line {lineno} in {POSTGRESTAB_PATH} on {hostname}"
                )

            try:
                port = int(port_str)
            except ValueError:
                raise DiscoveryError(
                    f"Invalid port '{port_str}' on line {lineno} "
                    f"in {POSTGRESTAB_PATH} on {hostname}"
                )

            if not (1 <= port <= 65535):
                raise DiscoveryError(
                    f"Port {port} out of range on line {lineno} "
                    f"in {POSTGRESTAB_PATH} on {hostname}"
                )

            instances.append(
                {
                    "hostname": inst_host,
                    "port": port,
                    "instance_name": instance_name,
                    "pgdata_dir": pgdata_dir,
                }
            )

        return instances
