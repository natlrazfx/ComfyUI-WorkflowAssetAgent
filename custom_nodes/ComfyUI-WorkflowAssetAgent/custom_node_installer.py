from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def _workspace_root() -> Path:
    comfy_root = Path(__file__).resolve().parents[3]
    return comfy_root.parent


def _custom_nodes_dir() -> Path:
    return _workspace_root() / "ComfyUI" / "custom_nodes"


def _python_executable() -> str:
    venv_python = _workspace_root() / "venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _safe_repo_name(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    path = (parsed.path or "").rstrip("/")
    if not path:
        raise ValueError("Invalid GitHub repository URL")
    name = path.split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    if not name:
        raise ValueError("Could not derive repository name from URL")
    return name


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )


def install_custom_node(repo_url: str, update_if_exists: bool = True, install_deps: bool = True) -> dict:
    repo_url = str(repo_url or "").strip()
    if not repo_url:
        raise ValueError("repo_url is required")
    if "github.com" not in repo_url:
        raise ValueError("Only GitHub repository URLs are currently supported")

    custom_nodes = _custom_nodes_dir()
    custom_nodes.mkdir(parents=True, exist_ok=True)
    repo_name = _safe_repo_name(repo_url)
    destination = custom_nodes / repo_name

    git_ok = _run(["git", "--version"])
    if git_ok.returncode != 0:
        raise RuntimeError("git is not available in this environment")

    logs: list[str] = []
    status = "installed"

    if destination.exists():
        if not update_if_exists:
            return {
                "ok": True,
                "status": "already_exists",
                "repo_url": repo_url,
                "destination": str(destination),
                "logs": [f"Repository already exists at {destination}"],
            }

        pull = _run(["git", "pull", "--ff-only"], cwd=destination)
        logs.append(pull.stdout.strip())
        if pull.stderr.strip():
            logs.append(pull.stderr.strip())
        if pull.returncode != 0:
            raise RuntimeError(f"git pull failed for {repo_name}: {pull.stderr.strip() or pull.stdout.strip()}")
        status = "updated"
    else:
        clone = _run(["git", "clone", repo_url, str(destination)])
        logs.append(clone.stdout.strip())
        if clone.stderr.strip():
            logs.append(clone.stderr.strip())
        if clone.returncode != 0:
            raise RuntimeError(f"git clone failed for {repo_name}: {clone.stderr.strip() or clone.stdout.strip()}")

    requirements = destination / "requirements.txt"
    if install_deps and requirements.exists():
        pip_install = _run([_python_executable(), "-m", "pip", "install", "-r", str(requirements)])
        logs.append(pip_install.stdout.strip())
        if pip_install.stderr.strip():
            logs.append(pip_install.stderr.strip())
        if pip_install.returncode != 0:
            raise RuntimeError(
                f"requirements install failed for {repo_name}: {pip_install.stderr.strip() or pip_install.stdout.strip()}"
            )

    return {
        "ok": True,
        "status": status,
        "repo_url": repo_url,
        "destination": str(destination),
        "requirements_installed": bool(install_deps and requirements.exists()),
        "logs": [item for item in logs if item],
    }
