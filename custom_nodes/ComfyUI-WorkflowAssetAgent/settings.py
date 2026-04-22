from __future__ import annotations

from copy import deepcopy

from .config import DEFAULT_SETTINGS, SETTINGS_PATH
from .storage import read_json, write_json


def get_settings() -> dict:
    return read_json(SETTINGS_PATH, deepcopy(DEFAULT_SETTINGS))


def save_settings(new_settings: dict) -> dict:
    current = get_settings()
    merged = deepcopy(current)
    merged.update(new_settings or {})
    if "ai" in new_settings:
        merged["ai"] = {**current.get("ai", {}), **(new_settings.get("ai") or {})}
    if "search" in new_settings:
        merged["search"] = {**current.get("search", {}), **(new_settings.get("search") or {})}
    if "category_subdirs" in new_settings:
        merged["category_subdirs"] = {
            **current.get("category_subdirs", {}),
            **(new_settings.get("category_subdirs") or {}),
        }
    write_json(SETTINGS_PATH, merged)
    return merged
