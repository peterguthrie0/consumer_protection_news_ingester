"""Source configuration loading with dependency-free validation."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when source configuration cannot be used safely."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    publisher_type: str
    country: str
    region: str
    method: str
    url: str
    enabled: bool
    expected_freshness_hours: int = 168
    rate_limit_per_minute: int = 6


def _required(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ConfigurationError(f"{context}: missing required field {key!r}")
    return mapping[key]


def load_sources(path: Path, *, enabled_only: bool = True) -> list[Source]:
    """Load enabled RSS/Atom sources from a JSON registry."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"cannot read {path}: {error}") from error
    return validate_sources(data, enabled_only=enabled_only)


def validate_sources(data: Any, *, enabled_only: bool = True) -> list[Source]:
    if not isinstance(data, list):
        raise ConfigurationError("source registry must be a JSON array")

    sources: list[Source] = []
    seen: set[str] = set()
    for index, item in enumerate(data):
        context = f"source #{index + 1}"
        if not isinstance(item, dict):
            raise ConfigurationError(f"{context}: expected an object")
        jurisdiction = _required(item, "jurisdiction", context)
        discovery = _required(item, "discovery", context)
        if not isinstance(jurisdiction, dict) or not isinstance(discovery, dict):
            raise ConfigurationError(f"{context}: jurisdiction and discovery must be objects")
        source_id = str(_required(item, "id", context))
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", source_id):
            raise ConfigurationError(f"{context}: id must contain lowercase words separated by hyphens")
        if source_id in seen:
            raise ConfigurationError(f"{context}: duplicate id {source_id!r}")
        seen.add(source_id)
        method = str(_required(discovery, "method", context)).lower()
        if method not in {"rss", "atom"}:
            raise ConfigurationError(f"{context}: prototype supports only rss and atom")
        url = str(_required(discovery, "url", context))
        if not valid_url(url, https_only=True):
            raise ConfigurationError(f"{context}: discovery URL must be absolute HTTPS")
        if type(_required(item, "enabled", context)) is not bool:
            raise ConfigurationError(f"{context}: enabled must be true or false")
        if not str(_required(item, "name", context)).strip():
            raise ConfigurationError(f"{context}: name is required")
        if _required(item, "publisher_type", context) not in {"attorney_general", "regulator", "legislature", "court", "federal_agency", "other_official"}:
            raise ConfigurationError(f"{context}: invalid publisher type")
        if not re.fullmatch(r"[A-Z]{2}", str(_required(jurisdiction, "country", context))):
            raise ConfigurationError(f"{context}: country must be a two-letter code")
        if not 2 <= len(str(_required(jurisdiction, "region", context))) <= 8:
            raise ConfigurationError(f"{context}: region must have 2 to 8 characters")
        for key, default, maximum in (("rate_limit_per_minute", 6, 60), ("expected_freshness_hours", 168, 87600)):
            value = item.get(key, default)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ConfigurationError(f"{context}: invalid {key}")
        sources.append(
            Source(
                id=source_id,
                name=str(_required(item, "name", context)),
                publisher_type=str(_required(item, "publisher_type", context)),
                country=str(_required(jurisdiction, "country", context)),
                region=str(_required(jurisdiction, "region", context)),
                method=method,
                url=url,
                enabled=bool(_required(item, "enabled", context)),
                expected_freshness_hours=int(item.get("expected_freshness_hours", 168)),
                rate_limit_per_minute=int(item.get("rate_limit_per_minute", 6)),
            )
        )
    return [source for source in sources if source.enabled or not enabled_only]


def valid_url(value: str, *, https_only: bool = False) -> bool:
    try:
        parsed = urlparse(value)
        return bool(parsed.scheme in ({"https"} if https_only else {"http", "https"})
                    and parsed.hostname and parsed.port != 0 and not parsed.username
                    and not any(c.isspace() or ord(c) < 32 for c in value))
    except ValueError:
        return False


def write_registry(path: Path, entries: list[dict[str, Any]]) -> None:
    """Validate before atomically replacing the registry; preserve unrelated fields."""
    validate_sources(entries, enabled_only=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            name = handle.name
            json.dump(entries, handle, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
