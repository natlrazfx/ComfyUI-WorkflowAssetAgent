from __future__ import annotations

import os
from pathlib import Path

EXTENSION_ROOT = Path(__file__).resolve().parent
WEB_DIRECTORY = "./web"

# Resolve ComfyUI and workspace roots from the actual extension location so the
# node works both inside RunPod (/workspace/ComfyUI/...) and local installs.
CUSTOM_NODES_ROOT = EXTENSION_ROOT.parent
COMFY_ROOT = CUSTOM_NODES_ROOT.parent
WORKSPACE_ROOT = COMFY_ROOT.parent

CONFIG_ROOT = WORKSPACE_ROOT / "config"
MODELS_ROOT = CONFIG_ROOT / "models"
MANIFEST_ROOT = CONFIG_ROOT / "manifests"
BY_WORKFLOW_ROOT = MANIFEST_ROOT / "by_workflow"
GENERATED_ROOT = MANIFEST_ROOT / "generated_runtime"
LOG_ROOT = WORKSPACE_ROOT / "logs" / "workflow_asset_agent"

REGISTRY_PATH = MODELS_ROOT / "model_registry.json"
SETTINGS_PATH = MODELS_ROOT / "workflow_asset_agent_settings.json"
ALIASES_PATH = MODELS_ROOT / "model_aliases.json"
POPULAR_MODELS_PATH = MODELS_ROOT / "popular_models.json"
MODEL_SOURCE_MAP_PATH = WORKSPACE_ROOT / "model_source_map.md"


def _first_existing_path(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DOWNLOAD_SCRIPT = _first_existing_path(
    [
        WORKSPACE_ROOT / "scripts" / "download_assets.py",
        COMFY_ROOT / "scripts" / "download_assets.py",
        EXTENSION_ROOT / "download_assets.py",
    ]
)


def _default_temp_root() -> str:
    workspace_posix = WORKSPACE_ROOT.as_posix()
    if workspace_posix == "/workspace" or os.environ.get("RUNPOD_POD_ID"):
        return "/tmp/comfy_assets"
    return str((WORKSPACE_ROOT / "tmp" / "comfy_assets").resolve())


DEFAULT_TEMP_ROOT = _default_temp_root()

CATEGORY_SUBDIRS = {
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
}

DEFAULT_SETTINGS = {
    "download_mode": "temp_assets",
    "custom_root": "",
    "default_temp_root": DEFAULT_TEMP_ROOT,
    "category_subdirs": CATEGORY_SUBDIRS,
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
