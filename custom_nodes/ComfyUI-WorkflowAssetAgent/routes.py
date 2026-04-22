from __future__ import annotations

import os
from copy import deepcopy
from aiohttp import web
from pathlib import Path
from server import PromptServer

from .custom_node_installer import install_custom_node
from .download_runner import apply_download_target, download_entries, enqueue_download, get_queue_status
from .logger import log_event
from .manifest_tools import manifest_path_for_workflow, parse_manifest, runtime_manifest_path, write_manifest
from .preflight import preflight_download
from .registry import get_registry, save_registry, sync_registry_from_manifests, upsert_registry_entry
from .resolver import (
    ai_pick,
    build_entry,
    dedupe_candidates,
    search_huggingface,
    search_huggingface_deep,
    search_note_urls,
    search_web_huggingface,
    target_from_category,
)
from .settings import get_settings, save_settings
from .workflow_scan import scan_workflow


def json_ok(data: dict, status: int = 200):
    return web.json_response(data, status=status)


def _merge_scan_metadata(entries: list[dict], workflow_data: dict) -> list[dict]:
    if not workflow_data:
        return entries

    scanned_map = {item.get("model_name"): item for item in scan_workflow(workflow_data)}
    merged = []
    for entry in entries:
        model_name = entry.get("model_name")
        scanned = scanned_map.get(model_name, {})
        next_entry = dict(entry)
        next_entry["node_types"] = scanned.get("node_types", entry.get("node_types", []))
        next_entry["sources"] = scanned.get("sources", entry.get("sources", []))
        next_entry["note_urls"] = scanned.get("note_urls", entry.get("note_urls", []))
        next_entry["note_model_hints"] = scanned.get("note_model_hints", entry.get("note_model_hints", []))
        merged.append(next_entry)
    return merged


