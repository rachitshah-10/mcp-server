import argparse
import asyncio
import tempfile
from pathlib import Path

from it_equipment_handler.agent import EquipmentRequestAgent, format_agent_result
from it_equipment_handler.config import load_settings
from it_equipment_handler.llm_client import AzureFoundryLLMClient

DEMO_SCENARIOS: list[tuple[str, str, str]] = [
    ("approve_laptop", "E101", "My laptop is 4 years old and slow, can I get a standard replacement?"),
    ("deny_contractor", "E103", "I would like a new Dell laptop for my work."),
    ("escalate_budget", "E107", "I work from home and need a Dell 27-inch monitor."),
    ("escalate_damage_conflict", "E106", "My laptop is 4 years old and the screen cracked yesterday, I need a replacement."),
    ("escalate_ergonomic", "E108", "I have been having back pain and need an ergonomic chair for my home office."),
]


async def run_demo(selected_name: str | None = None) -> None:
    """Execute demo scenarios against the live Azure Foundry model and print full traces."""
    settings = load_settings()
    llm_client = AzureFoundryLLMClient(
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        deployment_name=settings.azure_openai_deployment,
    )

    scenarios = [
        item for item in DEMO_SCENARIOS
        if selected_name is None or item[0] == selected_name
    ]
    if not scenarios:
        valid = ", ".join(name for name, _, _ in DEMO_SCENARIOS)
        raise ValueError(f"Unknown scenario '{selected_name}'. Valid options: {valid}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "demo_equipment.db"
        agent = EquipmentRequestAgent(
            llm_client=llm_client,
            db_path=db_path,
            input_cost_per_1m=settings.input_cost_per_1m,
            output_cost_per_1m=settings.output_cost_per_1m,
        )
        for name, emp_id, req_text in scenarios:
            print(f"\n{'=' * 60}\nScenario: {name}\n{'=' * 60}")
            result = await agent.handle_request(emp_id, req_text)
            print(format_agent_result(result))


def main() -> None:
    """Parse CLI arguments and run the ReAct agent demo suite."""
    parser = argparse.ArgumentParser(description="Run IT Equipment Request ReAct Agent demo scenarios.")
    parser.add_argument("--scenario", default=None, help="Optional single scenario name to run.")
    args = parser.parse_args()
    asyncio.run(run_demo(args.scenario))


if __name__ == "__main__":
    main()
