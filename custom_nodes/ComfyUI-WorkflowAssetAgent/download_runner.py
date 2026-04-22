from __future__ import annotations

from datetime import datetime
import json
import subprocess
import sys
import threading
import uuid

from .config import DOWNLOAD_SCRIPT
from .logger import log_event
from .manifest_tools import runtime_manifest_path, write_manifest
from .settings import get_settings

QUEUE_LOCK = threading.Lock()
QUEUE: list[dict] = []
QUEUE_STATUS: dict[str, dict] = {}
WORKER_STARTED = False
MAX_QUEUE_HISTORY = 40


def _safe_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def apply_download_target(entry: dict, settings: dict, override: dict | None = None) -> dict:
    override = override or {}
    mode = override.get("download_mode") or settings.get("download_mode", "temp_assets")
    custom_root = (override.get("custom_root") or settings.get("custom_root") or "").rstrip("/\\")

    next_entry = dict(entry)
    target = entry["target"].replace("\\", "/").lstrip("/")
    if mode == "custom_root" and custom_root:
        next_entry["target"] = f"{custom_root}/{target}"
    return next_entry


def download_entries(workflow_name: str, entries: list[dict], override: dict | None = None) -> dict:
    settings = get_settings()
    manifest_entries = [apply_download_target(entry, settings, override) for entry in entries]
    manifest_path = runtime_manifest_path(workflow_name)
    write_manifest(manifest_path, workflow_name, manifest_entries)

    command = [sys.executable, "-u", str(DOWNLOAD_SCRIPT), str(manifest_path)]
    proc = subprocess.run(command, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0,
        "manifest_path": str(manifest_path),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
        "command": command,
    }


def _run_download_streaming(workflow_name: str, entries: list[dict], override: dict | None, job_id: str) -> dict:
    settings = get_settings()
    manifest_entries = [apply_download_target(entry, settings, override) for entry in entries]
    manifest_path = runtime_manifest_path(workflow_name)
    write_manifest(manifest_path, workflow_name, manifest_entries)

    command = [sys.executable, "-u", str(DOWNLOAD_SCRIPT), str(manifest_path)]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    with QUEUE_LOCK:
        QUEUE_STATUS[job_id]["total_items"] = len(entries)
        QUEUE_STATUS[job_id]["completed_items"] = 0
        QUEUE_STATUS[job_id]["current_item"] = ""
        QUEUE_STATUS[job_id]["current_downloaded_bytes"] = 0
        QUEUE_STATUS[job_id]["current_total_bytes"] = None
        QUEUE_STATUS[job_id]["current_speed_bps"] = None
        QUEUE_STATUS[job_id]["current_eta_seconds"] = None
        QUEUE_STATUS[job_id]["current_stage"] = "starting"

    def handle_stdout() -> None:
        for raw_line in proc.stdout or []:
            line = raw_line.rstrip()
            stdout_lines.append(line)
            if line.startswith("[START] "):
                current = line.removeprefix("[START] ").strip()
                with QUEUE_LOCK:
                    QUEUE_STATUS[job_id]["current_item"] = current
                    QUEUE_STATUS[job_id]["current_downloaded_bytes"] = 0
                    QUEUE_STATUS[job_id]["current_total_bytes"] = None
                    QUEUE_STATUS[job_id]["current_speed_bps"] = None
                    QUEUE_STATUS[job_id]["current_eta_seconds"] = None
                    QUEUE_STATUS[job_id]["current_stage"] = "downloading"
            elif line.startswith("[SKIP] "):
                with QUEUE_LOCK:
                    QUEUE_STATUS[job_id]["last_completed_item"] = line.removeprefix("[SKIP] ").strip()
                    QUEUE_STATUS[job_id]["current_stage"] = "skipped"
            elif line.startswith("[DONE] "):
                finished = line.removeprefix("[DONE] ").strip()
                with QUEUE_LOCK:
                    completed = int(QUEUE_STATUS[job_id].get("completed_items", 0)) + 1
                    total = int(QUEUE_STATUS[job_id].get("total_items", 0))
                    QUEUE_STATUS[job_id]["completed_items"] = min(completed, total) if total else completed
                    QUEUE_STATUS[job_id]["current_item"] = ""
                    QUEUE_STATUS[job_id]["last_completed_item"] = finished
                    QUEUE_STATUS[job_id]["current_downloaded_bytes"] = 0
                    QUEUE_STATUS[job_id]["current_total_bytes"] = None
                    QUEUE_STATUS[job_id]["current_speed_bps"] = None
                    QUEUE_STATUS[job_id]["current_eta_seconds"] = None
                    QUEUE_STATUS[job_id]["current_stage"] = "completed-item"
            elif line.startswith("[PROGRESS] "):
                payload = line.removeprefix("[PROGRESS] ").strip()
                try:
                    progress = json.loads(payload)
                except Exception:
                    continue
                with QUEUE_LOCK:
                    if progress.get("target"):
                        QUEUE_STATUS[job_id]["current_item"] = progress.get("target")
                    QUEUE_STATUS[job_id]["current_downloaded_bytes"] = _safe_int(progress.get("downloaded_bytes")) or 0
                    QUEUE_STATUS[job_id]["current_total_bytes"] = _safe_int(progress.get("total_bytes"))
                    QUEUE_STATUS[job_id]["current_speed_bps"] = _safe_float(progress.get("speed_bps"))
                    QUEUE_STATUS[job_id]["current_eta_seconds"] = _safe_float(progress.get("eta_seconds"))
                    QUEUE_STATUS[job_id]["current_stage"] = progress.get("stage") or "downloading"
                    QUEUE_STATUS[job_id]["last_progress_at"] = datetime.now().isoformat(timespec="seconds")

    def handle_stderr() -> None:
        for raw_line in proc.stderr or []:
            stderr_lines.append(raw_line.rstrip())

    t_out = threading.Thread(target=handle_stdout, daemon=True)
    t_err = threading.Thread(target=handle_stderr, daemon=True)
    t_out.start()
    t_err.start()
    returncode = proc.wait()
    t_out.join(timeout=1)
    t_err.join(timeout=1)

    return {
        "ok": returncode == 0,
        "manifest_path": str(manifest_path),
        "stdout": "\n".join(stdout_lines),
        "stderr": "\n".join(stderr_lines),
        "returncode": returncode,
        "command": command,
    }


