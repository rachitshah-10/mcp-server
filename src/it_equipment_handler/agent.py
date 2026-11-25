import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from it_equipment_handler.llm_client import LLMClient
from it_equipment_handler.mcp_client import MCPToolClient

REACT_SYSTEM_GUIDANCE = """You are an internal IT Equipment Request Agent following the ReAct pattern.
You MUST investigate requests using the available MCP tools before deciding. Never guess policy limits, dates, or budgets.

Decision rules:
- APPROVED: Eligible per policy, within auto-approval cost cap, within remaining annual department budget, no duplicate open ticket, and no conflicting/exception claims.
- DENIED: Nonexistent employee, contractor, offboarding/notice_period, onsite employee requesting home monitor/dock, missing dependency, or routine request inside cooldown with no damage/exception claimed.
- ESCALATED: Ambiguous item, damaged/defective hardware or unverified diagnostic claims (escalate to IT-Ops E901), lost/stolen device (escalate to InfoSec E903), ergonomic/medical request (escalate to HR E902), specialty/high-cost item or exhausted annual department budget (escalate to employee's manager_id), existing open ticket conflict (E901), or facts contradicting DB records (E901).
- Whenever you choose ESCALATED, you MUST call flag_for_human_review with the appropriate reviewer_employee_id BEFORE returning your draft decision.
- Do NOT call finalize_request_decision yourself; the system records APPROVED/DENIED decisions automatically after Reflection.

Respond ONLY with a single valid JSON object (no markdown fences) in one of two shapes:
1) To call a tool:
{"thought": "...", "action": "<tool_name>", "action_input": {...}}
2) To submit your draft decision once investigation (and any escalation) is complete:
{"thought": "...", "decision": "APPROVED" | "DENIED" | "ESCALATED", "resource_id": "<RES-... or null>", "reason": "...", "draft_response": "..."}
"""

REFLECTION_GUIDANCE = """You are reviewing your own drafted IT equipment decision before sending it to the employee.
Check:
1. Does the decision accurately reflect what the MCP tool observations returned?
2. Does the draft avoid promising anything unconfirmed by the data?
3. If the tool returned ineligible/cooldown/budget/conflict, did you avoid wrongly approving?

Respond ONLY with a single valid JSON object (no markdown fences):
{
  "status": "CONFIRMED" | "REVISED",
  "critique": "Brief explanation of what you confirmed or corrected.",
  "decision": "APPROVED" | "DENIED" | "ESCALATED",
  "resource_id": "<RES-... or null>",
  "reviewer_employee_id": "<E... or null if not escalating>",
  "reason": "Concise policy reason.",
  "final_response": "Polite, accurate employee-facing response."
}
"""


@dataclass(frozen=True)
class ReActStep:
    """Single Thought -> Action -> Observation step in an agent trace."""

    thought: str
    action: str
    action_input: dict[str, Any]
    observation: str


