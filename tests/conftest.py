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
