import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _build_service(tmp_path: Path):
    """Create an isolated seeded SQLite database and return EquipmentService."""
    from it_equipment_handler.db import SqliteEquipmentRepository, create_connection, init_schema
    from it_equipment_handler.seed import seed_database
    from it_equipment_handler.service import EquipmentService

    db_path = tmp_path / "test_remaining.db"
    conn = create_connection(db_path)
    init_schema(conn)
    seed_database(conn)
    return EquipmentService(SqliteEquipmentRepository(conn)), conn


def test_get_policy_limits_known_and_unknown_roles(tmp_path: Path) -> None:
    """Verify get_policy_limits returns structured rules, clauses, and handles unknown roles."""
    service, conn = _build_service(tmp_path)
    try:
        eng = service.get_policy_limits("engineer")
        assert eng["found"] is True
        assert eng["role"] == "engineer"
        assert len(eng["structured_policies"]) >= 3
        assert len(eng["policy_clauses"]) >= 5
        assert any("E901" in clause["rule_text"] for clause in eng["policy_clauses"])

        unknown = service.get_policy_limits("astronaut")
        assert unknown["found"] is False
        assert unknown["structured_policies"] == []
        assert "not found" in unknown["message"].lower()

        with pytest.raises(ValueError):
            service.get_policy_limits("  ")
    finally:
        conn.close()


def test_lookup_resource_aliases_dependencies_and_unknown(tmp_path: Path) -> None:
    """Verify lookup_resource resolves aliases, dependencies, and unknown queries."""
    service, conn = _build_service(tmp_path)
    try:
        mon = service.lookup_resource("dell 27")
        assert mon["found"] is True
        assert mon["primary_match"]["resource_id"] == "RES-MON-27"
        assert mon["primary_match"]["category"] == "monitor"

        laptop = service.lookup_resource("laptop")
        assert laptop["found"] is True
        assert laptop["primary_match"]["resource_id"] == "RES-LAP-STD"

        pro = service.lookup_resource("macbook pro")
        assert pro["found"] is True
        assert pro["primary_match"]["resource_id"] == "RES-LAP-PRO"

        dock = service.lookup_resource("docking station")
        assert dock["found"] is True
        assert dock["primary_match"]["requires_category"] == "laptop"

        missing = service.lookup_resource("espresso machine")
        assert missing["found"] is False
        assert missing["matches"] == []

        with pytest.raises(ValueError):
            service.lookup_resource("")
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("employee_id", "item", "expected_eligible", "expected_status"),
    [
        ("E101", "laptop", True, "ELIGIBLE"),
        ("E102", "monitor", False, "INELIGIBLE_COOLDOWN"),
        ("E103", "laptop", False, "INELIGIBLE_ROLE"),
        ("E104", "monitor", False, "INELIGIBLE_WORK_MODE"),
        ("E105", "monitor", False, "INELIGIBLE_STATUS"),
        ("E102", "docking station", False, "MISSING_DEPENDENCY"),
        ("E110", "monitor", False, "OPEN_TICKET_EXISTS"),
        ("E101", "macbook pro", False, "REQUIRES_MANAGER_APPROVAL"),
        ("E107", "monitor", False, "INSUFFICIENT_DEPARTMENT_BUDGET"),
        ("E101", "espresso machine", False, "UNKNOWN_ITEM"),
        ("E999", "laptop", False, "EMPLOYEE_NOT_FOUND"),
    ],
)
def test_check_request_eligibility_cases(
    tmp_path: Path,
    employee_id: str,
    item: str,
    expected_eligible: bool,
    expected_status: str,
) -> None:
    """Verify deterministic eligibility checks across normal and edge cases."""
    service, conn = _build_service(tmp_path)
    try:
        result = service.check_request_eligibility(employee_id, item)
        assert result["eligible"] is expected_eligible
        assert result["status_code"] == expected_status
    finally:
        conn.close()


def test_flag_for_human_review_creates_ticket_and_validates_ids(tmp_path: Path) -> None:
    """Verify flag_for_human_review inserts an escalated ticket and validates IDs."""
    service, conn = _build_service(tmp_path)
    try:
        ticket = service.flag_for_human_review(
            employee_id="E107",
            request="Need a 27-inch monitor for home office",
            reason="Operations annual budget remaining ($350) is below item cost ($550).",
            reviewer_employee_id="E200",
        )
        assert ticket["status"] == "escalated"
        assert ticket["flag_for_human_review"] is True
        assert ticket["reviewer_employee_id"] == "E200"
        assert ticket["ticket_id"].startswith("TKT-")

        info = service.get_employee_info("E107")
        assert any(t["ticket_id"] == ticket["ticket_id"] for t in info["tickets"])

        with pytest.raises(ValueError):
            service.flag_for_human_review("E999", "Need laptop", "Missing", "E901")

        with pytest.raises(ValueError):
            service.flag_for_human_review("E107", "Need laptop", "Bad reviewer", "E999")
    finally:
        conn.close()


