#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
NODE_SRC = REPO_ROOT / "custom_nodes" / "ComfyUI-WorkflowAssetAgent"
SCRIPTS_SRC = REPO_ROOT / "scripts"
MODELS_CFG_SRC = REPO_ROOT / "config" / "models"


def copy_file(src: Path, dst: Path, overwrite: bool = True) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not overwrite:
        return
    shutil.copy2(src, dst)


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def default_temp_root(workspace_root: Path) -> str:
    workspace_posix = workspace_root.as_posix()
    if workspace_posix == "/workspace" or os.environ.get("RUNPOD_POD_ID"):
        return "/tmp/comfy_assets"
    return str((workspace_root / "tmp" / "comfy_assets").resolve())


def default_settings(workspace_root: Path) -> dict:
    return {
        "download_mode": "temp_assets",
        "custom_root": "",
        "default_temp_root": default_temp_root(workspace_root),
        "category_subdirs": {
            "checkpoints": "checkpoints",
            "diffusion_models": "diffusion_models",
            "vae": "vae",
            "vae_approx": "vae_approx",
            "loras": "loras",
            "unet": "unet",
            "clip": "clip",
            "clip_vision": "clip_vision",
            "controlnet": "controlnet",
            "text_encoders": "text_encoders",
            "model_patches": "model_patches",
            "latent_upscale_models": "latent_upscale_models",
            "upscale_models": "upscale_models",
            "embeddings": "embeddings",
            "depthanything3": "depthanything3",
            "sam3": "sam3",
            "whisper": "whisper",
            "Qwen3-ASR": "Qwen3-ASR",
            "LLM": "LLM",
            "other": "other",
        },
        "ai": {
            "enabled": True,
            "provider": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "model": "",
            "api_key_env": "OPENAI_API_KEY",
        },
        "search": {
            "provider_mode": "hf_only",
            "huggingface_enabled": True,
            "civitai_enabled": False,
            "timeout_seconds": 20,
            "max_candidates": 8,
        },
    }


def ensure_settings_file(models_dir: Path, workspace_root: Path) -> Path:
    target = models_dir / "workflow_asset_agent_settings.json"
    if target.exists():
        return target
    data = default_settings(workspace_root)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Workflow Asset Agent into an existing ComfyUI workspace.")
    parser.add_argument("--comfy-root", required=True, help="Path to the ComfyUI root directory.")
    parser.add_argument(
        "--workspace-root",
        help="Path to the shared workspace root. If omitted, the parent of --comfy-root is used.",
    )
    args = parser.parse_args()

    comfy_root = Path(args.comfy_root).resolve()
    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else comfy_root.parent

    if not comfy_root.exists():
        raise SystemExit(f"ComfyUI root does not exist: {comfy_root}")

    node_dst = comfy_root / "custom_nodes" / "ComfyUI-WorkflowAssetAgent"
    scripts_dst = workspace_root / "scripts"
    models_dst = workspace_root / "config" / "models"
    manifests_dst = workspace_root / "config" / "manifests"

    copy_tree(NODE_SRC, node_dst)
    copy_file(SCRIPTS_SRC / "download_assets.py", scripts_dst / "download_assets.py")
    copy_file(SCRIPTS_SRC / "workflow_to_manifest.py", scripts_dst / "workflow_to_manifest.py")
    copy_file(MODELS_CFG_SRC / "model_aliases.json", models_dst / "model_aliases.json")
    copy_file(MODELS_CFG_SRC / "popular_models.json", models_dst / "popular_models.json")
    settings_path = ensure_settings_file(models_dst, workspace_root)
    manifests_dst.mkdir(parents=True, exist_ok=True)

    print("Installed Workflow Asset Agent")
    print(f"  ComfyUI root   : {comfy_root}")
    print(f"  Workspace root : {workspace_root}")
    print(f"  Custom node    : {node_dst}")
    print(f"  Scripts        : {scripts_dst}")
    print(f"  Settings       : {settings_path}")


if __name__ == "__main__":
    main()
