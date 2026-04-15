"""Quick smoke-test: verify DB connection and that all expected tables exist."""
import psycopg

DSN = "postgresql://portal_user:portal_password@geek:5432/devops_portal"

EXPECTED_TABLES = {
    "alembic_version",
    "users",
    "teams",
    "clusters",
    "nodes",
    "pg_instances",
    "team_cluster_permissions",
    "database_assets",
    "database_requests",
    "operation_logs",
}

with psycopg.connect(DSN) as conn:
    print(f"Connected OK — server version: {conn.info.server_version}")
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()
    present = {r[0] for r in rows}
    print(f"\nTables in DB ({len(present)}):")
    for t in sorted(present):
        status = "OK" if t in EXPECTED_TABLES else "EXTRA"
        print(f"  [{status}] {t}")

    missing = EXPECTED_TABLES - present
    if missing:
        print(f"\nMISSING tables: {sorted(missing)}")
    else:
        print("\nAll expected tables are present.")

    # Check admin user exists
    admin = conn.execute(
        "SELECT username, role FROM users WHERE role = 'admin' LIMIT 1"
    ).fetchone()
    if admin:
        print(f"\nAdmin user found: {admin[0]} (role={admin[1]})")
    else:
        print("\nWARNING: No admin user found in users table.")
