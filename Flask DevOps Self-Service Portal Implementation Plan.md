# Problem Statement
Build a Flask-based self-service portal for DevOps engineers with secure authentication, RBAC, team/user administration, PostgreSQL database provisioning workflows, SSH-based cluster integration, role-specific dashboards, and deployable operational documentation.
## Current State
Requirements are defined in `prompt.txt`, but no implementation baseline is established yet.
The solution must support two roles (admin and user), team-scoped operations, multi-cluster PostgreSQL topology, script-based lifecycle actions over SSH, and approval-based controls for production-sensitive actions.
## Proposed Changes
### 1) Architecture and stack
Use a Flask application factory with Blueprints (`auth`, `admin`, `users`, `clusters`, `databases`, `requests`, `audit`) and a service layer for business rules.
Use PostgreSQL for app data, SQLAlchemy for ORM, Alembic for migrations, Flask-Login for sessions, Flask-WTF for CSRF/validation, and a background worker (Celery or RQ with Redis) for SSH operations.
Implement a Cockpit-inspired UI using Bootstrap plus custom tokens, with persisted light/dark theme preference.
### 2) Core domain model
Create entities for User, Role, Team, Cluster, Node, PgInstance, TeamClusterPermission, DatabaseAsset, DatabaseRequest, ApprovalDecision, OperationLog, and AuditEvent.
Represent per-team cluster permissions at action level (direct create/delete vs request-required).
Track database provenance (team-created vs DBA-created-for-team) to enforce deletion pathways.
### 3) Authentication, RBAC, and security
Implement credential-based login, password hashing (Argon2 or bcrypt), and user password-change flow.
Enforce RBAC at route and service layers so only admins can access admin functionality.
Harden remote operations with host key verification, SSH key auth, strict command allow-listing, input sanitization, and complete audit logging.
### 4) Cluster onboarding and discovery
Add an admin flow to register cluster nodes and load balancer address.
During initialization, run SSH discovery to parse `/home/postgres/postgres.info` and `/home/postgres/beheer/scripts/postgrestab`, validate consistency, and persist cluster/instance metadata.
Provide re-sync and clear error reporting for malformed files or inconsistent node data.
### 5) Database lifecycle workflows
Creation: user selects allowed cluster/instance and database name; app resolves port/load balancer from stored config.
If permission allows direct action, execute remote script and stream/store output.
If permission requires approval (e.g., PROD), create a request for admin approve/reject.
Deletion: allow direct delete only when policy permits; require approval path for DBA-created-for-team databases.
### 6) Dashboard design
Admin dashboard: team/user CRUD, cluster inventory, global database inventory, pending approvals, and operation history.
User dashboard: team-scoped databases, create/delete actions constrained by permission matrix, and request status tracking.
### 7) Service boundaries
Define modular services: IdentityService, TeamService, PermissionService, ClusterDiscoveryService, ProvisioningService, ApprovalWorkflowService, and AuditService.
Keep views/controllers thin and move policy/state-transition logic into services for testability.
### 8) Testing strategy
Write unit tests for RBAC policies, permission resolution, parser logic, request state machine, and SSH command construction.
Add integration tests for auth, dashboards, admin CRUD, and request approval flows.
Use mocked SSH execution in automated tests and include staging smoke tests for real command path validation.
### 9) Deployment and operations
Provide Docker Compose setup for web app, worker, Redis, and PostgreSQL app DB.
Support environment-based configuration for secrets, SSH settings, and runtime behavior.
Include migration commands, health checks, structured logging, backup guidance, and reverse-proxy/TLS deployment notes.
### 10) Documentation deliverables
Provide a README with quickstart, architecture, configuration, RBAC model, and operational workflows.
Provide an admin runbook for approvals, cluster sync, troubleshooting, and recovery.
Provide a developer guide for project layout, migrations, tests, and extension points.
### 11) Delivery phases
Phase 1: project scaffold, authentication, RBAC, team/user management, base UI.
Phase 2: cluster registration and SSH discovery ingestion.
Phase 3: database create/delete orchestration and approval workflows.
Phase 4: dashboard polish, observability, and audit hardening.
Phase 5: testing completion, deployment artifacts, and final documentation.