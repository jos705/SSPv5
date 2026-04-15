from __future__ import annotations

import click
from flask import Flask, render_template
from sqlalchemy import func, or_

from .config import Config
from .extensions import csrf, db, login_manager, migrate
from .models import User, UserRole


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    _init_extensions(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_cli_commands(app)

    return app


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        if not user_id.isdigit():
            return None
        return db.session.get(User, int(user_id))


def _register_blueprints(app: Flask) -> None:
    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404


def _register_cli_commands(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db() -> None:
        """Create database tables."""
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--username", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(email: str, username: str, password: str) -> None:
        """Create an administrator account."""
        normalized_email = email.strip().lower()
        normalized_username = username.strip()

        existing_user = User.query.filter(
            or_(
                func.lower(User.email) == normalized_email,
                func.lower(User.username) == normalized_username.lower(),
            )
        ).first()
        if existing_user:
            raise click.ClickException("A user with that email or username already exists.")

        admin = User(
            email=normalized_email,
            username=normalized_username,
            role=UserRole.ADMIN.value,
            team_id=None,
        )
        admin.set_password(password)

        db.session.add(admin)
        db.session.commit()
        click.echo(f"Admin user created: {admin.email}")

