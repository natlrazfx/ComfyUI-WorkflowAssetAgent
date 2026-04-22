from __future__ import annotations

import shutil
from pathlib import Path

import requests

from .config import DEFAULT_TEMP_ROOT


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    return f"{size:.1f} {unit}"


def _target_root(override: dict | None, settings: dict) -> str:
    override = override or {}
    mode = override.get("download_mode") or settings.get("download_mode", "temp_assets")
    custom_root = (override.get("custom_root") or settings.get("custom_root") or "").strip()
    if mode == "custom_root" and custom_root:
        return custom_root
    return settings.get("default_temp_root", DEFAULT_TEMP_ROOT)


def _existing_disk_root(root: str) -> Path:
    path = Path(root)
    if path.exists():
        return path
    for parent in [path, *path.parents]:
        if parent.exists():
            return parent
    return Path("/")


def _head_size(url: str, timeout: int) -> int | None:
    if not url or str(url).upper().startswith("PASTE_"):
        return None

    try:
        response = requests.head(url, allow_redirects=True, timeout=timeout)
        if response.ok:
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                return int(length)
    except Exception:
        pass

    try:
        response = requests.get(url, allow_redirects=True, timeout=timeout, stream=True)
        if response.ok:
            length = response.headers.get("Content-Length")
            response.close()
            if length and length.isdigit():
                return int(length)
    except Exception:
        return None
    return None


def preflight_download(entries: list[dict], override: dict | None, settings: dict) -> dict:
    root = _target_root(override, settings)
    disk_root = _existing_disk_root(root)
    disk = shutil.disk_usage(disk_root)
    timeout = int(settings.get("search", {}).get("timeout_seconds", 20))

    items = []
    known_total = 0
    unknown_count = 0
    for entry in entries:
        size_bytes = _head_size(entry.get("source", ""), timeout=timeout)
        if size_bytes is None:
            unknown_count += 1
        else:
            known_total += size_bytes
        items.append(
            {
                "model_name": entry.get("model_name", ""),
                "source": entry.get("source", ""),
                "target": entry.get("target", ""),
                "size_bytes": size_bytes,
                "size_human": _format_bytes(size_bytes),
            }
        )

    missing = max(0, known_total - disk.free)
    ok = known_total <= disk.free

    message = (
        f"Known download size: {_format_bytes(known_total)} | "
        f"Free space at {root} (checked via {disk_root}): {_format_bytes(disk.free)}"
    )
    if unknown_count:
        message += f" | Unknown sizes: {unknown_count}"
    if not ok:
        message += f" | Need about {_format_bytes(missing)} more free space before download starts."

    return {
        "ok": ok,
        "root": root,
        "disk_check_root": str(disk_root),
        "free_bytes": disk.free,
        "free_human": _format_bytes(disk.free),
        "known_total_bytes": known_total,
        "known_total_human": _format_bytes(known_total),
        "unknown_count": unknown_count,
        "insufficient_by_bytes": missing,
        "insufficient_by_human": _format_bytes(missing) if missing else "0 B",
        "message": message,
        "items": items,
    }
