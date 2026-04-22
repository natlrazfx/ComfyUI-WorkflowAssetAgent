# ComfyUI Workflow Asset Agent

Manifest-first workflow model resolver and downloader for ComfyUI.

This project scans the currently opened workflow, extracts referenced models, keeps a reusable manifest per workflow, resolves download sources with a Hugging Face-first strategy, and downloads assets into the correct ComfyUI model folders or a temporary asset root.

## What It Includes

- `custom_nodes/ComfyUI-WorkflowAssetAgent`
  ComfyUI sidebar extension and backend routes
- `scripts/download_assets.py`
  Streaming downloader used by the node and by standalone manifests
- `scripts/workflow_to_manifest.py`
  Helper for building manifests from workflow JSON
- `config/models/model_aliases.json`
  Alias patterns for fuzzy filename matching
- `config/models/popular_models.json`
  Curated direct source map for common models
- `install_to_comfyui.py`
  Cross-platform installer for local ComfyUI or RunPod workspaces

## Main Features

- Scan the active workflow and build a manifest
- Reuse existing manifests for known workflows
- Detect workflow renames and offer manifest migration
- Search workflow notes first, then Hugging Face API, then Hugging Face web
- Optional AI ranking with OpenAI-compatible APIs
- Download into temp assets or a custom mapped root
- Disk preflight before queuing downloads
- Real per-file download progress for direct file downloads
- One-click download from resolved entries
- Optional custom-node installer route in the same panel

## Search Strategy

The current public bundle is intentionally **Hugging Face first**.

- Workflow notes and related Hugging Face links are preferred
- Hugging Face API search is used next
- Hugging Face web discovery is used as a final fallback
- Civitai is disabled in the shipped default settings

## Repository Layout

```text
custom_nodes/ComfyUI-WorkflowAssetAgent/
scripts/
config/models/
config/manifests/examples/
docs/
install_to_comfyui.py
```

## Install

### Option 1. Install into an existing local ComfyUI

```bash
python install_to_comfyui.py --comfy-root /path/to/ComfyUI
```

Windows example:

```powershell
python .\install_to_comfyui.py --comfy-root "D:\ComfyUI-Easy-Install\ComfyUI-Easy-Install\ComfyUI"
```

This will:

- copy the custom node into `custom_nodes/ComfyUI-WorkflowAssetAgent`
- copy shared scripts into `<workspace>/scripts`
- copy reusable config into `<workspace>/config/models`
- create `workflow_asset_agent_settings.json` if it does not already exist

### Option 2. Install into a RunPod-style workspace

```bash
python install_to_comfyui.py --comfy-root /workspace/ComfyUI --workspace-root /workspace
```

## Environment Variables

Create a local `.env` or export variables in your shell:

```env
OPENAI_API_KEY=
HF_TOKEN=
```

Notes:

- `OPENAI_API_KEY` is only required for AI resolve
- `HF_TOKEN` is optional but useful for gated Hugging Face repos
- `.env` is intentionally excluded from git

## Settings File

Default example:

- `config/models/workflow_asset_agent_settings.example.json`

Runtime settings are written to:

- `config/models/workflow_asset_agent_settings.json`

This runtime file is ignored by git because it is user state.

## Generated Data

The following are generated at runtime and should usually stay out of git:

- `config/models/model_registry.json`
- `config/manifests/by_workflow/`
- `config/manifests/generated_runtime/`
- `logs/`

See [docs/MANIFESTS.md](docs/MANIFESTS.md).

## Publishing Notes

Before pushing publicly, review:

- `README.md`
- `.env.example`
- `config/models/popular_models.json`

to make sure everything matches the sources and naming you want to publish.

## Support

If this tool saves you time, add your support link here before publishing:

- Buy Me a Coffee: `YOUR_LINK_HERE`

## Status

This repository is intended to be a reusable public bundle of the Workflow Asset Agent toolchain, not a dump of a private ComfyUI workspace.
