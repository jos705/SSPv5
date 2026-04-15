"""
Shared pytest/unittest configuration for the test suite.

Database selection (in priority order):
  1. TEST_DATABASE_URL env var — set this in CI to point at the PostgreSQL
     service container.  Using a separate variable ensures the production
     DATABASE_URL (loaded from .env by config.py at import time) can never
     accidentally be used for tests.
  2. SQLite in-memory — the default for local development; fast, no setup.

Important: do NOT use DATABASE_URL here.  config.py calls load_dotenv() at
import time, which sets DATABASE_URL in os.environ from the .env file.  Tests
must not connect to the production database.
"""
from __future__ import annotations

import os


class TestConfig:
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    WTF_CSRF_ENABLED = False


def safe_drop_all(db) -> None:
    """
    Drop all tables — but only when connected to SQLite or a database whose
    URL contains 'test'.  Raises RuntimeError if the URL looks like a
    production database, preventing accidental data loss.
    """
    uri = db.engine.url.render_as_string(hide_password=True)
    is_safe = "sqlite" in uri or "test" in uri
    if not is_safe:
        raise RuntimeError(
            f"safe_drop_all() refused to drop tables on '{uri}'. "
            "The test database URL must contain 'test' or use SQLite."
        )
    db.drop_all()
