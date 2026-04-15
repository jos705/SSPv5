# DevOps Self-Service Portal (Phase 1)
This repository contains Phase 1 of the Flask-based DevOps self-service portal:
- Flask application scaffold with Blueprints and app factory
- Authentication (login/logout) and password change
- Role-based access control (admin vs user)
- Admin dashboard with team and user management (CRUD)
- Base responsive UI with light/dark theme toggle
## Quickstart
### 1) Create virtual environment and install dependencies
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
### 2) Configure environment
Copy `.env.example` to `.env` and update values (especially `SECRET_KEY` and `DATABASE_URL`).
### 3) Initialize database
```powershell
flask --app run.py init-db
```
### 4) Create the first admin user
```powershell
flask --app run.py create-admin
```
### 5) Run the app
```powershell
python run.py
```
Open `http://127.0.0.1:5000`.
## Notes
- For local development, if `DATABASE_URL` is not set, SQLite is used (`devops_portal.db`).
- Production deployments should use PostgreSQL and a strong `SECRET_KEY`.