@dataclass(frozen=True)
class AgentResult:
    """Complete outcome of handling one equipment request, including trace and telemetry."""

    employee_id: str
    request_text: str
    decision: str
    resource_id: str | None
    reason: str
    final_response: str
    trace: list[ReActStep]
    reflection: dict[str, Any]
    telemetry: dict[str, Any]


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    """Parse a JSON object from LLM output, stripping optional markdown code fences."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"LLM output did not contain a JSON object: {raw_text!r}")
    return json.loads(cleaned[start : end + 1])


def _build_react_prompt(
    employee_id: str,
    request_text: str,
    tools: list[dict[str, Any]],
    trace: list[ReActStep],
) -> str:
    """Assemble the ReAct prompt with available tools and prior observations."""
    # Exclude finalize_request_decision from the ReAct tool list since it runs post-reflection.
    visible_tools = [t for t in tools if t["name"] != "finalize_request_decision"]
    lines = [
        REACT_SYSTEM_GUIDANCE,
        f"Available tools:\n{json.dumps(visible_tools, indent=2)}",
        f"Employee ID: {employee_id}",
        f"Request: {request_text}",
    ]
    if trace:
        lines.append("History so far:")
        for idx, step in enumerate(trace, start=1):
            lines.append(f"Step {idx} Thought: {step.thought}")
            lines.append(f"Step {idx} Action: {step.action}({json.dumps(step.action_input)})")
            lines.append(f"Step {idx} Observation: {step.observation}")
    lines.append("Provide your next JSON object:")
    return "\n\n".join(lines)


def _build_reflection_prompt(
    employee_id: str,
    request_text: str,
    trace: list[ReActStep],
    draft: dict[str, Any],
) -> str:
    """Assemble the Reflection prompt comparing the draft against tool observations."""
    trace_lines = []
    for idx, step in enumerate(trace, start=1):
        trace_lines.append(
            f"Step {idx}: Thought={step.thought} | Action={step.action}({json.dumps(step.action_input)}) "
            f"| Observation={step.observation}"
        )
    return "\n\n".join(
        [
            REFLECTION_GUIDANCE,
            f"Employee ID: {employee_id}\nRequest: {request_text}",
            "Tool Observations:\n" + ("\n".join(trace_lines) or "(none)"),
            f"Draft Decision:\n{json.dumps(draft, indent=2)}",
        ]
    )


def format_agent_result(result: AgentResult) -> str:
    """Format an AgentResult into a readable ReAct, Reflection, and Telemetry report."""
    lines = [
        f"=== Request for {result.employee_id} ===",
        f"Input: {result.request_text}",
        "",
        "--- ReAct Trace ---",
    ]
    for idx, step in enumerate(result.trace, start=1):
        lines.append(f"[Step {idx}] Thought: {step.thought}")
        lines.append(f"[Step {idx}] Action: {step.action}({json.dumps(step.action_input)})")
        lines.append(f"[Step {idx}] Observation: {step.observation}")
    lines.extend(
        [
            "",
            "--- Reflection ---",
            f"Status: {result.reflection.get('status')}",
            f"Critique: {result.reflection.get('critique')}",
            "",
            "--- Final Decision ---",
            f"Decision: {result.decision}",
            f"Reason: {result.reason}",
            f"Response: {result.final_response}",
            "",
            "--- Telemetry ---",
            (
                f"LLM calls: {result.telemetry['llm_calls']} | "
                f"Tokens: {result.telemetry['input_tokens']} in / {result.telemetry['output_tokens']} out "
                f"({result.telemetry['total_tokens']} total) | "
                f"Latency: {result.telemetry['latency_ms']:.1f} ms | "
                f"Est. Cost: ${result.telemetry['estimated_cost_usd']:.6f}"
            ),
        ]
    )
    return "\n".join(lines)


class EquipmentRequestAgent:
    """ReAct + Reflection agent orchestrating MCP tools and per-request telemetry."""

    def __init__(
        self,
        llm_client: LLMClient,
        db_path: str | Path,
        max_steps: int = 8,
        input_cost_per_1m: float = 0.0,
        output_cost_per_1m: float = 0.0,
    ) -> None:
        """Configure the agent with an LLM client, target DB path, and token cost rates."""
        self._llm = llm_client
        self._db_path = Path(db_path)
        self._max_steps = max_steps
        self._input_cost_per_1m = input_cost_per_1m
        self._output_cost_per_1m = output_cost_per_1m

    def _compute_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Compute estimated USD cost from configured per-1M token rates."""
        in_cost = (input_tokens / 1_000_000) * self._input_cost_per_1m
        out_cost = (output_tokens / 1_000_000) * self._output_cost_per_1m
        return round(in_cost + out_cost, 8)

    async def handle_request(self, employee_id: str, request_text: str) -> AgentResult:
        """Run ReAct investigation, Reflection self-check, and post-reflection DB side effects."""
        norm_emp_id = employee_id.strip()
        if not norm_emp_id:
            raise ValueError("employee_id is mandatory and cannot be blank.")
        norm_req = request_text.strip()
        if not norm_req:
            raise ValueError("request_text is mandatory and cannot be blank.")

        start_time = time.perf_counter()
        llm_calls = 0
        total_in = 0
        total_out = 0
        trace: list[ReActStep] = []
        draft: dict[str, Any] | None = None

        async with MCPToolClient(self._db_path) as mcp_client:
            tools = await mcp_client.list_tools()

            for _ in range(self._max_steps + 1):
                prompt = _build_react_prompt(norm_emp_id, norm_req, tools, trace)
                llm_resp = self._llm.generate(prompt)
                llm_calls += 1
                total_in += llm_resp.input_tokens
                total_out += llm_resp.output_tokens

                parsed = _parse_json_object(llm_resp.text)
                if "decision" in parsed:
                    draft = parsed
                    break

                if len(trace) >= self._max_steps:
                    raise RuntimeError(f"Agent exceeded max_steps ({self._max_steps}) without reaching a decision.")

                if "action" not in parsed:
                    raise ValueError(f"LLM ReAct output must contain 'decision' or 'action': {parsed!r}")

                action = str(parsed["action"])
                action_input = parsed.get("action_input") or {}
                thought = str(parsed.get("thought", ""))
                if action == "finalize_request_decision":
                    # Block premature DB writes before the Reflection step runs.
                    observation = (
                        "Error: Do not call finalize_request_decision directly; "
                        "submit your draft decision JSON so Reflection can verify it first."
                    )
                else:
                    observation = await mcp_client.call_tool(action, action_input)
                trace.append(
                    ReActStep(
                        thought=thought,
                        action=action,
                        action_input=action_input,
                        observation=observation,
                    )
                )

            if draft is None:
                raise RuntimeError(f"Agent exceeded max_steps ({self._max_steps}) without reaching a decision.")

            reflection_prompt = _build_reflection_prompt(norm_emp_id, norm_req, trace, draft)
            refl_resp = self._llm.generate(reflection_prompt)
            llm_calls += 1
            total_in += refl_resp.input_tokens
            total_out += refl_resp.output_tokens
            reflection = _parse_json_object(refl_resp.text)

            final_decision = str(reflection["decision"]).upper()
            final_resource_id = reflection.get("resource_id")
            final_reason = str(reflection.get("reason", draft.get("reason", "")))
            final_response = str(reflection.get("final_response", draft.get("draft_response", "")))

            # Only finalized APPROVED/DENIED decisions write to logs (and auto-dispatch on APPROVED).
            if final_decision in ("APPROVED", "DENIED"):
                finalize_args: dict[str, Any] = {
                    "employee_id": norm_emp_id,
                    "request_text": norm_req,
                    "decision": final_decision,
                    "reason": final_reason,
                }
                if final_decision == "APPROVED":
                    finalize_args["resource_id"] = final_resource_id
                await mcp_client.call_tool("finalize_request_decision", finalize_args)
            elif final_decision == "ESCALATED" and not any(
                step.action == "flag_for_human_review" for step in trace
            ):
                # Ensure a review ticket is still created if Reflection flips a draft to ESCALATED.
                escalate_args: dict[str, Any] = {
                    "employee_id": norm_emp_id,
                    "request": norm_req,
                    "reason": final_reason,
                    "reviewer_employee_id": str(reflection.get("reviewer_employee_id") or "E901"),
                }
                if final_resource_id:
                    escalate_args["resource_id"] = final_resource_id
                await mcp_client.call_tool("flag_for_human_review", escalate_args)

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        telemetry = {
            "llm_calls": llm_calls,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "latency_ms": latency_ms,
            "estimated_cost_usd": self._compute_cost(total_in, total_out),
        }
        return AgentResult(
            employee_id=norm_emp_id,
            request_text=norm_req,
            decision=final_decision,
            resource_id=final_resource_id,
            reason=final_reason,
            final_response=final_response,
            trace=trace,
            reflection=reflection,
            telemetry=telemetry,
        )
