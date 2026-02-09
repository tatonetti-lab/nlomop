import logging

from openai import AsyncAzureOpenAI

from app.config import settings

log = logging.getLogger(__name__)

_client: AsyncAzureOpenAI | None = None
_deployment: str = settings.azure_openai.deployment

# Deployments actually provisioned on this Azure OpenAI endpoint.
AVAILABLE_MODELS = [
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-5-mini",
    "gpt-5.2",
    "model-router",
]

# Cheap model for lightweight tasks (label generation, etc.)
UTILITY_MODEL = "gpt-4.1-mini"

# Models that require max_completion_tokens instead of max_tokens.
_USES_MAX_COMPLETION_TOKENS = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.1", "gpt-5.2",
                                "o1", "o1-mini", "o1-pro", "o3", "o3-mini", "o4-mini",
                                "model-router"}


def _needs_max_completion_tokens(deployment: str) -> bool:
    """Check if a deployment uses the newer max_completion_tokens parameter."""
    d = deployment.lower()
    for prefix in _USES_MAX_COMPLETION_TOKENS:
        if d == prefix or d.startswith(prefix + "-"):
            return True
    return False


def get_client() -> AsyncAzureOpenAI:
    global _client
    if _client is None:
        cfg = settings.azure_openai
        _client = AsyncAzureOpenAI(
            azure_endpoint=cfg.endpoint,
            api_key=cfg.api_key,
            api_version=cfg.api_version,
        )
    return _client


def get_deployment() -> str:
    return _deployment


def set_deployment(name: str) -> None:
    global _deployment
    _deployment = name
    log.info("Deployment changed to: %s", name)


async def chat(system_prompt: str, user_message: str) -> tuple[str, str]:
    """Send a chat completion request. Returns (text, finish_reason)."""
    client = get_client()

    newer_api = _needs_max_completion_tokens(_deployment)

    kwargs: dict = {
        "model": _deployment,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    if newer_api:
        # gpt-5 / o-series: max_completion_tokens, no temperature control
        kwargs["max_completion_tokens"] = 4096
    else:
        kwargs["max_tokens"] = 4096
        kwargs["temperature"] = 0.0

    resp = await client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    return choice.message.content or "", choice.finish_reason or "stop"


async def quick_chat(user_message: str, model: str = UTILITY_MODEL, max_tokens: int = 60) -> str:
    """Lightweight LLM call for simple tasks (labels, summaries). Uses cheap model."""
    client = get_client()
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return (resp.choices[0].message.content or "").strip()
