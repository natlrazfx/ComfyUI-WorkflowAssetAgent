#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


DEFAULT_REGISTRY = "/workspace/config/models/model_registry.json"
DEFAULT_OUTPUT = "/workspace/config/manifests/session_manifest.txt"

MODEL_EXTENSIONS = (
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".gguf",
    ".onnx",
)

TEXT_ENCODER_HINTS = (
    "t5",
    "umt5",
    "qwen",
    "gemma",
    "clip_l",
    "clip_g",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_name(value: str) -> str:
    return value.replace("\\", "/").split("/")[-1].strip()


def looks_like_model_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    raw = value.strip()
    if not raw or raw.startswith("http://") or raw.startswith("https://"):
        return False
    lowered = raw.lower()
    if any(lowered.endswith(ext) for ext in MODEL_EXTENSIONS):
        return True
    if "/" not in raw and any(hint in lowered for hint in TEXT_ENCODER_HINTS):
        return True
    return False


def guess_category(name: str) -> str:
    lowered = name.lower()
    if "lora" in lowered:
        return "loras"
    if lowered.endswith(".gguf") and ("qwen" in lowered or "t5" in lowered or "gemma" in lowered):
        return "text_encoders"
    if "vae" in lowered:
        return "vae"
    if "upscaler" in lowered or "upscale" in lowered:
        return "latent_upscale_models"
    if "control" in lowered:
        return "controlnet"
    if "depth" in lowered:
        return "depthanything3"
    if "sam" in lowered:
        return "sam3"
    if "qwen" in lowered or "gemma" in lowered or "umt5" in lowered or "t5" in lowered:
        return "text_encoders"
    if lowered.endswith(".gguf"):
        return "unet"
    return "diffusion_models"


def collect_candidates(data) -> dict[str, dict]:
    found: dict[str, dict] = {}

    def remember(name: str, source: str) -> None:
        model_name = normalize_name(name)
        if not model_name:
            return
        entry = found.setdefault(
            model_name,
            {"name": model_name, "sources": set(), "category_hint": guess_category(model_name)},
        )
        entry["sources"].add(source)

    def walk(obj, source: str = "root") -> None:
        if isinstance(obj, dict):
            models = obj.get("models")
            if isinstance(models, list):
                for model in models:
                    if isinstance(model, dict):
                        name = model.get("name")
                        directory = model.get("directory")
                        if isinstance(name, str):
                            remember(name, f"{source}.models")
                            if isinstance(directory, str) and directory:
                                found[normalize_name(name)]["category_hint"] = directory

            widgets = obj.get("widgets_values")
            if isinstance(widgets, list):
                for idx, value in enumerate(widgets):
                    if looks_like_model_name(value):
                        remember(value, f"{source}.widgets_values[{idx}]")

            for key, value in obj.items():
                next_source = f"{source}.{key}" if source else key
                walk(value, next_source)
            return

        if isinstance(obj, list):
            for idx, item in enumerate(obj):
                walk(item, f"{source}[{idx}]")
            return

        if looks_like_model_name(obj):
            remember(obj, source)

    walk(data)
    return found


def load_registry(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("Registry must be a JSON object keyed by model filename.")
    return data


def prompt_yes_no(question: str, default: bool | None = None) -> bool:
    suffix = " [y/n] "
    if default is True:
        suffix = " [Y/n] "
    elif default is False:
        suffix = " [y/N] "

    while True:
        answer = input(question + suffix).strip().lower()
        if not answer and default is not None:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer y or n.")


def build_manifest_line(model_name: str, record: dict) -> str:
    kind = record["kind"].upper()
    source = record["source"]
    target = record["target"]
    return f"{kind}|{source}|{target}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract model names from a ComfyUI workflow and build a session manifest interactively."
    )
    parser.add_argument("workflow", help="Path to workflow JSON")
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help="JSON registry mapping model filenames to download metadata",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Where to write the generated session manifest",
    )
    args = parser.parse_args()

    workflow_path = Path(args.workflow)
    registry_path = Path(args.registry)
    output_path = Path(args.output)

    workflow_data = load_json(workflow_path)
    candidates = collect_candidates(workflow_data)
    registry = load_registry(registry_path)

    if not candidates:
        print("No obvious model filenames found in workflow.")
        sys.exit(0)

    ordered_names = sorted(candidates)
    print("Found models in workflow:")
    for name in ordered_names:
        status = "mapped" if name in registry else "unmapped"
        hint = candidates[name]["category_hint"]
        print(f"  - {name} [{status}, hint={hint}]")

    selected: list[str] = []
    if prompt_yes_no("Add all mapped models to session manifest?", default=True):
        selected = [name for name in ordered_names if name in registry]
    else:
        for name in ordered_names:
            if name not in registry:
                print(f"Skip {name}: not found in registry.")
                continue
            if prompt_yes_no(f"Add {name}?", default=False):
                selected.append(name)

    missing = [name for name in ordered_names if name not in registry]
    if missing:
        print("\nUnmapped models:")
        for name in missing:
            hint = candidates[name]["category_hint"]
            print(f"  - {name} (category hint: {hint})")

    lines = [
        "# Generated session manifest",
        f"# Workflow: {workflow_path}",
        "",
    ]
    for name in selected:
        lines.append(build_manifest_line(name, registry[name]))

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {len(selected)} entries to {output_path}")

    if missing:
        print(f"{len(missing)} model(s) still need mapping in {registry_path}")


if __name__ == "__main__":
    main()
