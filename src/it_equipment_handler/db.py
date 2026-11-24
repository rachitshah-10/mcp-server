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

    def get_structured_policies(self, role: str) -> list[dict[str, Any]]:
        """Fetch deterministic policy rules for a given role."""
        ...

    def get_policy_clauses(self) -> list[dict[str, Any]]:
        """Fetch all natural-language policy clauses and reviewer routing rules."""
        ...

    def search_resources(self, query: str) -> list[dict[str, Any]]:
        """Filter catalog resources in SQL matching the query or its tokens."""
        ...

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        """Fetch a single resource record by ID."""
        ...

    def create_ticket(
        self,
        ticket_id: str,
        employee_id: str,
        request_text: str,
        category: str | None,
        resource_id: str | None,
        status: str,
        flag_for_human_review: bool,
        reviewer_employee_id: str,
        reason: str,
        created_at: str,
    ) -> dict[str, Any]:
        """Insert a ticket row and return the persisted record."""
        ...

    def create_log(
        self,
        log_id: str,
        employee_id: str,
        request_text: str,
        decision: str,
        reason: str,
        created_at: str,
    ) -> dict[str, Any]:
        """Insert a finalized APPROVED or DENIED audit log row."""
        ...

    def record_approval_and_dispatch(
        self,
        log_id: str,
        dispatch_id: str,
        employee_id: str,
        resource_id: str,
        serial_id: str,
        dispatched_at: str,
        request_text: str,
        reason: str,
        created_at: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically insert an active dispatch row and an APPROVED audit log row."""
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

        CREATE INDEX IF NOT EXISTS idx_employees_department ON employees(department);
        CREATE INDEX IF NOT EXISTS idx_dispatches_employee_date ON employee_resource_dispatches(employee_id, dispatched_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_employee ON tickets(employee_id);
        CREATE INDEX IF NOT EXISTS idx_policies_role ON structured_policies(role);
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

    def get_structured_policies(self, role: str) -> list[dict[str, Any]]:
        """Fetch deterministic policy rules for a given role."""
        rows = self._conn.execute(
            """
            SELECT
                policy_id,
                role,
                employment_type,
                work_mode,
                category,
                allowed,
                max_active_qty,
                refresh_months,
                max_auto_cost_usd,
                min_tenure_months
            FROM structured_policies
            WHERE role = ?
            ORDER BY policy_id
            """,
            (role,),
        ).fetchall()
        policies = []
        for row in rows:
            item = dict(row)
            item["allowed"] = bool(item["allowed"])
            policies.append(item)
        return policies

    def get_policy_clauses(self) -> list[dict[str, Any]]:
        """Fetch all natural-language policy clauses and reviewer routing rules."""
        rows = self._conn.execute(
            "SELECT clause_id, topic, rule_text, default_reviewer_role FROM policy_clauses ORDER BY clause_id"
        ).fetchall()
        return [dict(row) for row in rows]

    def search_resources(self, query: str) -> list[dict[str, Any]]:
        """Filter catalog resources in SQL with escaped LIKE terms and bounded result size."""
        query_lower = query.strip().lower()
        terms = [query_lower] + [t for t in query_lower.split() if len(t) >= 2]
        # Deduplicate and cap terms to prevent unbounded OR clauses on long inputs.
        unique_terms = list(dict.fromkeys(terms))[:8]
        clauses = " OR ".join(
            "(LOWER(resource_id) = ? OR LOWER(name) LIKE ? ESCAPE '\\' "
            "OR LOWER(category) LIKE ? ESCAPE '\\' OR LOWER(aliases) LIKE ? ESCAPE '\\')"
            for _ in unique_terms
        )
        params: list[str] = []
        for term in unique_terms:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like_term = f"%{escaped}%"
            params.extend([term, like_term, like_term, like_term])

        rows = self._conn.execute(
            f"""
            SELECT resource_id, name, category, tier, unit_cost, requires_category, aliases
            FROM resources
            WHERE {clauses}
            ORDER BY resource_id
            LIMIT 20
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        """Fetch a single resource record by ID."""
        row = self._conn.execute(
            "SELECT resource_id, name, category, tier, unit_cost, requires_category, aliases FROM resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def create_ticket(
        self,
        ticket_id: str,
        employee_id: str,
        request_text: str,
        category: str | None,
        resource_id: str | None,
        status: str,
        flag_for_human_review: bool,
        reviewer_employee_id: str,
        reason: str,
        created_at: str,
    ) -> dict[str, Any]:
        """Insert a ticket row and return the persisted record."""
        self._conn.execute(
            """
            INSERT INTO tickets (
                ticket_id, employee_id, request_text, category, resource_id, status,
                flag_for_human_review, reviewer_employee_id, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                employee_id,
                request_text,
                category,
                resource_id,
                status,
                1 if flag_for_human_review else 0,
                reviewer_employee_id,
                reason,
                created_at,
            ),
        )
        self._conn.commit()
        return {
            "ticket_id": ticket_id,
            "employee_id": employee_id,
            "request_text": request_text,
            "category": category,
            "resource_id": resource_id,
            "status": status,
            "flag_for_human_review": flag_for_human_review,
            "reviewer_employee_id": reviewer_employee_id,
            "reason": reason,
            "created_at": created_at,
        }

    def create_log(
        self,
        log_id: str,
        employee_id: str,
        request_text: str,
        decision: str,
        reason: str,
        created_at: str,
    ) -> dict[str, Any]:
        """Insert a finalized APPROVED or DENIED audit log row."""
        self._conn.execute(
            """
            INSERT INTO logs (
                log_id, employee_id, request_text, decision, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (log_id, employee_id, request_text, decision, reason, created_at),
        )
        self._conn.commit()
        return {
            "log_id": log_id,
            "employee_id": employee_id,
            "request_text": request_text,
            "decision": decision,
            "reason": reason,
            "created_at": created_at,
        }

    def record_approval_and_dispatch(
        self,
        log_id: str,
        dispatch_id: str,
        employee_id: str,
        resource_id: str,
        serial_id: str,
        dispatched_at: str,
        request_text: str,
        reason: str,
        created_at: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically insert an active dispatch row and an APPROVED audit log row."""
        # Use a single SQLite transaction so dispatch and audit log succeed or roll back together.
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO employee_resource_dispatches (
                    dispatch_id, employee_id, resource_id, serial_id, dispatched_at, returned_at, condition_status
                ) VALUES (?, ?, ?, ?, ?, NULL, 'active')
                """,
                (dispatch_id, employee_id, resource_id, serial_id, dispatched_at),
            )
            self._conn.execute(
                """
                INSERT INTO logs (
                    log_id, employee_id, request_text, decision, reason, created_at
                ) VALUES (?, ?, ?, 'APPROVED', ?, ?)
                """,
                (log_id, employee_id, request_text, reason, created_at),
            )

        dispatch_record = {
            "dispatch_id": dispatch_id,
            "employee_id": employee_id,
            "resource_id": resource_id,
            "serial_id": serial_id,
            "dispatched_at": dispatched_at,
            "returned_at": None,
            "condition_status": "active",
        }
        log_record = {
            "log_id": log_id,
            "employee_id": employee_id,
            "request_text": request_text,
            "decision": "APPROVED",
            "reason": reason,
            "created_at": created_at,
        }
        return log_record, dispatch_record