def _ensure_worker() -> None:
    global WORKER_STARTED
    with QUEUE_LOCK:
        if WORKER_STARTED:
            return
        thread = threading.Thread(target=_queue_worker, daemon=True, name="workflow-asset-agent-queue")
        thread.start()
        WORKER_STARTED = True


def _terminal_jobs_in_order() -> list[str]:
    return [
        job_id
        for job_id, item in QUEUE_STATUS.items()
        if item.get("status") in {"completed", "failed"}
    ]


def _prune_queue_history() -> None:
    while len(QUEUE_STATUS) > MAX_QUEUE_HISTORY:
        removable = _terminal_jobs_in_order()
        if not removable:
            break
        QUEUE_STATUS.pop(removable[0], None)


def enqueue_download(workflow_name: str, entries: list[dict], override: dict | None = None) -> dict:
    _ensure_worker()
    job_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "job_id": job_id,
        "workflow_name": workflow_name,
        "entries": entries,
        "override": override or {},
        "created_at": created_at,
    }
    with QUEUE_LOCK:
        QUEUE.append(payload)
        QUEUE_STATUS[job_id] = {
            "job_id": job_id,
            "workflow_name": workflow_name,
            "status": "queued",
            "created_at": created_at,
            "total_items": 0,
            "completed_items": 0,
            "current_item": "",
            "current_downloaded_bytes": 0,
            "current_total_bytes": None,
            "current_speed_bps": None,
            "current_eta_seconds": None,
            "current_stage": "queued",
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }
        _prune_queue_history()
    log_event(f"Queued download job {job_id} for workflow '{workflow_name}' with {len(entries)} entries")
    return dict(QUEUE_STATUS[job_id])


def get_queue_status(job_id: str | None = None) -> dict:
    with QUEUE_LOCK:
        if job_id:
            return dict(QUEUE_STATUS.get(job_id, {"job_id": job_id, "status": "unknown"}))
        jobs = [dict(item) for item in QUEUE_STATUS.values()]
        return {
            "jobs": jobs[-20:],
            "history_retained": len(jobs),
            "queued": len([item for item in QUEUE_STATUS.values() if item.get("status") == "queued"]),
            "running": len([item for item in QUEUE_STATUS.values() if item.get("status") == "running"]),
        }


def _queue_worker() -> None:
    while True:
        payload = None
        with QUEUE_LOCK:
            if QUEUE:
                payload = QUEUE.pop(0)
                QUEUE_STATUS[payload["job_id"]]["status"] = "running"
                QUEUE_STATUS[payload["job_id"]]["started_at"] = datetime.now().isoformat(timespec="seconds")
        if payload is not None:
            log_event(
                f"Started download job {payload['job_id']} for workflow '{payload['workflow_name']}' "
                f"with {len(payload['entries'])} entries"
            )
        if payload is None:
            threading.Event().wait(0.5)
            continue

        result = _run_download_streaming(payload["workflow_name"], payload["entries"], payload["override"], payload["job_id"])
        with QUEUE_LOCK:
            QUEUE_STATUS[payload["job_id"]].update(
                {
                    "status": "completed" if result["ok"] else "failed",
                    "manifest_path": result["manifest_path"],
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "returncode": result["returncode"],
                    "command": result["command"],
                    "current_item": "",
                    "current_downloaded_bytes": 0,
                    "current_total_bytes": None,
                    "current_speed_bps": None,
                    "current_eta_seconds": None,
                    "current_stage": "done" if result["ok"] else "failed",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            if result["ok"]:
                total = int(QUEUE_STATUS[payload["job_id"]].get("total_items", 0))
                QUEUE_STATUS[payload["job_id"]]["completed_items"] = total
            _prune_queue_history()
        log_event(
            f"Finished download job {payload['job_id']} for workflow '{payload['workflow_name']}' "
            f"status={QUEUE_STATUS[payload['job_id']]['status']}"
        )
