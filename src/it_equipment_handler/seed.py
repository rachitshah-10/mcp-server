import sqlite3
from datetime import date, timedelta


def _iso_days_ago(days: int, ref_date: date) -> str:
    """Return an ISO YYYY-MM-DD date string N days before ref_date."""
    return (ref_date - timedelta(days=days)).isoformat()


def seed_database(conn: sqlite3.Connection, ref_date: date | None = None) -> None:
    """Populate deterministic synthetic data if the database is empty."""
    existing = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    if existing > 0:
        return

    today = ref_date or date.today()
    current_year_jan = date(today.year, 1, 15).isoformat()
    current_year_feb = date(today.year, 2, 10).isoformat()
    prev_year_nov = date(today.year - 1, 11, 5).isoformat()

    departments = [
        ("Engineering", 25000.0),
        ("Operations", 2000.0),
        ("Design", 8000.0),
        ("IT", 10000.0),
        ("HR", 5000.0),
        ("Security", 5000.0),
    ]
    conn.executemany(
        "INSERT INTO departments (department, annual_budget_usd) VALUES (?, ?)",
        departments,
    )

    # Insert leads and managers before their direct reports to satisfy manager_id FK.
    employees = [
        ("E901", "Morgan Lee", "it_ops_lead", "IT", "fte", "hybrid", "active", _iso_days_ago(2000, today), None),
        ("E902", "Priya Nair", "hr_lead", "HR", "fte", "hybrid", "active", _iso_days_ago(1900, today), None),
        ("E903", "Devon Brooks", "infosec_lead", "Security", "fte", "hybrid", "active", _iso_days_ago(2100, today), None),
        ("E100", "Elena Vance", "manager", "Engineering", "fte", "wfh", "active", _iso_days_ago(2200, today), None),
        ("E200", "Marcus Cole", "manager", "Operations", "fte", "hybrid", "active", _iso_days_ago(1800, today), None),
        ("E101", "Alice Chen", "engineer", "Engineering", "fte", "wfh", "active", _iso_days_ago(1825, today), "E100"),
        ("E102", "Bob Patel", "standard_employee", "Engineering", "fte", "hybrid", "active", _iso_days_ago(900, today), "E100"),
        ("E103", "Carlos Diaz", "contractor", "Engineering", "contractor", "wfh", "active", _iso_days_ago(200, today), "E100"),
        ("E104", "Diana Kim", "standard_employee", "Design", "fte", "onsite", "active", _iso_days_ago(600, today), "E100"),
        ("E105", "Evan Wright", "standard_employee", "Engineering", "fte", "wfh", "offboarding", _iso_days_ago(1000, today), "E100"),
        ("E106", "Farah Ali", "engineer", "Engineering", "fte", "wfh", "active", _iso_days_ago(1100, today), "E100"),
        ("E107", "Gabe Torres", "engineer", "Operations", "fte", "wfh", "active", _iso_days_ago(1200, today), "E200"),
        ("E108", "Hannah Park", "standard_employee", "Engineering", "fte", "hybrid", "active", _iso_days_ago(800, today), "E100"),
        ("E109", "Ian Foster", "engineer", "Engineering", "fte", "wfh", "active", _iso_days_ago(950, today), "E100"),
        ("E110", "Julia Reyes", "standard_employee", "Engineering", "fte", "wfh", "active", _iso_days_ago(1300, today), "E100"),
    ]
    conn.executemany(
        """
        INSERT INTO employees (
            employee_id, name, role, department, employment_type, work_mode, status, hire_date, manager_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        employees,
    )

    resources = [
        ("RES-LAP-STD", 'Dell Latitude 14" Laptop', "laptop", "standard", 1400.0, None, "laptop,dell laptop,standard laptop,work laptop"),
        ("RES-LAP-PRO", 'MacBook Pro 16" Max', "laptop", "specialty", 2800.0, None, "macbook pro,specialty laptop,gpu workstation,workstation"),
        ("RES-MON-27", 'Dell 27" 4K Monitor', "monitor", "standard", 550.0, None, "monitor,dell 27,4k monitor,ultrasharp,display,second monitor"),
        ("RES-DOCK-TB", "CalDigit Thunderbolt Dock", "dock", "standard", 250.0, "laptop", "dock,docking station,thunderbolt dock,usb-c dock"),
        ("RES-HEAD-PRO", "Jabra Evolve2 Headset", "headset", "standard", 120.0, None, "headset,headphones,jabra"),
        ("RES-KBM-STD", "Logitech Wireless Keyboard & Mouse", "keyboard_mouse", "standard", 90.0, None, "keyboard,mouse,keyboard and mouse"),
        ("RES-CHR-ERG", "Herman Miller Ergonomic Chair", "ergonomic_chair", "specialty", 850.0, None, "ergonomic chair,chair,standing desk"),
    ]
    conn.executemany(
        """
        INSERT INTO resources (
            resource_id, name, category, tier, unit_cost, requires_category, aliases
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        resources,
    )

    dispatches = [
        # E101 received a standard laptop ~4 years ago (eligible for 24-month refresh).
        ("DSP-001", "E101", "RES-LAP-STD", "SN-LAP-101A", _iso_days_ago(1460, today), None, "active"),
        # E102 received a monitor 6 months ago (inside 36-month cooldown).
        ("DSP-002", "E102", "RES-MON-27", "SN-MON-102A", _iso_days_ago(180, today), None, "active"),
        # E106 received a laptop 14 months ago (used for conflicting-claim / damage test).
        ("DSP-003", "E106", "RES-LAP-STD", "SN-LAP-106A", _iso_days_ago(420, today), None, "active"),
        # Operations current-year dispatches ($1,400 + $250 = $1,650 of $2,000 budget, leaving $350).
        ("DSP-004", "E200", "RES-LAP-STD", "SN-LAP-200A", current_year_jan, None, "damaged"),
        ("DSP-005", "E107", "RES-DOCK-TB", "SN-DCK-107A", current_year_feb, None, "active"),
        # Prior-year Operations dispatch must NOT count toward current calendar-year spend.
        ("DSP-006", "E107", "RES-LAP-STD", "SN-LAP-107A", prev_year_nov, None, "active"),
        # E109 has an active laptop for the lost/stolen escalation scenario.
        ("DSP-007", "E109", "RES-LAP-STD", "SN-LAP-109A", _iso_days_ago(300, today), None, "active"),
    ]
    conn.executemany(
        """
        INSERT INTO employee_resource_dispatches (
            dispatch_id, employee_id, resource_id, serial_id, dispatched_at, returned_at, condition_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        dispatches,
    )

    structured_policies = [
        ("POL-ENG-LAP", "engineer", "fte", "any", "laptop", 1, 1, 24, 2000.0, 0),
        ("POL-ENG-MON", "engineer", "fte", "wfh,hybrid", "monitor", 1, 2, 36, 1200.0, 0),
        ("POL-ENG-DCK", "engineer", "fte", "wfh,hybrid", "dock", 1, 1, 36, 400.0, 0),
        ("POL-STD-LAP", "standard_employee", "fte", "any", "laptop", 1, 1, 36, 1800.0, 0),
        ("POL-STD-MON", "standard_employee", "fte", "wfh,hybrid", "monitor", 1, 1, 36, 800.0, 0),
        ("POL-STD-MON-ONSITE", "standard_employee", "fte", "onsite", "monitor", 0, 0, 0, 0.0, 0),
        ("POL-STD-HDS", "standard_employee", "fte", "any", "headset", 1, 1, 24, 200.0, 0),
        ("POL-MGR-LAP", "manager", "fte", "any", "laptop", 1, 1, 24, 2500.0, 0),
        ("POL-MGR-MON", "manager", "fte", "wfh,hybrid", "monitor", 1, 1, 36, 1000.0, 0),
        ("POL-CTR-ALL", "contractor", "contractor", "any", "any", 0, 0, 0, 0.0, 0),
    ]
    conn.executemany(
        """
        INSERT INTO structured_policies (
            policy_id, role, employment_type, work_mode, category, allowed,
            max_active_qty, refresh_months, max_auto_cost_usd, min_tenure_months
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        structured_policies,
    )

    policy_clauses = [
        ("CLS-REV-DIR", "reviewer_directory", "Standard reviewers: IT-Ops Lead = E901, HR Accommodations Lead = E902, InfoSec Lead = E903. Budget and specialty cost overrides route to the employee's manager_id.", None),
        ("CLS-OFFBOARD", "lifecycle_freeze", "Employees in notice_period or offboarding status are ineligible for any new hardware dispatches and must be denied.", None),
        ("CLS-DAMAGED", "hardware_damage", "Hardware damage, defects, or unverified diagnostic claims (e.g. battery health) inside a refresh window must be escalated to IT-Ops Lead (E901).", "it_ops_lead"),
        ("CLS-LOST", "lost_or_stolen", "Lost or stolen equipment replacements require InfoSec verification regardless of refresh window; escalate to InfoSec Lead (E903).", "infosec_lead"),
        ("CLS-ERGO", "ergonomic_medical", "Ergonomic or medical accommodations (chairs, vertical mice, standing desks) bypass standard limits and must be escalated to HR Accommodations Lead (E902).", "hr_lead"),
        ("CLS-BUDGET", "annual_budget", "If a request is otherwise eligible and reasonable, but the department's remaining annual budget is below unit_cost, escalate to the employee's manager_id.", "manager"),
        ("CLS-DUP-TKT", "duplicate_tickets", "If an employee already has an open or escalated ticket for the same category, do not auto-approve; escalate to IT-Ops Lead (E901).", "it_ops_lead"),
        ("CLS-CONFLICT", "conflicting_facts", "If the employee's stated equipment age or history contradicts dispatch records, do not guess; escalate to IT-Ops Lead (E901).", "it_ops_lead"),
    ]
    conn.executemany(
        """
        INSERT INTO policy_clauses (
            clause_id, topic, rule_text, default_reviewer_role
        ) VALUES (?, ?, ?, ?)
        """,
        policy_clauses,
    )

    tickets = [
        (
            "TKT-001",
            "E110",
            "Need a new 27-inch monitor for home office",
            "monitor",
            "RES-MON-27",
            "open",
            1,
            "E901",
            "Pending initial hardware availability check",
            _iso_days_ago(3, today),
        ),
    ]
    conn.executemany(
        """
        INSERT INTO tickets (
            ticket_id, employee_id, request_text, category, resource_id, status,
            flag_for_human_review, reviewer_employee_id, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tickets,
    )
    conn.commit()
