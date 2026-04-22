from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def ensure_json_file(path: Path, default_data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(default_data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path, default_data: Any) -> Any:
    ensure_json_file(path, default_data)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(default_data)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
