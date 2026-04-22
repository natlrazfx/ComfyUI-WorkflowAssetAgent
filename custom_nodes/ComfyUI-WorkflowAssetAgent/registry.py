from __future__ import annotations

import re
from pathlib import Path

from .config import BY_WORKFLOW_ROOT, MODEL_SOURCE_MAP_PATH, REGISTRY_PATH
from .storage import read_json, write_json


def get_registry() -> dict:
    return read_json(REGISTRY_PATH, {})


def save_registry(data: dict) -> None:
    write_json(REGISTRY_PATH, data)


def upsert_registry_entry(model_name: str, entry: dict, write_back: bool = True) -> dict:
    registry = get_registry()
    current = registry.get(model_name, {})
    merged = {**current, **entry}
    registry[model_name] = merged
    if write_back:
        save_registry(registry)
    return merged


def sync_registry_from_manifests(write_back: bool = True) -> int:
    registry = get_registry()
    touched = 0

    for manifest in BY_WORKFLOW_ROOT.glob("*.manifest.txt"):
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 3:
                continue
            kind, source, target = parts[0].upper(), parts[1], parts[2]
            if kind not in {"FILE", "SNAPSHOT"}:
                continue
            model_name = Path(target).name
            next_entry = {
                **registry.get(model_name, {}),
                "kind": kind,
                "source": source,
                "target": target,
                "workflow_manifest": manifest.name,
            }
            if registry.get(model_name) != next_entry:
                registry[model_name] = next_entry
                touched += 1

    if MODEL_SOURCE_MAP_PATH.exists():
        pattern = re.compile(r"^\|\s*(.+?)\s*\|\s*(https?://[^|]+|\-)\s*\|")
        for raw in MODEL_SOURCE_MAP_PATH.read_text(encoding="utf-8").splitlines():
            match = pattern.match(raw.strip())
            if not match:
                continue
            model_name, source = match.group(1).strip(), match.group(2).strip()
            if source == "-":
                continue
            existing = registry.get(model_name, {})
            if not existing.get("source") or str(existing.get("source", "")).upper().startswith("PASTE_"):
                existing["source"] = source.replace("/blob/", "/resolve/")
                existing.setdefault("kind", "FILE")
                registry[model_name] = existing
                touched += 1

    if write_back:
        save_registry(registry)
    return touched
