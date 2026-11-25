import asyncio
import json
import types
from pathlib import Path

import pytest


def _build_service(tmp_path: Path):
    """Create an isolated seeded SQLite database and return (service, repo, conn)."""
    from it_equipment_handler.db import SqliteEquipmentRepository, create_connection, init_schema
    from it_equipment_handler.seed import seed_database
    from it_equipment_handler.service import EquipmentService

    conn = create_connection(tmp_path / "edge_cases.db")
    init_schema(conn)
    seed_database(conn)
    repo = SqliteEquipmentRepository(conn)
    return EquipmentService(repo), repo, conn


def test_service_resource_scoring_and_policy_fallbacks(tmp_path: Path) -> None:
    """Verify exact ID, name-only substring, missing role policy, and implicit work-mode denial."""
    service, _, conn = _build_service(tmp_path)
    try:
        by_id = service.lookup_resource("RES-MON-27")
        assert by_id["found"] is True
        assert by_id["primary_match"]["resource_id"] == "RES-MON-27"

        by_name_part = service.lookup_resource("CalDigit")
        assert by_name_part["found"] is True
        assert by_name_part["primary_match"]["resource_id"] == "RES-DOCK-TB"

        no_policy = service.check_request_eligibility("E101", "ergonomic chair")
        assert no_policy["eligible"] is False
        assert no_policy["status_code"] == "INELIGIBLE_ROLE"

        no_mode_match = service.check_request_eligibility("E104", "dock")
        assert no_mode_match["eligible"] is False
        assert no_mode_match["status_code"] == "INELIGIBLE_WORK_MODE"
    finally:
        conn.close()


def test_service_flag_and_finalize_resource_validation(tmp_path: Path) -> None:
    """Verify explicit resource_id handling on flag_for_human_review and finalize_request_decision."""
    service, _, conn = _build_service(tmp_path)
    try:
        ticket = service.flag_for_human_review(
            employee_id="E107",
            request="Need display",
            reason="Budget review",
            reviewer_employee_id="E200",
            resource_id="RES-MON-27",
        )
        assert ticket["category"] == "monitor"
        assert ticket["resource_id"] == "RES-MON-27"

        with pytest.raises(ValueError, match="does not exist"):
            service.flag_for_human_review(
                employee_id="E107",
                request="Need display",
                reason="Invalid resource",
                reviewer_employee_id="E200",
                resource_id="RES-BAD",
            )

        with pytest.raises(ValueError, match="nonexistent employee"):
            service.finalize_request_decision("E999", "Laptop", "APPROVED", "Bad emp", "RES-LAP-STD")

        with pytest.raises(ValueError, match="does not exist"):
            service.finalize_request_decision("E101", "Laptop", "APPROVED", "Bad res", "RES-BAD")
    finally:
        conn.close()


def test_db_unknown_department_and_seed_idempotency(tmp_path: Path) -> None:
    """Verify unknown department budget returns None and repeated seeding is a no-op."""
    from it_equipment_handler.seed import seed_database

    _, repo, conn = _build_service(tmp_path)
    try:
        assert repo.get_department_annual_budget("NonExistentDept", 2026) is None
        before = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        seed_database(conn)
        after = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        assert after == before
    finally:
        conn.close()


def test_config_loads_dotenv_without_overwriting_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify load_settings reads .env values while preserving existing environment variables."""
    from it_equipment_handler.config import load_dotenv_file, load_settings

    # Missing file is ignored cleanly.
    load_dotenv_file(tmp_path / "missing.env")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment line\n\n"
        "AZURE_OPENAI_API_KEY='from-file-key'\n"
        "AZURE_OPENAI_DEPLOYMENT=gpt-6-astra\n"
        "AZURE_OPENAI_INPUT_COST_PER_1M=1.5\n"
        "AZURE_OPENAI_OUTPUT_COST_PER_1M=6.0\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "preset-deployment")
    monkeypatch.delenv("AZURE_OPENAI_INPUT_COST_PER_1M", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_OUTPUT_COST_PER_1M", raising=False)

    settings = load_settings(env_file)
    assert settings.azure_openai_api_key == "from-file-key"
    assert settings.azure_openai_deployment == "preset-deployment"
    assert settings.input_cost_per_1m == 1.5
    assert settings.output_cost_per_1m == 6.0


def test_llm_client_validation_and_response_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify AzureFoundryLLMClient validates API key and extracts both output_text and fallback blocks."""
    import openai

    from it_equipment_handler.llm_client import AzureFoundryLLMClient

    with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
        AzureFoundryLLMClient("https://example.test/v1", "   ", "gpt-6-astra")

    responses_queue = [
        types.SimpleNamespace(
            output_text="  direct text  ",
            output=[],
            usage=types.SimpleNamespace(input_tokens=12, output_tokens=5),
        ),
        types.SimpleNamespace(
            output_text="",
            output=[
                types.SimpleNamespace(
                    content=[types.SimpleNamespace(text="fallback block text")]
                )
            ],
            usage=types.SimpleNamespace(input_tokens=20, output_tokens=8),
        ),
    ]

    class StubOpenAI:
        """Minimal stub mimicking OpenAI().responses.create."""

        def __init__(self, base_url: str, api_key: str) -> None:
            """Store init params and attach responses namespace."""
            self.base_url = base_url
            self.api_key = api_key
            self.responses = types.SimpleNamespace(create=self._create)

        def _create(self, model: str, input: str):
            """Pop the next scripted stub response."""
            return responses_queue.pop(0)

    monkeypatch.setattr(openai, "OpenAI", StubOpenAI)
    client = AzureFoundryLLMClient("https://example.test/v1", "test-key", "gpt-6-astra")

    first = client.generate("hello")
    assert first.text == "direct text"
    assert first.input_tokens == 12
    assert first.output_tokens == 5

    second = client.generate("hello again")
    assert second.text == "fallback block text"
    assert second.input_tokens == 20
    assert second.output_tokens == 8


