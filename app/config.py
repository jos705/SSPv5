from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///devops_portal.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # SSH security
    # Set SSH_STRICT_HOST_KEYS=true in production to reject unknown host keys.
    # When false (default for dev) a WarningPolicy is used: new keys are accepted
    # with a log warning rather than silently (AutoAddPolicy) or hard-failing.
    SSH_STRICT_HOST_KEYS: bool = os.getenv("SSH_STRICT_HOST_KEYS", "false").lower() == "true"
    # Optional path to an additional known_hosts file managed by this app.
    # Populate it by running: ssh-keyscan <node> >> <path>
    SSH_KNOWN_HOSTS_FILE: str | None = os.getenv("SSH_KNOWN_HOSTS_FILE") or None

