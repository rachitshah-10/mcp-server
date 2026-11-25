from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    """Structured text and token-usage response returned by an LLM call."""

    text: str
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    """Minimal LLM interface so orchestration can swap live and fake models."""

    def generate(self, prompt: str) -> LLMResponse:
        """Send a prompt and return the model text with token usage counts."""
        ...


class AzureFoundryLLMClient:
    """OpenAI Responses API client targeting an Azure AI Foundry deployment."""

    def __init__(self, endpoint: str, api_key: str, deployment_name: str) -> None:
        """Initialize the OpenAI SDK client for Azure AI Foundry."""
        if not api_key.strip():
            raise ValueError("AZURE_OPENAI_API_KEY is required for live LLM calls.")
        from openai import OpenAI

        self._client = OpenAI(base_url=endpoint, api_key=api_key)
        self._deployment_name = deployment_name

    def generate(self, prompt: str) -> LLMResponse:
        """Call client.responses.create and return extracted text and token usage."""
        response = self._client.responses.create(
            model=self._deployment_name,
            input=prompt,
        )
        text = getattr(response, "output_text", None) or ""
        if not text and getattr(response, "output", None):
            # Fallback for SDK/response shapes where output_text is not populated directly.
            chunks: list[str] = []
            for item in response.output:
                for content in getattr(item, "content", []) or []:
                    part_text = getattr(content, "text", None)
                    if part_text:
                        chunks.append(part_text)
            text = "\n".join(chunks)

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return LLMResponse(text=text.strip(), input_tokens=input_tokens, output_tokens=output_tokens)
