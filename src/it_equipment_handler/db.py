import sqlite3
from pathlib import Path
from typing import Any, Protocol


class EquipmentRepository(Protocol):
    """Minimal data-access interface required by EquipmentService."""

    def get_employee(self, employee_id: str) -> dict[str, Any] | None:
        """Fetch a single employee record by ID."""
        ...

    def get_employee_dispatches(self, employee_id: str) -> list[dict[str, Any]]:
        """Fetch all equipment dispatch records for an employee."""
        ...

    def get_employee_tickets(self, employee_id: str) -> list[dict[str, Any]]:
        """Fetch all tickets associated with an employee."""
        ...

    def get_department_annual_budget(self, department: str, year: int) -> dict[str, Any] | None:
        """Fetch a department's annual budget and dynamically summed spend for a year."""
        ...


def create_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with row dicts and foreign keys enabled."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all database tables required by the IT equipment system."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS departments (
            department TEXT PRIMARY KEY,
            annual_budget_usd REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS employees (
            employee_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            department TEXT NOT NULL REFERENCES departments(department),
            employment_type TEXT NOT NULL,
            work_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            hire_date TEXT NOT NULL,
            manager_id TEXT REFERENCES employees(employee_id)
        );

        CREATE TABLE IF NOT EXISTS resources (
            resource_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            tier TEXT NOT NULL,
            unit_cost REAL NOT NULL,
            requires_category TEXT,
            aliases TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS employee_resource_dispatches (
            dispatch_id TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL REFERENCES employees(employee_id),
            resource_id TEXT NOT NULL REFERENCES resources(resource_id),
            serial_id TEXT NOT NULL UNIQUE,
            dispatched_at TEXT NOT NULL,
            returned_at TEXT,
            condition_status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS structured_policies (
            policy_id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            employment_type TEXT NOT NULL,
            work_mode TEXT NOT NULL,
            category TEXT NOT NULL,
            allowed INTEGER NOT NULL,
            max_active_qty INTEGER NOT NULL,
            refresh_months INTEGER NOT NULL,
            max_auto_cost_usd REAL NOT NULL,
            min_tenure_months INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS policy_clauses (
            clause_id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            rule_text TEXT NOT NULL,
            default_reviewer_role TEXT
        );

        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL REFERENCES employees(employee_id),
            request_text TEXT NOT NULL,
            category TEXT,
            resource_id TEXT REFERENCES resources(resource_id),
            status TEXT NOT NULL,
            flag_for_human_review INTEGER NOT NULL,
            reviewer_employee_id TEXT NOT NULL REFERENCES employees(employee_id),
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- logs.employee_id intentionally omits a foreign key so nonexistent IDs (e.g. E999) can be logged as DENIED.
        CREATE TABLE IF NOT EXISTS logs (
            log_id TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL,
            request_text TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


class SqliteEquipmentRepository:
    """SQLite-backed implementation of EquipmentRepository."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Store the active SQLite connection."""
        self._conn = conn

    def get_employee(self, employee_id: str) -> dict[str, Any] | None:
        """Fetch a single employee record by ID."""
        row = self._conn.execute(
            "SELECT * FROM employees WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_employee_dispatches(self, employee_id: str) -> list[dict[str, Any]]:
        """Fetch all equipment dispatch records for an employee ordered newest first."""
        rows = self._conn.execute(
            """
            SELECT
                d.dispatch_id,
                d.employee_id,
                d.resource_id,
                r.name AS resource_name,
                r.category,
                r.tier,
                r.unit_cost,
                d.serial_id,
                d.dispatched_at,
                d.returned_at,
                d.condition_status
            FROM employee_resource_dispatches d
            JOIN resources r ON r.resource_id = d.resource_id
            WHERE d.employee_id = ?
            ORDER BY d.dispatched_at DESC, d.dispatch_id DESC
            """,
            (employee_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_employee_tickets(self, employee_id: str) -> list[dict[str, Any]]:
        """Fetch all tickets associated with an employee ordered newest first."""
        rows = self._conn.execute(
            """
            SELECT
                ticket_id,
                employee_id,
                request_text,
                category,
                resource_id,
                status,
                CAST(flag_for_human_review AS INTEGER) AS flag_for_human_review,
                reviewer_employee_id,
                reason,
                created_at
            FROM tickets
            WHERE employee_id = ?
            ORDER BY created_at DESC, ticket_id DESC
            """,
            (employee_id,),
        ).fetchall()
        tickets = []
        for row in rows:
            item = dict(row)
            item["flag_for_human_review"] = bool(item["flag_for_human_review"])
            tickets.append(item)
        return tickets

    def get_department_annual_budget(self, department: str, year: int) -> dict[str, Any] | None:
        """Calculate a department's annual budget and spend dynamically from current-year dispatches."""
        dept_row = self._conn.execute(
            "SELECT department, annual_budget_usd FROM departments WHERE department = ?",
            (department,),
        ).fetchone()
        if dept_row is None:
            return None

        year_prefix = f"{year:04d}-"
        spend_row = self._conn.execute(
            """
            SELECT COALESCE(SUM(r.unit_cost), 0.0) AS annual_spend_usd
            FROM employee_resource_dispatches d
            JOIN employees e ON e.employee_id = d.employee_id
            JOIN resources r ON r.resource_id = d.resource_id
            WHERE e.department = ?
              AND d.dispatched_at LIKE ?
            """,
            (department, f"{year_prefix}%"),
        ).fetchone()

        annual_budget = float(dept_row["annual_budget_usd"])
        annual_spend = float(spend_row["annual_spend_usd"])
        return {
            "department": dept_row["department"],
            "year": year,
            "annual_budget_usd": annual_budget,
            "annual_spend_usd": annual_spend,
            "remaining_budget_usd": round(annual_budget - annual_spend, 2),
        }