def _merge_manifest_entries(preferred: list[dict], fallback: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for entry in fallback:
        merged[entry["model_name"]] = deepcopy(entry)

    for entry in preferred:
        model_name = entry["model_name"]
        existing = merged.get(model_name)
        if existing is None:
            merged[model_name] = deepcopy(entry)
            continue

        existing_resolved = bool(existing.get("source")) and not str(existing.get("source", "")).upper().startswith("PASTE_")
        next_resolved = bool(entry.get("source")) and not str(entry.get("source", "")).upper().startswith("PASTE_")

        if next_resolved and not existing_resolved:
            merged[model_name] = deepcopy(entry)
            continue
        if next_resolved == existing_resolved and len(str(entry.get("target", ""))) < len(str(existing.get("target", ""))):
            merged[model_name] = deepcopy(entry)
    return list(merged.values())


def _rewrite_registry_manifest_name(old_manifest_name: str, new_manifest_name: str) -> None:
    registry = get_registry()
    changed = False
    for model_name, entry in registry.items():
        if entry.get("workflow_manifest") != old_manifest_name:
            continue
        registry[model_name] = {**entry, "workflow_manifest": new_manifest_name}
        changed = True
    if changed:
        save_registry(registry)


def _migrate_runtime_manifest(old_workflow_name: str, new_workflow_name: str, remove_old: bool) -> dict:
    old_runtime = runtime_manifest_path(old_workflow_name)
    new_runtime = runtime_manifest_path(new_workflow_name)
    if not old_runtime.exists():
        return {"migrated": False, "removed_old": False, "merged": False}

    old_entries = parse_manifest(old_runtime)
    if new_runtime.exists():
        new_entries = parse_manifest(new_runtime)
        merged_entries = _merge_manifest_entries(old_entries, new_entries)
        write_manifest(new_runtime, new_workflow_name, merged_entries)
        migrated = True
        merged = True
    else:
        old_runtime.replace(new_runtime)
        migrated = True
        merged = False

    removed_old = False
    if remove_old and old_runtime != new_runtime and old_runtime.exists():
        old_runtime.unlink()
        removed_old = True

    return {"migrated": migrated, "removed_old": removed_old, "merged": merged}


def _entry_with_local_status(entry: dict, settings: dict) -> dict:
    effective = apply_download_target(entry, settings)
    effective_target = str(effective.get("target") or "")
    try:
        local_exists = bool(effective_target) and Path(effective_target).exists()
    except Exception:
        local_exists = False

    next_entry = dict(entry)
    next_entry["effective_target"] = effective_target
    next_entry["local_exists"] = local_exists
    return next_entry


@PromptServer.instance.routes.get("/workflow-assets/health")
async def workflow_assets_health(request):
    return json_ok({"ok": True, "service": "WorkflowAssetAgent"})


@PromptServer.instance.routes.get("/workflow-assets/settings")
async def workflow_assets_settings_get(request):
    return json_ok({"ok": True, "settings": get_settings()})


@PromptServer.instance.routes.get("/workflow-assets/runtime-status")
async def workflow_assets_runtime_status(request):
    settings = get_settings()
    ai_cfg = settings.get("ai", {})
    api_key_env = str(ai_cfg.get("api_key_env", "OPENAI_API_KEY")).strip() or "OPENAI_API_KEY"
    model = str(ai_cfg.get("model", "")).strip()
    base_url = str(ai_cfg.get("base_url", "")).strip()
    enabled = bool(ai_cfg.get("enabled", True))
    api_key_present = bool(os.environ.get(api_key_env, "").strip())
    model_present = bool(model)
    ai_active = enabled and api_key_present and model_present
    return json_ok(
        {
            "ok": True,
            "ai": {
                "enabled": enabled,
                "active": ai_active,
                "provider": str(ai_cfg.get("provider", "openai_compatible")).strip() or "openai_compatible",
                "api_key_env": api_key_env,
                "api_key_present": api_key_present,
                "model": model,
                "model_present": model_present,
                "base_url": base_url,
            },
            "search": settings.get("search", {}),
            "download_mode": settings.get("download_mode", "temp_assets"),
        }
    )


@PromptServer.instance.routes.post("/workflow-assets/settings")
async def workflow_assets_settings_post(request):
    payload = await request.json()
    settings = save_settings(payload)
    log_event("Settings updated")
    return json_ok({"ok": True, "settings": settings})


@PromptServer.instance.routes.post("/workflow-assets/rebuild-registry")
async def workflow_assets_rebuild_registry(request):
    touched = sync_registry_from_manifests(write_back=True)
    log_event(f"Registry rebuild complete, touched={touched}")
    return json_ok({"ok": True, "touched": touched, "registry_size": len(get_registry())})


@PromptServer.instance.routes.post("/workflow-assets/scan")
async def workflow_assets_scan(request):
    payload = await request.json()
    workflow_name = (payload.get("workflow_name") or "current-workflow.json").strip()
    workflow_data = payload.get("workflow_data") or {}
    prefer_existing = bool(payload.get("prefer_existing", True))
    force_regenerate = bool(payload.get("force_regenerate", False))

    manifest_path = manifest_path_for_workflow(workflow_name)
    registry = get_registry()
    settings = get_settings()

    if manifest_path.exists() and prefer_existing and not force_regenerate:
        entries = parse_manifest(manifest_path)
        entries = _merge_scan_metadata(entries, workflow_data)
        entries = [_entry_with_local_status(entry, settings) for entry in entries]
        log_event(f"Loaded existing manifest for workflow '{workflow_name}'")
        return json_ok(
            {
                "ok": True,
                "workflow_name": workflow_name,
                "manifest_path": str(manifest_path),
                "used_existing_manifest": True,
                "entries": entries,
            }
        )

    scanned = scan_workflow(workflow_data)
    entries = []
    for item in scanned:
        reg = registry.get(item["model_name"], {})
        entry = build_entry(item["model_name"], item["category"], reg)
        entry["node_types"] = item.get("node_types", [])
        entry["sources"] = item.get("sources", [])
        entry["note_urls"] = item.get("note_urls", [])
        entry["note_model_hints"] = item.get("note_model_hints", [])
        entries.append(_entry_with_local_status(entry, settings))

    write_manifest(manifest_path, workflow_name, entries)
    sync_registry_from_manifests(write_back=True)
    log_event(f"Generated manifest for workflow '{workflow_name}' with {len(entries)} entries")
    return json_ok(
        {
            "ok": True,
            "workflow_name": workflow_name,
            "manifest_path": str(manifest_path),
            "used_existing_manifest": False,
            "entries": entries,
        }
    )


@PromptServer.instance.routes.post("/workflow-assets/migrate-manifest")
async def workflow_assets_migrate_manifest(request):
    payload = await request.json()
    old_workflow_name = str(payload.get("old_workflow_name") or "").strip()
    new_workflow_name = str(payload.get("new_workflow_name") or "").strip()
    remove_old = bool(payload.get("remove_old", True))

    if not old_workflow_name or not new_workflow_name:
        return json_ok({"ok": False, "error": "Both old_workflow_name and new_workflow_name are required"}, status=400)

    old_manifest = manifest_path_for_workflow(old_workflow_name)
    new_manifest = manifest_path_for_workflow(new_workflow_name)
    if not old_manifest.exists():
        return json_ok({"ok": False, "error": f"Old manifest not found: {old_manifest}"}, status=404)

    old_entries = parse_manifest(old_manifest)
    new_entries = parse_manifest(new_manifest)
    merged_entries = _merge_manifest_entries(old_entries, new_entries)
    write_manifest(new_manifest, new_workflow_name, merged_entries)

    runtime_result = _migrate_runtime_manifest(old_workflow_name, new_workflow_name, remove_old)

    old_manifest_name = old_manifest.name
    new_manifest_name = new_manifest.name
    if remove_old and old_manifest != new_manifest and old_manifest.exists():
        old_manifest.unlink()

    _rewrite_registry_manifest_name(old_manifest_name, new_manifest_name)
    sync_registry_from_manifests(write_back=True)
    log_event(f"Migrated manifest '{old_workflow_name}' -> '{new_workflow_name}' with {len(merged_entries)} entries")
    return json_ok(
        {
            "ok": True,
            "old_manifest_path": str(old_manifest),
            "new_manifest_path": str(new_manifest),
            "entries": len(merged_entries),
            "runtime_migrated": runtime_result["migrated"],
            "runtime_merged": runtime_result["merged"],
            "removed_old_manifest": remove_old and old_manifest != new_manifest,
            "removed_old_runtime_manifest": runtime_result["removed_old"],
        }
    )


@PromptServer.instance.routes.post("/workflow-assets/download")
async def workflow_assets_download(request):
    payload = await request.json()
    workflow_name = payload.get("workflow_name") or "current-workflow.json"
    entries = payload.get("entries") or []
    selected = payload.get("selected_models") or []
    override = payload.get("download_settings") or {}
    if selected:
        entries = [entry for entry in entries if entry.get("model_name") in selected]
    preflight = preflight_download(entries, override, get_settings())
    if not preflight["ok"]:
        log_event(f"Download blocked for workflow '{workflow_name}' due to insufficient disk space")
        return json_ok({"ok": False, "preflight": preflight, "error": preflight["message"]}, status=400)
    result = download_entries(workflow_name, entries, override)
    log_event(
        f"Download requested for workflow '{workflow_name}', models={len(entries)}, "
        f"mode={(override or {}).get('download_mode', '')}, ok={result['ok']}"
    )
    return json_ok({**result, "preflight": preflight}, status=200 if result["ok"] else 500)


@PromptServer.instance.routes.post("/workflow-assets/queue-download")
async def workflow_assets_queue_download(request):
    payload = await request.json()
    workflow_name = payload.get("workflow_name") or "current-workflow.json"
    entries = payload.get("entries") or []
    selected = payload.get("selected_models") or []
    override = payload.get("download_settings") or {}
    if selected:
        entries = [entry for entry in entries if entry.get("model_name") in selected]
    preflight = preflight_download(entries, override, get_settings())
    if not preflight["ok"]:
        log_event(f"Queue request blocked for workflow '{workflow_name}' due to insufficient disk space")
        return json_ok({"ok": False, "preflight": preflight, "error": preflight["message"]}, status=400)
    status = enqueue_download(workflow_name, entries, override)
    return json_ok({"ok": True, "job": status, "preflight": preflight})


@PromptServer.instance.routes.post("/workflow-assets/preflight")
async def workflow_assets_preflight(request):
    payload = await request.json()
    entries = payload.get("entries") or []
    selected = payload.get("selected_models") or []
    override = payload.get("download_settings") or {}
    if selected:
        entries = [entry for entry in entries if entry.get("model_name") in selected]
    preflight = preflight_download(entries, override, get_settings())
    return json_ok({"ok": True, "preflight": preflight})


@PromptServer.instance.routes.get("/workflow-assets/queue-status")
async def workflow_assets_queue_status(request):
    job_id = request.query.get("job_id")
    return json_ok({"ok": True, **get_queue_status(job_id)})


@PromptServer.instance.routes.post("/workflow-assets/resolve-model")
async def workflow_assets_resolve_model(request):
    payload = await request.json()
    model_name = payload["model_name"]
    category = payload.get("category", "other")
    note_urls = payload.get("note_urls") or []
    note_model_hints = payload.get("note_model_hints") or []
    resolve_mode = str(payload.get("resolve_mode", "find")).strip().lower()
    settings = get_settings()
    search_cfg = settings.get("search", {})
    timeout = int(search_cfg.get("timeout_seconds", 20))
    limit = int(search_cfg.get("max_candidates", 8))
    hf_enabled = bool(search_cfg.get("huggingface_enabled", True))
    civitai_enabled = False
    provider_mode = "hf_only"

    candidates = []
    errors = []
    try:
        candidates.extend(
            search_note_urls(
                model_name,
                category,
                note_urls,
                note_model_hints=note_model_hints,
                timeout=timeout,
            )
        )
    except Exception as exc:
        errors.append(f"notes: {exc}")
    if hf_enabled:
        try:
            if resolve_mode == "ai_deep":
                candidates.extend(
                    search_huggingface_deep(
                        model_name,
                        note_urls=note_urls,
                        note_model_hints=note_model_hints,
                        limit=max(limit, 12),
                        timeout=timeout,
                    )
                )
            else:
                candidates.extend(
                    search_huggingface(
                        model_name,
                        note_model_hints=note_model_hints,
                        limit=limit,
                        timeout=timeout,
                    )
                )
        except Exception as exc:
            errors.append(f"huggingface: {exc}")
    hf_candidates = [item for item in dedupe_candidates(candidates) if "huggingface" in str(item.get("provider", ""))]
    strong_hf_found = any(
        item.get("exact_filename_match")
        or item.get("token_overlap", 0) >= 0.55
        or item.get("match_score", 0) >= 0.88
        for item in hf_candidates
    )

    if hf_enabled and not strong_hf_found:
        try:
            candidates.extend(
                search_web_huggingface(
                    model_name,
                    note_urls=note_urls,
                    note_model_hints=note_model_hints,
                    limit=max(limit, 10),
                    timeout=timeout,
                )
            )
        except Exception as exc:
            errors.append(f"web_huggingface: {exc}")

    hf_candidates = [item for item in dedupe_candidates(candidates) if "huggingface" in str(item.get("provider", ""))]
    strong_hf_found = any(
        item.get("exact_filename_match")
        or item.get("token_overlap", 0) >= 0.55
        or item.get("match_score", 0) >= 0.88
        for item in hf_candidates
    )

    candidates = dedupe_candidates(candidates)
    decision = ai_pick(
        model_name,
        category,
        candidates,
        settings,
        note_urls=note_urls,
        note_model_hints=note_model_hints,
    )
    summary = {
        "notes": len([item for item in candidates if str(item.get("provider", "")).startswith("note:")]),
        "huggingface": len([item for item in candidates if "huggingface" in str(item.get("provider", ""))]),
        "web": len([item for item in candidates if str(item.get("provider", "")).startswith("web:")]),
        "civitai": 0,
    }
    log_event(
        f"Resolve requested for '{model_name}' in category '{category}', "
        f"note_urls={len(note_urls)}, note_hints={len(note_model_hints)}, candidates={len(candidates)}, "
        f"resolve_mode={resolve_mode}, "
        f"provider_mode={provider_mode}, "
        f"decision={decision.get('decision', 'unknown')}, provider={decision.get('provider', '')}, "
        f"mode={decision.get('mode', 'unknown')}, notes={summary['notes']}, hf={summary['huggingface']}, web={summary['web']}"
    )
    return json_ok(
        {
            "ok": True,
            "model_name": model_name,
            "category": category,
            "candidates": candidates,
            "decision": decision,
            "errors": errors,
            "summary": summary,
            "resolve_mode": resolve_mode,
        }
    )


@PromptServer.instance.routes.post("/workflow-assets/install-custom-node")
async def workflow_assets_install_custom_node(request):
    payload = await request.json()
    repo_url = str(payload.get("repo_url") or "").strip()
    update_if_exists = bool(payload.get("update_if_exists", True))
    install_deps = bool(payload.get("install_dependencies", True))
    try:
        result = install_custom_node(repo_url, update_if_exists=update_if_exists, install_deps=install_deps)
    except Exception as exc:
        log_event(f"Custom node install failed for '{repo_url}': {exc}")
        return json_ok({"ok": False, "error": str(exc)}, status=400)

    log_event(
        f"Custom node install requested for '{repo_url}', status={result.get('status')}, "
        f"ok={result.get('ok')}, destination={result.get('destination')}"
    )
    return json_ok(result, status=200 if result.get("ok") else 500)


@PromptServer.instance.routes.post("/workflow-assets/apply-resolution")
async def workflow_assets_apply_resolution(request):
    payload = await request.json()
    workflow_name = payload.get("workflow_name") or "current-workflow.json"
    model_name = payload["model_name"]
    category = payload.get("category", "other")
    kind = payload.get("kind", "FILE")
    source = payload.get("source", "PASTE_URL_HERE")
    target = payload.get("target") or target_from_category(model_name, category)
    note = payload.get("note", "")

    entry = upsert_registry_entry(
        model_name,
        {"kind": kind, "source": source, "target": target, "note": note},
        write_back=False,
    )
    registry = get_registry()
    registry[model_name] = entry
    save_registry(registry)

    manifest_path = manifest_path_for_workflow(workflow_name)
    manifest_entries = parse_manifest(manifest_path)
    replaced = False
    for manifest_entry in manifest_entries:
        if manifest_entry["model_name"] == model_name:
            manifest_entry["kind"] = kind
            manifest_entry["source"] = source
            manifest_entry["target"] = target
            manifest_entry["note"] = note
            replaced = True
    if not replaced:
        manifest_entries.append(
            {"model_name": model_name, "kind": kind, "source": source, "target": target, "note": note}
        )
    write_manifest(manifest_path, workflow_name, manifest_entries)
    sync_registry_from_manifests(write_back=True)
    log_event(f"Applied resolution for '{model_name}' in workflow '{workflow_name}' -> {source}")
    return json_ok({"ok": True, "entry": entry, "manifest_path": str(manifest_path)})