def test_finalize_request_decision_logs_and_auto_dispatches(tmp_path: Path) -> None:
    """Verify APPROVED logs and dispatches, DENIED logs E999 cleanly, and ESCALATED is rejected."""
    service, conn = _build_service(tmp_path)
    try:
        before_budget = service.get_employee_info("E101")["department_budget"]["remaining_budget_usd"]

        approved = service.finalize_request_decision(
            employee_id="E101",
            request_text="Replace 4-year-old laptop",
            decision="APPROVED",
            reason="Eligible for 24-month engineer laptop refresh.",
            resource_id="RES-LAP-STD",
        )
        assert approved["decision"] == "APPROVED"
        assert approved["dispatched"] is True
        assert approved["dispatch_id"] is not None

        after_info = service.get_employee_info("E101")
        assert after_info["department_budget"]["remaining_budget_usd"] == before_budget - 1400.0
        assert any(d["dispatch_id"] == approved["dispatch_id"] for d in after_info["dispatch_history"])

        denied = service.finalize_request_decision(
            employee_id="E999",
            request_text="Need a laptop",
            decision="DENIED",
            reason="Employee ID E999 not found in directory.",
        )
        assert denied["decision"] == "DENIED"
        assert denied["dispatched"] is False

        with pytest.raises(ValueError):
            service.finalize_request_decision("E107", "Need monitor", "ESCALATED", "Wrong table")
    finally:
        conn.close()


async def _exercise_mcp_tools(db_path: Path) -> tuple[list[str], dict, dict]:
    """Connect over stdio and verify all six MCP tools are registered and callable."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "it_equipment_handler.mcp_server"],
        env={"IT_EQUIPMENT_DB_PATH": str(db_path)},
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]

            elig_res = await session.call_tool(
                "check_request_eligibility",
                arguments={"employee_id": "E101", "item": "laptop"},
            )
            elig_payload = json.loads(elig_res.content[0].text)

            esc_res = await session.call_tool(
                "flag_for_human_review",
                arguments={
                    "employee_id": "E107",
                    "request": "Need monitor",
                    "reason": "Annual budget exhausted",
                    "reviewer_employee_id": "E200",
                },
            )
            esc_payload = json.loads(esc_res.content[0].text)
            return tool_names, elig_payload, esc_payload


def test_mcp_server_exposes_all_six_tools(tmp_path: Path) -> None:
    """Verify all six required and helper tools work through the MCP stdio server."""
    db_path = tmp_path / "mcp_all_tools.db"
    tool_names, elig_payload, esc_payload = asyncio.run(_exercise_mcp_tools(db_path))
    expected = {
        "get_employee_info",
        "get_policy_limits",
        "lookup_resource",
        "check_request_eligibility",
        "flag_for_human_review",
        "finalize_request_decision",
    }
    assert expected.issubset(set(tool_names))
    assert elig_payload["eligible"] is True
    assert esc_payload["status"] == "escalated"
    assert esc_payload["reviewer_employee_id"] == "E200"


def test_flag_for_human_review_blocks_duplicate_followup_request(tmp_path: Path) -> None:
    """Verify newly escalated tickets record category and trigger OPEN_TICKET_EXISTS on retries."""
    service, conn = _build_service(tmp_path)
    try:
        ticket = service.flag_for_human_review(
            employee_id="E107",
            request="Need a 27-inch monitor for home office",
            reason="Operations annual budget exhausted",
            reviewer_employee_id="E200",
        )
        assert ticket["category"] == "monitor"
        assert ticket["resource_id"] == "RES-MON-27"

        followup = service.check_request_eligibility("E107", "monitor")
        assert followup["eligible"] is False
        assert followup["status_code"] == "OPEN_TICKET_EXISTS"
    finally:
        conn.close()


def test_finalize_approved_without_resource_id_raises_value_error(tmp_path: Path) -> None:
    """Verify APPROVED decisions cannot silently skip auto-dispatch by omitting resource_id."""
    service, conn = _build_service(tmp_path)
    try:
        with pytest.raises(ValueError, match="resource_id"):
            service.finalize_request_decision(
                employee_id="E101",
                request_text="Replace laptop",
                decision="APPROVED",
                reason="Within policy",
                resource_id=None,
            )
    finally:
        conn.close()


def test_lookup_resource_escapes_like_wildcards(tmp_path: Path) -> None:
    """Verify SQL LIKE wildcard characters do not match all catalog rows."""
    service, conn = _build_service(tmp_path)
    try:
        assert service.lookup_resource("%")["found"] is False
        assert service.lookup_resource("_")["found"] is False
    finally:
        conn.close()


def test_check_request_eligibility_returns_applicable_clauses(tmp_path: Path) -> None:
    """Verify eligibility results include relevant natural-language policy clauses."""
    service, conn = _build_service(tmp_path)
    try:
        result = service.check_request_eligibility("E107", "monitor")
        topics = {c["topic"] for c in result["applicable_clauses"]}
        assert "annual_budget" in topics
        assert "reviewer_directory" in topics
    finally:
        conn.close()


def test_check_request_eligibility_enforces_min_tenure_months(tmp_path: Path) -> None:
    """Verify min_tenure_months policy gate returns INELIGIBLE_TENURE when unmet."""
    service, conn = _build_service(tmp_path)
    try:
        conn.execute("UPDATE structured_policies SET min_tenure_months = 120 WHERE policy_id = 'POL-ENG-LAP'")
        conn.commit()
        result = service.check_request_eligibility("E101", "laptop")
        assert result["eligible"] is False
        assert result["status_code"] == "INELIGIBLE_TENURE"
    finally:
        conn.close()
