"""Data source management — CRUD + JSON file persistence."""

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data_sources.json"


class DataSource(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    host: str = "localhost"
    port: int = 5432
    dbname: str = "synthea10"
    user: str = ""
    password: str = ""
    schema: str = "cdm_synthea"
    description: str = ""

    @property
    def conninfo(self) -> str:
        parts = f"host={self.host} port={self.port} dbname={self.dbname} user={self.user}"
        if self.password:
            parts += f" password={self.password}"
        return parts


class DataSourceStore(BaseModel):
    sources: list[DataSource] = []
    active_id: str = ""


def _load_store() -> DataSourceStore:
    if _CONFIG_PATH.exists():
        data = json.loads(_CONFIG_PATH.read_text())
        return DataSourceStore(**data)
    return DataSourceStore()


def _save_store(store: DataSourceStore) -> None:
    _CONFIG_PATH.write_text(store.model_dump_json(indent=2) + "\n")


def seed_from_env() -> DataSourceStore:
    """If no data_sources.json exists, seed with the current .env DB config."""
    if _CONFIG_PATH.exists():
        store = _load_store()
        if store.sources:
            return store

    from app.config import settings

    default = DataSource(
        name="Default (from .env)",
        host=settings.db.host,
        port=settings.db.port,
        dbname=settings.db.name,
        user=settings.db.user,
        password=settings.db.password,
        schema=settings.db.schema_,
        description="Auto-created from environment variables",
    )
    store = DataSourceStore(sources=[default], active_id=default.id)
    _save_store(store)
    log.info("Seeded data_sources.json with default source from .env")
    return store


def list_sources() -> list[DataSource]:
    return _load_store().sources


def get_source(source_id: str) -> DataSource | None:
    for s in _load_store().sources:
        if s.id == source_id:
            return s
    return None


def get_active_source() -> DataSource | None:
    store = _load_store()
    for s in store.sources:
        if s.id == store.active_id:
            return s
    if store.sources:
        return store.sources[0]
    return None


def get_active_source_id() -> str:
    store = _load_store()
    return store.active_id


def add_source(source: DataSource) -> DataSource:
    store = _load_store()
    store.sources.append(source)
    if not store.active_id:
        store.active_id = source.id
    _save_store(store)
    return source


def update_source(source_id: str, updates: dict[str, Any]) -> DataSource | None:
    store = _load_store()
    for i, s in enumerate(store.sources):
        if s.id == source_id:
            data = s.model_dump()
            data.update({k: v for k, v in updates.items() if v is not None})
            data["id"] = source_id  # prevent id change
            store.sources[i] = DataSource(**data)
            _save_store(store)
            return store.sources[i]
    return None


def delete_source(source_id: str) -> bool:
    store = _load_store()
    original_len = len(store.sources)
    store.sources = [s for s in store.sources if s.id != source_id]
    if len(store.sources) == original_len:
        return False
    if store.active_id == source_id:
        store.active_id = store.sources[0].id if store.sources else ""
    _save_store(store)
    return True


def set_active_source_id(source_id: str) -> bool:
    store = _load_store()
    if not any(s.id == source_id for s in store.sources):
        return False
    store.active_id = source_id
    _save_store(store)
    return True
