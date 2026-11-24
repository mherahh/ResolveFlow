"""Lightweight idempotent schema migrations for upgrading existing SQLite databases."""
from sqlalchemy import inspect, text

from database.db import Base, engine, get_session
from database import models as m


def _column_names(conn, table_name: str) -> set[str]:
    insp = inspect(conn)
    if not insp.has_table(table_name):
        return set()
    return {col["name"] for col in insp.get_columns(table_name)}


def migrate_schema():
    """Ensure all tables and new columns exist without destroying existing data."""
    Base.metadata.create_all(engine)

    additions = {
        "customers": [
            ("password_hash", "TEXT"),
            ("role", "TEXT DEFAULT 'customer'"),
            ("created_at", "DATETIME"),
        ],
        "refunds": [
            ("reference_id", "TEXT"),
        ],
        "tickets": [
            ("resolution_mode", "TEXT"),
            ("updated_at", "DATETIME"),
        ],
        "ticket_messages": [
            ("metadata_json", "TEXT"),
        ],
        "actions_log": [
            ("customer_id", "TEXT"),
            ("actor_id", "TEXT"),
            ("actor_role", "TEXT"),
            ("confirmation_id", "TEXT"),
            ("previous_state", "TEXT"),
            ("new_state", "TEXT"),
        ],
    }

    with engine.begin() as conn:
        for table, cols in additions.items():
            existing = _column_names(conn, table)
            if not existing:
                continue
            for col_name, col_type in cols:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))

        # Migrate legacy status 'pending_approval' -> 'awaiting_approval'
        if "tickets" in inspect(conn).get_table_names():
            conn.execute(text("UPDATE tickets SET status = 'awaiting_approval' WHERE status = 'pending_approval'"))

    # Backfill password hashes on seeded customers if missing and ensure manager M001 exists
    from auth import hash_password
    s = get_session()
    try:
        default_cust_hash = hash_password("Password123!")
        changed = False
        for c in s.query(m.Customer).all():
            if not c.password_hash:
                c.password_hash = default_cust_hash
                changed = True
            if not c.role:
                c.role = "customer"
                changed = True
        if not s.get(m.Customer, "M001") and s.query(m.Customer).count() > 0:
            if not s.query(m.Customer).filter_by(email="manager@resolveflow.io").first():
                s.add(m.Customer(
                    id="M001",
                    name="Tariq Mehmood",
                    email="manager@resolveflow.io",
                    tier="staff",
                    role="manager",
                    password_hash=hash_password("Manager123!"),
                ))
                changed = True
        if changed:
            s.commit()
    finally:
        s.close()
