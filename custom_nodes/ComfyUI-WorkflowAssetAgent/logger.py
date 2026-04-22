from __future__ import annotations

from datetime import datetime

from .config import LOG_ROOT


def log_event(message: str) -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
    with (LOG_ROOT / f"workflow_asset_agent_{stamp}.log").open("a", encoding="utf-8") as handle:
        handle.write(line)
