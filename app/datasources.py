"""Data source management — CRUD + JSON file persistence.

Passwords (db and SSH) are stored in the OS keychain via `keyring`,
never in data_sources.json.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import keyring
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data_sources.json"
_KEYRING_SERVICE = "nlomop"
_SECRET_FIELDS = ("password", "ssh_password")


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
    # SSH tunnel fields
    use_ssh: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_key_path: str = ""
    ssh_password: str = ""

    @property
    def conninfo(self) -> str:
        parts = f"host={self.host} port={self.port} dbname={self.dbname} user={self.user}"
        if self.password:
            parts += f" password={self.password}"
        return parts


class DataSourceStore(BaseModel):
    sources: list[DataSource] = []
    active_id: str = ""


# ── Keyring helpers ──


def _keyring_key(source_id: str, field: str) -> str:
    return f"{source_id}:{field}"


def _save_secret(source_id: str, field: str, value: str) -> None:
    key = _keyring_key(source_id, field)
    if value:
        keyring.set_password(_KEYRING_SERVICE, key, value)
    else:
        try:
            keyring.delete_password(_KEYRING_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass


def _load_secret(source_id: str, field: str) -> str:
    return keyring.get_password(_KEYRING_SERVICE, _keyring_key(source_id, field)) or ""


def _delete_secrets(source_id: str) -> None:
    for field in _SECRET_FIELDS:
        try:
            keyring.delete_password(_KEYRING_SERVICE, _keyring_key(source_id, field))
        except keyring.errors.PasswordDeleteError:
            pass


def _populate_secrets(source: DataSource) -> DataSource:
    """Fill in password fields from keyring."""
    data = source.model_dump()
    for field in _SECRET_FIELDS:
        data[field] = _load_secret(source.id, field)
    return DataSource(**data)


def _strip_secrets(store: DataSourceStore) -> dict:
    """Return store dict with password fields blanked out for JSON."""
    data = store.model_dump()
    for src in data["sources"]:
        for field in _SECRET_FIELDS:
            src[field] = ""
    return data


# ── Persistence ──


def _load_store() -> DataSourceStore:
    if _CONFIG_PATH.exists():
        raw = json.loads(_CONFIG_PATH.read_text())
        store = DataSourceStore(**raw)
        # Populate secrets from keyring
        store.sources = [_populate_secrets(s) for s in store.sources]
        return store
    return DataSourceStore()


def _save_store(store: DataSourceStore) -> None:
    # Save secrets to keyring, strip them from JSON
    for src in store.sources:
        for field in _SECRET_FIELDS:
            value = getattr(src, field)
            if value:
                _save_secret(src.id, field, value)
    stripped = _strip_secrets(store)
    _CONFIG_PATH.write_text(json.dumps(stripped, indent=2) + "\n")


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
            # Only overwrite secret fields if a new value was provided.
            # Empty string from the edit form means "unchanged".
            for field in _SECRET_FIELDS:
                if not updates.get(field):
                    updates.pop(field, None)
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
    _delete_secrets(source_id)
    return True


def set_active_source_id(source_id: str) -> bool:
    store = _load_store()
    if not any(s.id == source_id for s in store.sources):
        return False
    store.active_id = source_id
    _save_store(store)
    return True
