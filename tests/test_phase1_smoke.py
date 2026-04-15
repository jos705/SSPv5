from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.extensions import db
from app.models import Team, User, UserRole
from tests.conftest import TestConfig, safe_drop_all


class Phase1SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app(TestConfig)
    @classmethod
    def tearDownClass(cls) -> None:
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def setUp(self) -> None:
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        admin = User(
            email="admin@example.com",
            username="admin",
            role=UserRole.ADMIN.value,
            team_id=None,
        )
        admin.set_password("AdminPass123!")
        db.session.add(admin)
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self) -> None:
        db.session.remove()
        safe_drop_all(db)
        self.app_context.pop()

    def test_authentication_rbac_and_admin_crud(self) -> None:
        response = self.client.post(
            "/auth/login",
            data={"email": "admin@example.com", "password": "AdminPass123!"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"You are now signed in.", response.data)

        response = self.client.post(
            "/admin/teams/new",
            data={"name": "Platform Team", "description": "Core platform ops"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Team created successfully.", response.data)

        created_team = Team.query.filter_by(name="Platform Team").first()
        self.assertIsNotNone(created_team)
        team_id = created_team.id

        response = self.client.post(
            "/admin/users/new",
            data={
                "email": "alice@example.com",
                "username": "alice",
                "role": "user",
                "team_id": str(team_id),
                "password": "AlicePass123!",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"User created successfully.", response.data)

        created_user = User.query.filter_by(email="alice@example.com").first()
        self.assertIsNotNone(created_user)
        user_id = created_user.id

        response = self.client.post("/auth/logout", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"You have been signed out.", response.data)

        response = self.client.post(
            "/auth/login",
            data={"email": "alice@example.com", "password": "AlicePass123!"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 403)

        self.client.post("/auth/logout", follow_redirects=True)

        response = self.client.post(
            "/auth/login",
            data={"email": "admin@example.com", "password": "AdminPass123!"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            f"/admin/teams/{team_id}/edit",
            data={"name": "Platform Team", "description": "Updated description"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Team updated successfully.", response.data)

        response = self.client.post(
            f"/admin/users/{user_id}/edit",
            data={
                "email": "alice@example.com",
                "username": "alice.ops",
                "role": "user",
                "team_id": str(team_id),
                "password": "NewAlicePass123!",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"User updated successfully.", response.data)

        response = self.client.post(f"/admin/users/{user_id}/delete", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"User deleted.", response.data)

        response = self.client.post(f"/admin/teams/{team_id}/delete", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Team deleted.", response.data)


if __name__ == "__main__":
    unittest.main()
