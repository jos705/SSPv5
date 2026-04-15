from __future__ import annotations

import click
import logging
import logging.config
from flask import Flask, render_template
from sqlalchemy import func, or_

from .config import Config
from .extensions import csrf, db, login_manager, migrate
from .models import (  # noqa: F401
    Cluster, DatabaseAsset, DatabaseRequest, Node, OperationLog,
    PgInstance, TeamClusterPermission, User, UserRole,
)


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    _configure_logging(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_cli_commands(app)

    return app


def _configure_logging(app: Flask) -> None:
    """Configure application-wide logging with a structured format."""
    log_level_name = app.config.get("LOG_LEVEL", "INFO")
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)

    fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    # Only configure if not already configured (avoids double-formatting in tests)
    if not logging.root.handlers:
        logging.basicConfig(level=log_level, format=fmt, datefmt=datefmt)
    else:
        logging.root.setLevel(log_level)
        for handler in logging.root.handlers:
            handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    # Suppress noisy third-party loggers
    logging.getLogger("paramiko.transport").setLevel(logging.WARNING)
    logging.getLogger("paramiko.hostkeys").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    app.logger.setLevel(log_level)
    app.logger.info("Logging configured — level=%s", log_level_name.upper())


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
    from .databases import bp as databases_bp
    from .main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(databases_bp)


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

