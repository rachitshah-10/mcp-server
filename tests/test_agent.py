import asyncio
import json
from pathlib import Path

import pytest


class FakeLLMClient:
    """Deterministic fake LLM client returning scripted JSON responses and token counts."""

    def __init__(self, responses: list[tuple[str, int, int]]) -> None:
        """Store scripted (text, input_tokens, output_tokens) turns."""
        self._responses = list(responses)
        self.calls: list[str] = []

    def generate(self, prompt: str):
        """Return the next scripted LLMResponse."""
        from it_equipment_handler.llm_client import LLMResponse

        self.calls.append(prompt)
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of scripted responses.")
        text, in_tok, out_tok = self._responses.pop(0)
        return LLMResponse(text=text, input_tokens=in_tok, output_tokens=out_tok)


def _query_counts(db_path: Path) -> tuple[int, int, int]:
    """Return row counts for (logs, tickets, dispatches) in the test SQLite database."""
    from it_equipment_handler.db import create_connection

    conn = create_connection(db_path)
    try:
        logs = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        tickets = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        dispatches = conn.execute("SELECT COUNT(*) FROM employee_resource_dispatches").fetchone()[0]
        return logs, tickets, dispatches
    finally:
        conn.close()


def test_agent_rejects_blank_employee_id_or_request(tmp_path: Path) -> None:
    """Verify missing employee_id or request_text fails fast with zero LLM calls."""
    from it_equipment_handler.agent import EquipmentRequestAgent

    llm = FakeLLMClient([])
    agent = EquipmentRequestAgent(llm_client=llm, db_path=tmp_path / "agent_invalid.db")

    with pytest.raises(ValueError, match="employee_id"):
        asyncio.run(agent.handle_request("", "I need a monitor"))

    with pytest.raises(ValueError, match="request_text"):
        asyncio.run(agent.handle_request("E101", "   "))

    assert len(llm.calls) == 0


def test_agent_approved_path_runs_react_reflection_dispatches_and_tracks_telemetry(tmp_path: Path) -> None:
    """Verify APPROVED path executes ReAct + Reflection, logs + dispatches, and computes telemetry."""
    from it_equipment_handler.agent import EquipmentRequestAgent

    db_path = tmp_path / "agent_approve.db"
    llm = FakeLLMClient(
        [
            (
                json.dumps({
                    "thought": "Look up E101 profile first.",
                    "action": "get_employee_info",
                    "action_input": {"employee_id": "E101"},
                }),
                100,
                30,
            ),
            (
                json.dumps({
                    "thought": "Check E101 eligibility for a standard laptop.",
                    "action": "check_request_eligibility",
                    "action_input": {"employee_id": "E101", "item": "laptop"},
                }),
                150,
                35,
            ),
            (
                json.dumps({
                    "thought": "E101 is eligible and Engineering budget covers $1,400.",
                    "decision": "APPROVED",
                    "resource_id": "RES-LAP-STD",
                    "reason": "4-year-old laptop exceeds 24-month engineer refresh policy.",
                    "draft_response": 'Your request for a Dell Latitude 14" Laptop is approved.',
                }),
                200,
                50,
            ),
            (
                json.dumps({
                    "status": "CONFIRMED",
                    "critique": "Draft accurately matches check_request_eligibility observation.",
                    "decision": "APPROVED",
                    "resource_id": "RES-LAP-STD",
                    "reason": "4-year-old laptop exceeds 24-month engineer refresh policy.",
                    "final_response": 'Your request for a Dell Latitude 14" Laptop is approved and dispatched.',
                }),
                250,
                45,
            ),
        ]
    )
    agent = EquipmentRequestAgent(
        llm_client=llm,
        db_path=db_path,
        input_cost_per_1m=2.0,
        output_cost_per_1m=8.0,
    )

    result = asyncio.run(agent.handle_request("E101", "My laptop is 4 years old and slow, can I get a replacement?"))

    assert result.decision == "APPROVED"
    assert len(result.trace) == 2
    assert result.trace[0].action == "get_employee_info"
    assert result.trace[1].action == "check_request_eligibility"
    assert result.reflection["status"] == "CONFIRMED"
    assert "approved" in result.final_response.lower()

    # Telemetry: 700 input tokens * $2/1M + 160 output tokens * $8/1M = $0.00268.
    assert result.telemetry["llm_calls"] == 4
    assert result.telemetry["input_tokens"] == 700
    assert result.telemetry["output_tokens"] == 160
    assert result.telemetry["total_tokens"] == 860
    assert result.telemetry["latency_ms"] >= 0
    assert result.telemetry["estimated_cost_usd"] == pytest.approx(0.00268, abs=1e-6)

    logs_count, _, dispatches_count = _query_counts(db_path)
    assert logs_count == 1
    assert dispatches_count == 8  # 7 seeded dispatches + 1 newly auto-dispatched laptop.


def test_agent_denied_path_for_unknown_employee_logs_without_dispatch(tmp_path: Path) -> None:
    """Verify nonexistent employee E999 is DENIED and logged without creating a ticket or dispatch."""
    from it_equipment_handler.agent import EquipmentRequestAgent

    db_path = tmp_path / "agent_deny.db"
    llm = FakeLLMClient(
        [
            (
                json.dumps({
                    "thought": "Look up E999 in employee directory.",
                    "action": "get_employee_info",
                    "action_input": {"employee_id": "E999"},
                }),
                90,
                25,
            ),
            (
                json.dumps({
                    "thought": "E999 does not exist, so deny the request.",
                    "decision": "DENIED",
                    "resource_id": None,
                    "reason": "Employee ID E999 was not found in the company directory.",
                    "draft_response": "Your request is denied because employee ID E999 was not found.",
                }),
                120,
                40,
            ),
            (
                json.dumps({
                    "status": "CONFIRMED",
                    "critique": "Confirmed against get_employee_info not_found result.",
                    "decision": "DENIED",
                    "resource_id": None,
                    "reason": "Employee ID E999 was not found in the company directory.",
                    "final_response": "Your request is denied because employee ID E999 was not found.",
                }),
                140,
                35,
            ),
        ]
    )
    agent = EquipmentRequestAgent(llm_client=llm, db_path=db_path)

    result = asyncio.run(agent.handle_request("E999", "Can I get a laptop?"))

    assert result.decision == "DENIED"
    logs_count, tickets_count, dispatches_count = _query_counts(db_path)
    assert logs_count == 1
    assert tickets_count == 1  # Only the 1 seeded ticket for E110.
    assert dispatches_count == 7


