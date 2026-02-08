import logging
from typing import Any

log = logging.getLogger(__name__)

_catalog_text: str = ""


def build_catalog_text(concepts: list[dict[str, Any]]) -> str:
    """Format the concept list into a text block for the system prompt."""
    # Deduplicate by concept_id, keeping first occurrence
    seen: dict[int, dict[str, Any]] = {}
    for c in concepts:
        cid = c["concept_id"]
        if cid and cid not in seen:
            seen[cid] = c

    # Group by domain
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for c in seen.values():
        domain = c.get("domain_id", "Other")
        by_domain.setdefault(domain, []).append(c)

    lines = ["# Concept Catalog (concepts actually present in clinical data)\n"]
    for domain in sorted(by_domain):
        items = sorted(by_domain[domain], key=lambda x: x["concept_name"])
        lines.append(f"\n## {domain} ({len(items)} concepts)")
        for item in items:
            lines.append(f"- {item['concept_id']}: {item['concept_name']} [{item.get('vocabulary_id', '')}]")

    text = "\n".join(lines)
    log.info("Concept catalog: %d unique concepts, %d chars", len(seen), len(text))
    return text


def set_catalog(text: str) -> None:
    global _catalog_text
    _catalog_text = text


def get_catalog() -> str:
    return _catalog_text
