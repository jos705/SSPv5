# DevOps Self-Service Portal

A Flask-based self-service portal for managing PostgreSQL databases across registered clusters, complete with SSH-based discovery, approval workflows, and an admin control panel.

## Features

- **Authentication & RBAC** — login/logout, role-based access (admin vs user), password management
- **Team management** — create teams, assign users, control cluster permissions
- **Cluster registration** — register PostgreSQL clusters and discover instances via SSH
- **Database lifecycle** — request, provision, and delete PostgreSQL databases with direct or approval-based workflows
- **Approval queue** — admin review and approve/reject pending database requests
- **Observability** — operation logs, enriched dashboards, structured logging

---

## Requirements

- Python 3.11+
- PostgreSQL 14+ (running and accessible)
- Network/SSH access to registered cluster nodes

---

## Setup

### 1. Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. Configure environment

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set at minimum:

- `SECRET_KEY` — long random string, **change this in production**
- `DATABASE_URL` — PostgreSQL connection URL
- `LOG_LEVEL` — `DEBUG` / `INFO` / `WARNING` (default: `INFO`)
- `SSH_STRICT_HOST_KEYS` — `true` in production, `false` for dev

Example `DATABASE_URL`:
```
DATABASE_URL=postgresql+psycopg://portal_user:portal_password@localhost:5432/devops_portal
```

### 3. Apply database migrations

```powershell
python -m flask db upgrade
```

This creates all tables. Re-run after any future code updates to apply new migrations.

### 4. Create the first admin user

```powershell
python -m flask create-admin
```

You will be prompted for email, username, and password.

### 5. Verify the database (optional)

```powershell
python check_db.py
```

Confirms connectivity, all expected tables, migration head, and admin user presence.

---

## Running the application

### Development server

Uses Flask's built-in server with hot reload. **Do not use in production.**

```powershell
python run.py
```

Open `http://127.0.0.1:5000`.

### Production server

Uses [waitress](https://docs.pylonsproject.org/projects/waitress/), a pure-Python WSGI server that works on Windows.

```powershell
python serve.py
```

Configure via environment variables:

- `HOST` — bind address (default: `0.0.0.0`)
- `PORT` — bind port (default: `5000`)
- `THREADS` — worker threads (default: `4`)

Example (custom port, 8 threads):
```powershell
$env:PORT="8080"; $env:THREADS="8"; python serve.py
```

---

## Admin user management

### Create a new admin user

```powershell
python -m flask create-admin
```

### Reset an existing user's password

Open a Flask shell:

```powershell
python -m flask shell
```

Then run:

```python
from app.extensions import db
from app.models import User

user = db.session.execute(db.select(User).filter_by(username="admin")).scalar_one()
user.set_password("NewSecurePassword123!")
db.session.commit()
print("Password updated for:", user.email)
```

### Promote an existing user to admin

```python
from app.extensions import db
from app.models import User, UserRole

user = db.session.execute(db.select(User).filter_by(username="someuser")).scalar_one()
user.role = UserRole.ADMIN.value
db.session.commit()
print(user.username, "is now admin")
```

### List all admin accounts

```python
from app.extensions import db
from app.models import User, UserRole

admins = db.session.execute(
    db.select(User).filter_by(role=UserRole.ADMIN.value)
).scalars().all()
for u in admins:
    print(u.username, u.email, "active:", u.is_active)
```

---

## Database migrations

After pulling new code that includes model changes, always run:

```powershell
python -m flask db upgrade
```

To check the current migration revision:

```powershell
python -m flask db current
```

To generate a new migration after changing models (developers only):

```powershell
python -m flask db migrate -m "describe the change"
python -m flask db upgrade
```

---

## CLI reference

| Command | Description |
|---|---|
| `python -m flask db upgrade` | Apply all pending migrations |
| `python -m flask db current` | Show current migration revision |
| `python -m flask create-admin` | Interactively create an admin user |
| `python -m flask shell` | Open a Flask application shell |
| `python check_db.py` | Verify DB connection and schema |
| `python run.py` | Start development server |
| `python serve.py` | Start production server (waitress) |

---

## Project structure

```
app/
  admin/          — Admin blueprint (users, teams, clusters, requests, operations)
  auth/           — Authentication blueprint (login, logout, password change)
  databases/      — Database lifecycle blueprint (create, delete, request)
  main/           — User dashboard blueprint
  models.py       — SQLAlchemy models
  services/
    cluster_discovery.py  — SSH-based cluster/instance discovery
    provisioning.py       — SSH-based database provisioning
    approval_workflow.py  — Request approval state machine
    ssh_client.py         — Shared paramiko SSH client factory
  static/         — CSS and static assets
  templates/      — Jinja2 HTML templates
migrations/       — Alembic migration scripts
check_db.py       — DB connectivity and schema verification script
run.py            — Development server entry point
serve.py          — Production server entry point (waitress)
```

---

## Notes

- Never use `run.py` in production — it runs Flask's dev server with debug mode enabled.
- Always set a strong, unique `SECRET_KEY` in production to protect session cookies.
- Set `SSH_STRICT_HOST_KEYS=true` in production and populate a `SSH_KNOWN_HOSTS_FILE` to prevent SSH MITM attacks.
- PostgreSQL is required; SQLite is only used as a fallback when `DATABASE_URL` is not set.