def test_agent_escalated_path_creates_ticket_and_skips_logs(tmp_path: Path) -> None:
    """Verify ESCALATED requests create a human-review ticket and do not write to logs."""
    from it_equipment_handler.agent import EquipmentRequestAgent

    db_path = tmp_path / "agent_escalate.db"
    llm = FakeLLMClient(
        [
            (
                json.dumps({
                    "thought": "Check E107 eligibility for a monitor.",
                    "action": "check_request_eligibility",
                    "action_input": {"employee_id": "E107", "item": "monitor"},
                }),
                110,
                30,
            ),
            (
                json.dumps({
                    "thought": "Operations budget is insufficient; escalate to manager E200.",
                    "action": "flag_for_human_review",
                    "action_input": {
                        "employee_id": "E107",
                        "request": "Need a 27-inch monitor",
                        "reason": "Operations remaining annual budget ($350) is below monitor cost ($550).",
                        "reviewer_employee_id": "E200",
                    },
                }),
                160,
                55,
            ),
            (
                json.dumps({
                    "thought": "Escalation ticket created for manager E200.",
                    "decision": "ESCALATED",
                    "resource_id": "RES-MON-27",
                    "reason": "Escalated to manager E200 due to insufficient Operations annual budget.",
                    "draft_response": "Your monitor request has been escalated to your manager (E200) for budget approval.",
                }),
                180,
                45,
            ),
            (
                json.dumps({
                    "status": "CONFIRMED",
                    "critique": "Confirmed: escalation ticket was created and assigned to E200.",
                    "decision": "ESCALATED",
                    "resource_id": "RES-MON-27",
                    "reason": "Escalated to manager E200 due to insufficient Operations annual budget.",
                    "final_response": "Your monitor request has been escalated to your manager (E200) for budget approval.",
                }),
                200,
                40,
            ),
        ]
    )
    agent = EquipmentRequestAgent(llm_client=llm, db_path=db_path)

    result = asyncio.run(agent.handle_request("E107", "Need a 27-inch monitor"))

    assert result.decision == "ESCALATED"
    logs_count, tickets_count, dispatches_count = _query_counts(db_path)
    assert logs_count == 0
    assert tickets_count == 2  # 1 seeded ticket + 1 newly escalated ticket.
    assert dispatches_count == 7


def test_agent_reflection_catches_and_revises_ungrounded_draft(tmp_path: Path) -> None:
    """Verify Reflection catches an ungrounded APPROVED draft and revises it to DENIED before side effects."""
    from it_equipment_handler.agent import EquipmentRequestAgent

    db_path = tmp_path / "agent_reflect.db"
    llm = FakeLLMClient(
        [
            (
                json.dumps({
                    "thought": "Check E102 eligibility for a monitor.",
                    "action": "check_request_eligibility",
                    "action_input": {"employee_id": "E102", "item": "monitor"},
                }),
                100,
                30,
            ),
            (
                json.dumps({
                    "thought": "Approve the monitor request.",
                    "decision": "APPROVED",
                    "resource_id": "RES-MON-27",
                    "reason": "Standard employee needs monitor.",
                    "draft_response": "Your monitor request is approved!",
                }),
                120,
                35,
            ),
            (
                json.dumps({
                    "status": "REVISED",
                    "critique": "Draft wrongly approved E102 despite INELIGIBLE_COOLDOWN (monitor issued 6 months ago).",
                    "decision": "DENIED",
                    "resource_id": None,
                    "reason": "Monitor was issued 6 months ago; 36-month refresh window not met.",
                    "final_response": "Your monitor request cannot be approved because your current monitor was issued 6 months ago (36-month refresh policy).",
                }),
                180,
                60,
            ),
        ]
    )
    agent = EquipmentRequestAgent(llm_client=llm, db_path=db_path)

    result = asyncio.run(agent.handle_request("E102", "Can I get a new monitor?"))

    assert result.reflection["status"] == "REVISED"
    assert result.decision == "DENIED"
    logs_count, _, dispatches_count = _query_counts(db_path)
    assert logs_count == 1
    assert dispatches_count == 7  # Reflection prevented an erroneous dispatch!


def test_agent_respects_max_steps_bound(tmp_path: Path) -> None:
    """Verify the ReAct loop terminates safely when max_steps is reached."""
    from it_equipment_handler.agent import EquipmentRequestAgent

    loop_turn = (
        json.dumps({
            "thought": "Check employee again.",
            "action": "get_employee_info",
            "action_input": {"employee_id": "E101"},
        }),
        50,
        20,
    )
    llm = FakeLLMClient([loop_turn, loop_turn, loop_turn])
    agent = EquipmentRequestAgent(llm_client=llm, db_path=tmp_path / "agent_max.db", max_steps=2)

    with pytest.raises(RuntimeError, match="max_steps"):
        asyncio.run(agent.handle_request("E101", "Need laptop"))
