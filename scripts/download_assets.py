#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_MANIFEST = "/workspace/config/manifests/models_manifest.txt"
DEFAULT_BASE_PATH = Path("/tmp/comfy_assets")


def run(cmd: list[str]) -> None:
    print(">>", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def is_placeholder(value: str) -> bool:
    marker = value.strip().upper()
    return marker.startswith("PASTE_") or marker == "PASTE_URL_HERE"


def normalize_target(raw_target: str) -> Path:
    target = Path(raw_target)
    if target.is_absolute():
        return target
    return DEFAULT_BASE_PATH / target


def emit_progress(target: Path, downloaded: int, total: int | None, speed_bps: float | None, eta_seconds: float | None, stage: str = "downloading") -> None:
    payload = {
        "target": str(target),
        "downloaded_bytes": int(downloaded),
        "total_bytes": int(total) if total is not None else None,
        "speed_bps": round(float(speed_bps), 2) if speed_bps is not None else None,
        "eta_seconds": round(float(eta_seconds), 2) if eta_seconds is not None else None,
        "stage": stage,
    }
    print(f"[PROGRESS] {json.dumps(payload, ensure_ascii=False)}", flush=True)


def ensure_requests() -> None:
    try:
        import requests  # noqa: F401
    except Exception:
        run([sys.executable, "-m", "pip", "install", "requests"])


def download_file(url: str, target: Path) -> None:
    if is_placeholder(url):
        print(f"[SKIP] Placeholder URL for {target}", flush=True)
        print(f"[DONE] {target}", flush=True)
        return

    if target.exists():
        print(f"[SKIP] File already exists: {target}", flush=True)
        print(f"[DONE] {target}", flush=True)
        return

    ensure_parent(target)
    print(f"[START] {target}", flush=True)
    ensure_requests()
    import requests

    part_path = target.with_name(f"{target.name}.part")
    resume_from = part_path.stat().st_size if part_path.exists() else 0
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    started_at = time.monotonic()
    last_emit_at = 0.0

    with requests.get(url, stream=True, allow_redirects=True, headers=headers, timeout=120) as response:
        response.raise_for_status()

        if resume_from and response.status_code != 206:
            part_path.unlink(missing_ok=True)
            resume_from = 0

        remaining = response.headers.get("Content-Length")
        total_bytes = None
        if remaining is not None:
            try:
                remaining_int = int(remaining)
                total_bytes = resume_from + remaining_int if resume_from and response.status_code == 206 else remaining_int
            except Exception:
                total_bytes = None

        downloaded = resume_from
        mode = "ab" if resume_from and response.status_code == 206 else "wb"
        emit_progress(target, downloaded, total_bytes, None, None, "starting")

        with open(part_path, mode) as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_emit_at >= 0.5:
                    elapsed = max(now - started_at, 0.001)
                    speed = max(downloaded - resume_from, 0) / elapsed
                    eta = ((total_bytes - downloaded) / speed) if total_bytes and speed > 0 and downloaded <= total_bytes else None
                    emit_progress(target, downloaded, total_bytes, speed, eta, "downloading")
                    last_emit_at = now

        part_path.replace(target)
        elapsed = max(time.monotonic() - started_at, 0.001)
        avg_speed = max(downloaded - resume_from, 0) / elapsed
        emit_progress(target, downloaded, total_bytes or downloaded, avg_speed, 0, "completed")
        print(f"[DONE] {target}", flush=True)
        return


def ensure_huggingface_hub() -> None:
    try:
        import huggingface_hub  # noqa: F401
    except Exception:
        run([sys.executable, "-m", "pip", "install", "huggingface_hub"])


def hf_snapshot(repo_id: str, target_dir: Path) -> None:
    if is_placeholder(repo_id):
        print(f"[SKIP] Placeholder repo id for {target_dir}", flush=True)
        print(f"[DONE] {target_dir}", flush=True)
        return

    ensure_huggingface_hub()
    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"[START] {target_dir}", flush=True)
    emit_progress(target_dir, 0, None, None, None, "snapshot")

    hf_token = os.environ.get("HF_TOKEN", "").strip()

    code = f"""
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id={repo_id!r},
    local_dir={str(target_dir)!r},
    local_dir_use_symlinks=False,
    token={hf_token!r} if {bool(hf_token)!r} else None,
)
print("DONE:", {str(target_dir)!r})
"""
    run([sys.executable, "-c", code])
    emit_progress(target_dir, 0, None, None, 0, "completed")
    print(f"[DONE] {target_dir}", flush=True)


def iter_manifest_entries(manifest_path: Path, seen: set[Path] | None = None):
    if seen is None:
        seen = set()

    manifest_path = manifest_path.resolve()
    if manifest_path in seen:
        raise ValueError(f"Manifest include loop detected: {manifest_path}")
    seen.add(manifest_path)

    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    for line_number, raw_line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("|")]
        kind = parts[0].upper()

        if kind == "INCLUDE":
            if len(parts) < 2 or not parts[1]:
                raise ValueError(
                    f"Invalid INCLUDE at {manifest_path}:{line_number}: {raw_line}"
                )
            nested = Path(parts[1])
            if not nested.is_absolute():
                nested = manifest_path.parent / nested
            yield from iter_manifest_entries(nested, seen)
            continue

        if len(parts) < 3:
            raise ValueError(
                f"Invalid manifest line at {manifest_path}:{line_number}: {raw_line}"
            )

        source = parts[1]
        target = normalize_target(parts[2])
        yield kind, source, target


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download ComfyUI workflow assets into temporary pod storage."
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        default=DEFAULT_MANIFEST,
        help="Path to manifest file. Defaults to /workspace/config/manifests/models_manifest.txt",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    for kind, source, target in iter_manifest_entries(manifest_path):
        if kind == "FILE":
            download_file(source, target)
        elif kind == "SNAPSHOT":
            hf_snapshot(source, target)
        else:
            raise ValueError(f"Unknown manifest entry type: {kind}")

    print("All requested assets are ready.", flush=True)


if __name__ == "__main__":
    main()
