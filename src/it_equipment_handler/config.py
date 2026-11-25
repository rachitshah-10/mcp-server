import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AZURE_ENDPOINT = "https://foundry-apps-01.services.ai.azure.com/openai/v1"
DEFAULT_AZURE_DEPLOYMENT = "gpt-6-astra"
DEFAULT_DB_PATH = ".local_equipment.db"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables and optional .env file."""

    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment: str
    input_cost_per_1m: float
    output_cost_per_1m: float
    db_path: Path


def load_dotenv_file(env_path: Path = Path(".env")) -> None:
    """Load KEY=VALUE pairs from a local .env file without overwriting existing env vars."""
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_settings(env_path: Path = Path(".env")) -> Settings:
    """Return Settings populated from .env and process environment variables."""
    load_dotenv_file(env_path)
    return Settings(
        azure_openai_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", DEFAULT_AZURE_ENDPOINT),
        azure_openai_api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
        azure_openai_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", DEFAULT_AZURE_DEPLOYMENT),
        input_cost_per_1m=float(os.environ.get("AZURE_OPENAI_INPUT_COST_PER_1M", "0.0")),
        output_cost_per_1m=float(os.environ.get("AZURE_OPENAI_OUTPUT_COST_PER_1M", "0.0")),
        db_path=Path(os.environ.get("IT_EQUIPMENT_DB_PATH", DEFAULT_DB_PATH)),
    )
