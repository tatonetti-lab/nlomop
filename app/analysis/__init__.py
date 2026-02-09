import importlib
import logging
from collections.abc import Callable

from app.analysis.models import AnalysisResult

log = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable] = {}

# All analysis module names — imported at bottom to auto-register
_MODULES = [
    "app.analysis.survival",
    "app.analysis.pre_post",
    "app.analysis.comparative",
    "app.analysis.odds_ratio",
    "app.analysis.correlation",
]


def register(name: str):
    """Decorator to register an analysis function."""

    def decorator(fn):
        _REGISTRY[name] = fn
        return fn

    return decorator


async def resolve_label(concept_ids: list[int], fallback: str = "Unknown") -> str:
    """Resolve a list of concept IDs into a short, readable group label via cheap LLM call."""
    from app import db, llm

    id_list = ", ".join(str(i) for i in concept_ids)
    sql = f"SELECT concept_name FROM cdm_synthea.concept WHERE concept_id IN ({id_list}) ORDER BY concept_id LIMIT 10"
    rows = await db.execute_query(sql)
    names = [r["concept_name"] for r in rows]
    if not names:
        return fallback
    if len(names) == 1:
        return names[0]
    # Use cheap LLM to summarize multiple concept names into a short group label
    try:
        prompt = (
            "Given these medical concept names, produce a SHORT group label (1-4 words). "
            "Return ONLY the label, nothing else.\n\n"
            + "\n".join(f"- {n}" for n in names)
        )
        label = await llm.quick_chat(prompt)
        # Strip quotes if the model wraps it
        return label.strip('"\'')
    except Exception:
        log.warning("Label generation failed, using first concept name")
        return names[0]


async def run_analysis(analysis_type: str, params: dict) -> AnalysisResult:
    """Dispatch to the registered analysis function."""
    fn = _REGISTRY.get(analysis_type)
    if not fn:
        raise ValueError(
            f"Unknown analysis type: {analysis_type}. "
            f"Available: {', '.join(sorted(_REGISTRY))}"
        )
    log.info("Running analysis: %s with params %s", analysis_type, params)
    return await fn(params)


# Auto-import modules to trigger @register decorators
for _mod in _MODULES:
    importlib.import_module(_mod)