def test_agent_helpers_and_mcp_client_guards(tmp_path: Path) -> None:
    """Verify JSON fence parsing, formatting, missing-action guard, and MCPToolClient session guards."""
    from it_equipment_handler.agent import (
        AgentResult,
        EquipmentRequestAgent,
        ReActStep,
        _parse_json_object,
        format_agent_result,
    )
    from it_equipment_handler.llm_client import LLMResponse
    from it_equipment_handler.mcp_client import MCPToolClient

    assert _parse_json_object('```json\n{"decision": "DENIED"}\n```') == {"decision": "DENIED"}
    with pytest.raises(ValueError, match="JSON object"):
        _parse_json_object("plain text without braces")

    client = MCPToolClient(tmp_path / "unused.db")
    with pytest.raises(RuntimeError, match="async with"):
        asyncio.run(client.list_tools())
    with pytest.raises(RuntimeError, match="async with"):
        asyncio.run(client.call_tool("get_employee_info", {"employee_id": "E101"}))

    sample = AgentResult(
        employee_id="E101",
        request_text="Need laptop",
        decision="APPROVED",
        resource_id="RES-LAP-STD",
        reason="Eligible",
        final_response="Approved!",
        trace=[ReActStep("Think", "get_employee_info", {"employee_id": "E101"}, "{}")],
        reflection={"status": "CONFIRMED", "critique": "Matches data."},
        telemetry={
            "llm_calls": 2,
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "latency_ms": 50.0,
            "estimated_cost_usd": 0.001,
        },
    )
    formatted = format_agent_result(sample)
    assert "ReAct Trace" in formatted
    assert "Reflection" in formatted
    assert "Est. Cost: $0.001000" in formatted

    class BadTurnLLM:
        """Stub LLM returning a JSON object missing both decision and action."""

        def generate(self, prompt: str) -> LLMResponse:
            """Return malformed ReAct JSON."""
            return LLMResponse(text='{"thought": "oops"}', input_tokens=10, output_tokens=5)

    agent = EquipmentRequestAgent(llm_client=BadTurnLLM(), db_path=tmp_path / "bad_turn.db")
    with pytest.raises(ValueError, match="decision"):
        asyncio.run(agent.handle_request("E101", "Need laptop"))


def test_mcp_server_tool_wrappers_in_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify all six mcp_server tool wrapper functions directly in-process."""
    from it_equipment_handler import mcp_server

    monkeypatch.setenv("IT_EQUIPMENT_DB_PATH", str(tmp_path / "inproc_mcp.db"))

    emp = json.loads(mcp_server.get_employee_info("E101"))
    assert emp["found"] is True

    pol = json.loads(mcp_server.get_policy_limits("engineer"))
    assert pol["found"] is True

    res = json.loads(mcp_server.lookup_resource("monitor"))
    assert res["found"] is True

    elig = json.loads(mcp_server.check_request_eligibility("E101", "laptop"))
    assert elig["eligible"] is True

    esc = json.loads(mcp_server.flag_for_human_review("E107", "Need monitor", "Budget", "E200"))
    assert esc["status"] == "escalated"

    fin = json.loads(
        mcp_server.finalize_request_decision("E101", "Need laptop", "APPROVED", "Eligible", "RES-LAP-STD")
    )
    assert fin["dispatched"] is True
