import logging

from openai import AsyncAzureOpenAI

from app.config import settings

log = logging.getLogger(__name__)

_client: AsyncAzureOpenAI | None = None
_deployment: str = settings.azure_openai.deployment

# Deployments actually provisioned on this Azure OpenAI endpoint.
AVAILABLE_MODELS = [
    "gpt-4o-mini",
    "gpt-5-mini",
    "gpt-5.2",
    "model-router",
]

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


async def chat(system_prompt: str, user_message: str) -> str:
    """Send a chat completion request and return the assistant text."""
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
    return resp.choices[0].message.content or ""
