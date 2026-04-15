"""
Shared SSH client factory
=========================
Centralises all paramiko connection setup so both ``ClusterDiscoveryService``
and ``ProvisioningService`` use identical security settings.

Host-key policy (controlled by SSH_STRICT_HOST_KEYS in .env)
-------------------------------------------------------------
``false`` (default / development)
    ``WarningPolicy`` — accepts unknown host keys but writes a WARNING to the
    application log.  Easier to bootstrap without pre-seeding known_hosts.

``true`` (production)
    ``RejectPolicy`` — raises ``paramiko.SSHException`` for any host whose key
    is not already present in the system known_hosts or the optional app-managed
    ``SSH_KNOWN_HOSTS_FILE``.  Prevents man-in-the-middle attacks.

To add a node to the app known_hosts file::

    ssh-keyscan -t ed25519 <node-hostname> >> /path/to/app_known_hosts
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import paramiko
from flask import current_app

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


def open_ssh_client(
    hostname: str,
    username: str,
    key_path: str | None = None,
    timeout: int = 15,
) -> paramiko.SSHClient:
    """
    Return an open, authenticated ``paramiko.SSHClient``.

    Parameters
    ----------
    hostname:
        Remote host to connect to.
    username:
        SSH login user.
    key_path:
        Path to a specific private key file.  ``None`` lets paramiko discover
        the default key (``~/.ssh/id_ed25519``, ``~/.ssh/id_rsa``, etc.).
    timeout:
        TCP connect timeout in seconds.

    Raises
    ------
    paramiko.SSHException
        If ``SSH_STRICT_HOST_KEYS=true`` and the host key is unknown.
    socket.timeout / OSError
        On connectivity problems.
    """
    strict: bool = current_app.config.get("SSH_STRICT_HOST_KEYS", False)
    extra_known_hosts: str | None = current_app.config.get("SSH_KNOWN_HOSTS_FILE")

    client = paramiko.SSHClient()
    client.load_system_host_keys()

    if extra_known_hosts and os.path.isfile(extra_known_hosts):
        client.load_host_keys(extra_known_hosts)
        log.debug("Loaded extra known_hosts from %s", extra_known_hosts)

    if strict:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        log.debug("SSH strict mode: RejectPolicy active for %s", hostname)
    else:
        client.set_missing_host_key_policy(paramiko.WarningPolicy())

    connect_kwargs: dict = {
        "hostname": hostname,
        "username": username,
        "timeout": timeout,
    }
    if key_path:
        connect_kwargs["key_filename"] = key_path

    try:
        client.connect(**connect_kwargs)
        log.debug("SSH connected to %s@%s", username, hostname)
    except Exception:
        client.close()
        raise

    return client
