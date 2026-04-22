from __future__ import annotations

import difflib
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from .config import ALIASES_PATH, POPULAR_MODELS_PATH
from .storage import read_json

REQUEST_HEADERS = {
    "User-Agent": "WorkflowAssetAgent/1.0 (+ComfyUI)",
    "Accept": "application/json",
}

HTML_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', flags=re.IGNORECASE)
GENERIC_MODEL_TOKENS = {
    "model",
    "models",
    "lora",
    "loras",
    "distilled",
    "transformer",
    "input",
    "scaled",
    "only",
    "main",
    "blob",
    "resolve",
    "download",
    "file",
    "files",
}


def target_from_category(model_name: str, category: str) -> str:
    category = category.strip("/").replace("\\", "/") or "other"
    return f"{category}/{model_name}"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", Path(str(value)).name.lower())


def _path_name(value: str) -> str:
    return Path(str(value).replace("\\", "/")).name


def _match_score(model_name: str, candidate_name: str) -> float:
    left = normalize_name(model_name)
    right = normalize_name(candidate_name)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _name_tokens(value: str) -> set[str]:
    text = _path_name(value).lower().replace("\\", "/")
    return {token for token in re.split(r"[^a-z0-9]+", text) if len(token) >= 2}


def _token_overlap_score(model_name: str, candidate_name: str) -> float:
    left = _name_tokens(model_name)
    right = _name_tokens(candidate_name)
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left), 1)


def _salient_tokens(value: str) -> set[str]:
    tokens = _name_tokens(value)
    return {
        token
        for token in tokens
        if token not in GENERIC_MODEL_TOKENS and not re.fullmatch(r"(fp\d+|bf16|gguf|v\d+)", token)
    }


def _semantic_overlap_score(model_name: str, candidate_name: str) -> float:
    left = _salient_tokens(model_name)
    right = _salient_tokens(candidate_name)
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left), 1)


def _is_safe_autoresolve_candidate(model_name: str, candidate: dict) -> bool:
    if not candidate:
        return False
    if candidate.get("exact_filename_match"):
        return True
    semantic = float(candidate.get("semantic_overlap", 0) or 0)
    overlap = float(candidate.get("token_overlap", 0) or 0)
    score = float(candidate.get("match_score", 0) or 0)
    if semantic >= 0.74:
        return True
    if semantic >= 0.58 and overlap >= 0.62:
        return True
    if overlap >= 0.82 and score >= 0.86:
        return True
    if score >= 0.97:
        return True
    return False


def _normalize_huggingface_file_url(url: str) -> str:
    value = str(url or "").strip()
    if "huggingface.co" not in value:
        return value
    return value.replace("/blob/", "/resolve/")


def _looks_like_model_file(name: str) -> bool:
    lower = _path_name(name).lower()
    return lower.endswith((".safetensors", ".gguf", ".pth", ".pt", ".onnx", ".bin"))


def _stem(value: str) -> str:
    return Path(str(value)).name.rsplit(".", 1)[0]


def _unique_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _candidate_entry(provider: str, label: str, url: str, model_name: str, file_name: str = "") -> dict:
    match_name = file_name or label or url
    score = _match_score(model_name, match_name)
    overlap = _token_overlap_score(model_name, match_name)
    semantic = _semantic_overlap_score(model_name, match_name)
    normalized_url = _normalize_huggingface_file_url(url)
    return {
        "provider": provider,
        "label": label,
        "candidate_url": normalized_url,
        "exact_filename_match": score >= 0.999,
        "match_score": round(score, 4),
        "token_overlap": round(overlap, 4),
        "semantic_overlap": round(semantic, 4),
        "file_name": _path_name(file_name or label or ""),
    }


def _direct_file_candidate(provider: str, url: str, model_name: str) -> dict | None:
    parsed = urlparse(url)
    file_name = _path_name(parsed.path)
    if not _looks_like_model_file(file_name):
        return None
    candidate = _candidate_entry(provider, url, url, model_name, file_name)
    if candidate.get("exact_filename_match") or candidate.get("token_overlap", 0) >= 0.34 or candidate.get("match_score", 0) >= 0.5:
        return candidate
    return None


def _decode_search_result_url(url: str) -> str:
    raw = html.unescape(str(url or "").strip())
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    parsed = urlparse(raw)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        redirected = parse_qs(parsed.query).get("uddg", [""])[0]
        if redirected:
            return unquote(redirected)
    return raw


def _candidate_sort_key(item: dict) -> tuple:
    return (
        item.get("provider", "").startswith("note:"),
        item.get("exact_filename_match", False),
        item.get("semantic_overlap", 0),
        item.get("token_overlap", 0),
        item.get("match_score", 0),
        "huggingface" in str(item.get("provider", "")),
    )


def _huggingface_search_queries(model_name: str) -> list[str]:
    variants = alias_variants(model_name)
    stem = _stem(model_name)
    stem_variants = [_stem(item) for item in variants]

    split_dash = [part for part in re.split(r"[-_]+", stem) if part]
    split_space = [part for part in re.split(r"[^a-zA-Z0-9.]+", stem) if part]

    queries = [
        model_name,
        stem,
        stem.replace("-", " "),
        stem.replace("_", " "),
        *variants,
        *stem_variants,
    ]

    if len(split_dash) >= 2:
        queries.extend(
            [
                "-".join(split_dash[:2]),
                " ".join(split_dash[:2]),
                "-".join(split_dash[:3]),
                " ".join(split_dash[:3]),
            ]
        )
    if len(split_dash) >= 4:
        queries.extend(
            [
                "-".join(split_dash[:4]),
                " ".join(split_dash[:4]),
            ]
        )

    major_version = re.match(r"^([a-z]+)-(\d+(?:\.\d+)?)", stem, flags=re.IGNORECASE)
    if major_version:
        queries.extend(
            [
                f"{major_version.group(1)}-{major_version.group(2)}",
                f"{major_version.group(1)} {major_version.group(2)}",
            ]
        )

    semantic_tokens = [
        token
        for token in split_space
        if len(token) >= 2 and token.lower() not in {"fp8", "fp16", "bf16", "scaled", "mixed"}
    ]
    if semantic_tokens:
        queries.append(" ".join(semantic_tokens[:5]))
    if len(semantic_tokens) >= 3:
        queries.append(" ".join(semantic_tokens[:3]))

    return _unique_preserve(queries)


def _huggingface_deep_queries(model_name: str) -> list[str]:
    queries = list(_huggingface_search_queries(model_name))
    stem = _stem(model_name)
    stem_tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", stem) if token]
    lower_tokens = [token.lower() for token in stem_tokens]

    family_queries: list[str] = []
    if lower_tokens:
        family_queries.append(lower_tokens[0])
    if len(lower_tokens) >= 2:
        family_queries.append("-".join(lower_tokens[:2]))
        family_queries.append(" ".join(lower_tokens[:2]))
    if len(lower_tokens) >= 3:
        family_queries.append("-".join(lower_tokens[:3]))
        family_queries.append(" ".join(lower_tokens[:3]))

    # Repo-level discovery queries help when the exact filename is not indexed directly.
    for token in lower_tokens:
        if token in {"safetensors", "bf16", "fp8", "fp16", "gguf", "pth", "onnx"}:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            continue
        family_queries.append(token)

    return _unique_preserve([*queries, *family_queries])


def _related_model_names(model_name: str, note_model_hints: list[str] | None = None) -> list[str]:
    return _unique_preserve([model_name, *(note_model_hints or [])])


def _related_huggingface_queries(model_name: str, note_model_hints: list[str] | None = None) -> list[str]:
    queries: list[str] = []
    for related_name in _related_model_names(model_name, note_model_hints):
        queries.extend(_huggingface_search_queries(related_name))
        queries.extend(_huggingface_deep_queries(related_name))
    return _unique_preserve(queries)


def _fetch_huggingface_repo(repo_id: str, timeout: int = 20) -> dict:
    response = requests.get(
        f"https://huggingface.co/api/models/{repo_id}",
        timeout=timeout,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()
    return response.json()


def _fetch_huggingface_search(query: str, limit: int = 8, timeout: int = 20) -> list[dict]:
    url = f"https://huggingface.co/api/models?search={quote(query)}&limit={limit}&full=true"
    response = requests.get(url, timeout=timeout, headers=REQUEST_HEADERS)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def load_aliases() -> dict:
    data = read_json(ALIASES_PATH, {})
    return data.get("aliases", {}) if isinstance(data, dict) else {}


def load_alias_patterns() -> list[dict]:
    data = read_json(ALIASES_PATH, {})
    return data.get("patterns", []) if isinstance(data, dict) else []


def load_popular_models() -> dict:
    data = read_json(POPULAR_MODELS_PATH, {})
    return data.get("models", {}) if isinstance(data, dict) else {}


def alias_variants(model_name: str) -> list[str]:
    aliases = load_aliases()
    patterns = load_alias_patterns()
    variants = {model_name}
    normalized = normalize_name(model_name)

    for canonical, alt_names in aliases.items():
        names = [canonical, *(alt_names or [])]
        if any(normalize_name(name) == normalized for name in names):
            variants.update(names)

    for item in patterns:
        pattern = item.get("pattern", "")
        base = item.get("base", "")
        if not pattern or not base:
            continue
        try:
            if re.match(pattern, model_name, flags=re.IGNORECASE):
                python_base = re.sub(r"\$(\d+)", r"\\\1", base)
                variants.add(re.sub(pattern, python_base, model_name, flags=re.IGNORECASE))
        except re.error:
            continue

    return sorted(variants)


def popular_match(model_name: str, category: str) -> dict | None:
    models = load_popular_models()
    variants = alias_variants(model_name)

    for variant in variants:
        if variant in models:
            item = models[variant]
            return {
                "decision": "resolved",
                "provider": "popular_models",
                "source": item.get("url", ""),
                "target": target_from_category(model_name, category),
                "reason": f"Matched curated model entry via alias '{variant}'",
            }

    normalized = normalize_name(model_name)
    best_name = ""
    best_score = 0.0
    for candidate_name in models:
        score = difflib.SequenceMatcher(None, normalized, normalize_name(candidate_name)).ratio()
        if score > best_score:
            best_name = candidate_name
            best_score = score

    if best_name and best_score >= 0.88:
        item = models[best_name]
        return {
            "decision": "resolved",
            "provider": "popular_models_fuzzy",
            "source": item.get("url", ""),
            "target": target_from_category(model_name, category),
            "reason": f"Fuzzy matched curated model '{best_name}' ({best_score:.0%})",
        }

    return None


def build_entry(model_name: str, category: str, registry_entry: dict | None = None) -> dict:
    registry_entry = registry_entry or {}
    kind = registry_entry.get("kind", "FILE")
    source = registry_entry.get("source", "PASTE_URL_HERE")
    target = registry_entry.get("target") or target_from_category(model_name, category)
    return {
        "model_name": model_name,
        "category": category,
        "kind": kind,
        "source": source,
        "target": target,
        "note": registry_entry.get("note", ""),
        "resolved": bool(source and not str(source).upper().startswith("PASTE_")),
    }


def _huggingface_repo_id(note_url: str) -> str:
    parsed = urlparse(note_url)
    if "huggingface.co" not in parsed.netloc:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return ""
    if parts[0] in {"api", "spaces", "datasets"}:
        return ""
    return "/".join(parts[:2])


def _huggingface_candidates_from_url(url: str, model_name: str, timeout: int = 20, provider: str = "web:huggingface") -> list[dict]:
    results: list[dict] = []
    direct = _direct_file_candidate(provider, url, model_name)
    if direct:
        results.append(direct)
    repo_id = _huggingface_repo_id(url)
    if repo_id:
        data = _fetch_huggingface_repo(repo_id, timeout=timeout)
        siblings = data.get("siblings") or []
        repo_candidates = _huggingface_file_candidates(model_name, repo_id, siblings, relaxed=True)
        for item in repo_candidates[:8]:
            item["provider"] = provider
        results.extend(repo_candidates[:8])
    elif not direct:
        results.append(_candidate_entry(provider, url, url, model_name))
    return dedupe_candidates(results)


def _huggingface_direct_from_repo(model_name: str, repo_id: str, timeout: int = 20) -> str:
    data = _fetch_huggingface_repo(repo_id, timeout=timeout)
    siblings = data.get("siblings") or []
    variants = {Path(item).name for item in alias_variants(model_name)}
    for sibling in siblings:
        candidate_name = sibling.get("rfilename") or sibling.get("path") or ""
        if Path(candidate_name).name in variants:
            return f"https://huggingface.co/{repo_id}/resolve/main/{candidate_name}"
    return ""


def _huggingface_file_candidates(model_name: str, repo_id: str, siblings: list[dict], *, relaxed: bool = False) -> list[dict]:
    candidates: list[dict] = []
    for sibling in siblings or []:
        candidate_name = sibling.get("rfilename") or sibling.get("path") or ""
        if not _looks_like_model_file(candidate_name):
            continue
        score = _match_score(model_name, candidate_name)
        overlap = _token_overlap_score(model_name, candidate_name)
        if not relaxed and score < 0.72 and overlap < 0.4 and _path_name(model_name) not in candidate_name:
            continue
        if relaxed and score < 0.35 and overlap < 0.25 and _path_name(model_name) not in candidate_name:
            continue
        candidates.append(
            _candidate_entry(
                "huggingface",
                f"{repo_id}/{candidate_name}",
                f"https://huggingface.co/{repo_id}/resolve/main/{candidate_name}",
                model_name,
                candidate_name,
            )
        )
    candidates.sort(
        key=lambda item: (
            item.get("exact_filename_match", False),
            item.get("token_overlap", 0),
            item.get("match_score", 0),
        ),
        reverse=True,
    )
    return candidates


def _civitai_model_id(url: str) -> str:
    parsed = urlparse(url)
    if "civitai.com" not in parsed.netloc:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "models":
        return parts[1]
    return ""


def _civitai_candidates_from_model(model_name: str, item: dict) -> list[dict]:
    results: list[dict] = []
    model_id = item.get("id")
    model_name_label = item.get("name") or str(model_id)
    for version in item.get("modelVersions", []) or []:
        version_name = version.get("name") or ""
        for file in version.get("files", []) or []:
            file_name = file.get("name") or file.get("filename") or ""
            if not _looks_like_model_file(file_name):
                continue
            score = _match_score(model_name, file_name)
            overlap = _token_overlap_score(model_name, file_name)
            if score < 0.72 and overlap < 0.4 and _path_name(model_name) not in file_name:
                continue
            download_url = file.get("downloadUrl") or f"https://civitai.com/models/{model_id}"
            results.append(
                _candidate_entry(
                    "civitai",
                    f"{model_name_label} / {version_name} / {file_name}".strip(" /"),
                    download_url,
                    model_name,
                    file_name,
                )
            )
    if not results and model_id:
        results.append(
            _candidate_entry(
                "civitai",
                model_name_label,
                f"https://civitai.com/models/{model_id}",
                model_name,
            )
        )
    results.sort(
        key=lambda item: (
            item.get("exact_filename_match", False),
            item.get("token_overlap", 0),
            item.get("match_score", 0),
        ),
        reverse=True,
    )
    return results


def search_note_urls(
    model_name: str,
    category: str,
    note_urls: list[str] | None,
    note_model_hints: list[str] | None = None,
    timeout: int = 20,
) -> list[dict]:
    results: list[dict] = []
    for note_url in note_urls or []:
        url = (note_url or "").strip()
        if not url:
            continue
        try:
            if "huggingface.co" in url:
                direct = _direct_file_candidate("note:huggingface", url, model_name)
                if direct:
                    results.append(direct)
                repo_id = _huggingface_repo_id(url)
                if repo_id:
                    response = requests.get(
                        f"https://huggingface.co/api/models/{repo_id}",
                        timeout=timeout,
                        headers=REQUEST_HEADERS,
                    )
                    response.raise_for_status()
                    data = response.json()
                    siblings = data.get("siblings") or []
                    repo_candidates = _huggingface_file_candidates(model_name, repo_id, siblings, relaxed=True)
                    for item in repo_candidates[:8]:
                        item["provider"] = "note:huggingface"
                    results.extend(repo_candidates[:8])
                else:
                    results.append(_candidate_entry("note:huggingface", url, url, model_name))
                continue
        except Exception:
            results.append(_candidate_entry("note", url, url, model_name))
    return dedupe_candidates(results)


def search_huggingface(
    model_name: str,
    note_model_hints: list[str] | None = None,
    limit: int = 8,
    timeout: int = 20,
) -> list[dict]:
    results: list[dict] = []
    repo_cache: dict[str, dict] = {}
    used_queries: set[str] = set()
    for query in _related_huggingface_queries(model_name, note_model_hints)[:14]:
        used_queries.add(query.lower())
        data = _fetch_huggingface_search(query, limit=limit, timeout=timeout)
        for item in data:
            model_id = item.get("id")
            if not model_id:
                continue

            repo_data = repo_cache.get(model_id)
            if repo_data is None:
                repo_data = item if item.get("siblings") else _fetch_huggingface_repo(model_id, timeout=timeout)
                repo_cache[model_id] = repo_data

            siblings = repo_data.get("siblings") or []
            repo_results = _huggingface_file_candidates(model_name, model_id, siblings)
            if not repo_results and siblings:
                # Broader repo matches are still useful when the filename is nested or not an exact token match.
                repo_results = _huggingface_file_candidates(model_name, model_id, siblings, relaxed=True)
            if repo_results:
                results.extend(repo_results[:6])
            else:
                results.append(_candidate_entry("huggingface", model_id, f"https://huggingface.co/{model_id}", model_name))

    strong = [
        item
        for item in results
        if item.get("exact_filename_match")
        or item.get("token_overlap", 0) >= 0.55
        or item.get("match_score", 0) >= 0.88
    ]
    if not strong:
        for query in _related_huggingface_queries(model_name, note_model_hints)[:16]:
            if query.lower() in used_queries:
                continue
            data = _fetch_huggingface_search(query, limit=max(limit, 12), timeout=timeout)
            for item in data:
                model_id = item.get("id")
                if not model_id:
                    continue
                repo_data = repo_cache.get(model_id)
                if repo_data is None:
                    repo_data = item if item.get("siblings") else _fetch_huggingface_repo(model_id, timeout=timeout)
                    repo_cache[model_id] = repo_data
                siblings = repo_data.get("siblings") or []
                repo_results = _huggingface_file_candidates(model_name, model_id, siblings, relaxed=True)
                if repo_results:
                    results.extend(repo_results[:6])
                elif any(token in normalize_name(model_id) for token in _name_tokens(model_name)):
                    results.append(_candidate_entry("huggingface:repo", model_id, f"https://huggingface.co/{model_id}", model_name))
    return dedupe_candidates(results)


def search_huggingface_deep(
    model_name: str,
    note_urls: list[str] | None = None,
    note_model_hints: list[str] | None = None,
    limit: int = 8,
    timeout: int = 20,
) -> list[dict]:
    results: list[dict] = []
    repo_cache: dict[str, dict] = {}
    seen_repos: set[str] = set()
    noted_repo_ids = [
        repo_id
        for repo_id in (_huggingface_repo_id(url) for url in (note_urls or []))
        if repo_id
    ]

    for repo_id in _unique_preserve(noted_repo_ids):
        try:
            repo_data = _fetch_huggingface_repo(repo_id, timeout=timeout)
            repo_cache[repo_id] = repo_data
            siblings = repo_data.get("siblings") or []
            repo_results = _huggingface_file_candidates(model_name, repo_id, siblings, relaxed=True)
            if repo_results:
                for item in repo_results[:limit]:
                    item["provider"] = "note:huggingface"
                results.extend(repo_results[:limit])
        except Exception:
            continue

    for query in _related_huggingface_queries(model_name, note_model_hints)[:20]:
        try:
            data = _fetch_huggingface_search(query, limit=max(limit, 12), timeout=timeout)
        except Exception:
            continue
        for item in data:
            model_id = item.get("id")
            if not model_id or model_id in seen_repos:
                continue
            seen_repos.add(model_id)

            repo_data = repo_cache.get(model_id)
            if repo_data is None:
                try:
                    repo_data = item if item.get("siblings") else _fetch_huggingface_repo(model_id, timeout=timeout)
                except Exception:
                    continue
                repo_cache[model_id] = repo_data

            siblings = repo_data.get("siblings") or []
            repo_results = _huggingface_file_candidates(model_name, model_id, siblings, relaxed=True)
            if repo_results:
                results.extend(repo_results[:limit])
            elif any(token in normalize_name(model_id) for token in _name_tokens(model_name)):
                results.append(_candidate_entry("huggingface:repo", model_id, f"https://huggingface.co/{model_id}", model_name))

    results = dedupe_candidates(results)
    results.sort(key=_candidate_sort_key, reverse=True)
    return results


def search_web_huggingface(
    model_name: str,
    note_urls: list[str] | None = None,
    note_model_hints: list[str] | None = None,
    limit: int = 8,
    timeout: int = 20,
) -> list[dict]:
    results: list[dict] = []
    seen_urls: set[str] = set()
    queries: list[str] = []

    for related_name in _related_model_names(model_name, note_model_hints):
        queries.append(f"\"{related_name}\" site:huggingface.co")
        queries.append(f"{related_name} huggingface")
    for url in note_urls or []:
        repo_id = _huggingface_repo_id(url)
        if repo_id:
            queries.append(f"\"{model_name}\" \"{repo_id}\"")

    for query in _unique_preserve(queries)[:10]:
        try:
            response = requests.get(
                "https://duckduckgo.com/html/",
                params={"q": query},
                timeout=timeout,
                headers={**REQUEST_HEADERS, "Accept": "text/html,application/xhtml+xml"},
            )
            response.raise_for_status()
        except Exception:
            continue

        for raw_href in HTML_HREF_RE.findall(response.text):
            decoded = _decode_search_result_url(raw_href)
            if not decoded or "huggingface.co" not in decoded:
                continue
            normalized = decoded.split("#", 1)[0]
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            try:
                results.extend(_huggingface_candidates_from_url(normalized, model_name, timeout=timeout, provider="web:huggingface"))
            except Exception:
                continue
            if len(results) >= limit * 2:
                break
        if len(results) >= limit * 2:
            break

    results = dedupe_candidates(results)
    results.sort(key=_candidate_sort_key, reverse=True)
    return results[: max(limit, 8)]


def search_civitai(model_name: str, limit: int = 8, timeout: int = 20) -> list[dict]:
    results = []
    for query in alias_variants(model_name)[:4]:
        url = f"https://civitai.com/api/v1/models?query={quote(query)}&limit={limit}"
        response = requests.get(url, timeout=timeout, headers=REQUEST_HEADERS)
        response.raise_for_status()
        data = response.json()
        for item in data.get("items", []):
            results.extend(_civitai_candidates_from_model(model_name, item)[:6])
    return results


def dedupe_candidates(candidates: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for candidate in candidates:
        key = (candidate.get("provider", ""), candidate.get("candidate_url", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    deduped.sort(key=_candidate_sort_key, reverse=True)
    return deduped


def _prefer_huggingface_candidates(candidates: list[dict]) -> list[dict]:
    candidates = dedupe_candidates(candidates)
    hf_candidates = [item for item in candidates if "huggingface" in str(item.get("provider", ""))]
    note_hf_candidates = [item for item in candidates if str(item.get("provider", "")).startswith("note:huggingface")]

    strong_hf = [
        item
        for item in hf_candidates
        if item.get("exact_filename_match")
        or item.get("token_overlap", 0) >= 0.55
        or item.get("match_score", 0) >= 0.88
    ]
    if strong_hf:
        return dedupe_candidates(hf_candidates + [item for item in candidates if str(item.get("provider", "")).startswith("note:")])

    if note_hf_candidates:
        return dedupe_candidates(hf_candidates + [item for item in candidates if str(item.get("provider", "")).startswith("note:")])

    return dedupe_candidates([item for item in candidates if "civitai" not in str(item.get("provider", ""))])


def heuristic_pick(model_name: str, category: str, candidates: list[dict]) -> dict:
    candidates = _prefer_huggingface_candidates(candidates)
    curated = popular_match(model_name, category)
    if curated:
        curated["mode"] = "heuristic"
        return curated
    note_candidates = [c for c in candidates if c.get("provider", "").startswith("note:")]
    if note_candidates:
        strong_note = [
            c
            for c in note_candidates
            if _is_safe_autoresolve_candidate(model_name, c)
        ]
        if strong_note:
            best = strong_note[0]
            return {
                "decision": "resolved",
                "provider": best["provider"],
                "source": best["candidate_url"],
                "target": target_from_category(model_name, category),
                "reason": "Matched workflow note candidate before generic internet search",
                "mode": "heuristic",
            }
    note_exact = [c for c in candidates if c.get("provider", "").startswith("note:") and c.get("exact_filename_match")]
    if note_exact:
        best = note_exact[0]
        return {
            "decision": "resolved",
            "provider": best["provider"],
            "source": best["candidate_url"],
            "target": target_from_category(model_name, category),
            "reason": "Matched exact filename from workflow note URL",
            "mode": "heuristic",
        }
    exact = [c for c in candidates if c.get("exact_filename_match")]
    if exact:
        best = exact[0]
        return {
            "decision": "resolved",
            "provider": best["provider"],
            "source": best["candidate_url"],
            "target": target_from_category(model_name, category),
            "reason": "Exact filename match in search candidates",
            "mode": "heuristic",
        }
    normalized = normalize_name(model_name)
    best = None
    best_score = 0.0
    for candidate in candidates:
        score = max(
            candidate.get("token_overlap", 0),
            candidate.get("match_score", 0),
            difflib.SequenceMatcher(None, normalized, normalize_name(candidate.get("label") or candidate.get("candidate_url") or "")).ratio(),
        )
        if score > best_score:
            best = candidate
            best_score = score
    if best and best_score >= 0.86 and _is_safe_autoresolve_candidate(model_name, best):
        return {
            "decision": "resolved",
            "provider": best["provider"],
            "source": best["candidate_url"],
            "target": target_from_category(model_name, category),
            "reason": f"Fuzzy matched search candidate ({best_score:.0%})",
            "mode": "heuristic",
        }
    return {
        "decision": "unresolved",
        "provider": "",
        "source": "",
        "target": target_from_category(model_name, category),
        "reason": "No exact filename match found in notes or internet search",
        "mode": "heuristic",
    }


def ai_pick(
    model_name: str,
    category: str,
    candidates: list[dict],
    settings: dict,
    *,
    note_urls: list[str] | None = None,
    note_model_hints: list[str] | None = None,
) -> dict:
    candidates = _prefer_huggingface_candidates(candidates)
    if not candidates:
        return heuristic_pick(model_name, category, candidates)
    total_candidates = len(candidates)
    candidates = candidates[:24]
    ai_cfg = settings.get("ai", {})
    model = (ai_cfg.get("model") or "").strip()
    api_key = os.environ.get(ai_cfg.get("api_key_env", "OPENAI_API_KEY"), "").strip()
    if not model or not api_key:
        return heuristic_pick(model_name, category, candidates)

    base_url = ai_cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                "You resolve ComfyUI model download sources. Hugging Face is strongly preferred. "
                    "Use only Hugging Face and workflow note evidence. "
                    "Prefer workflow note links when they point to Hugging Face repos containing the requested file. "
                    "Use note model hints as same-family clues. "
                    "If a note points to a sibling model from the same family or repo, search that repo for the exact requested filename first. "
                    "Return JSON with keys: decision, source, provider, reason."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "model_name": model_name,
                        "category": category,
                        "note_urls": note_urls or [],
                        "note_model_hints": note_model_hints or [],
                        "candidates": candidates,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    request_body = json.dumps(payload, ensure_ascii=False)
    prompt_estimate = max(1, len(request_body) // 4)
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            **REQUEST_HEADERS,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    parsed = json.loads(data["choices"][0]["message"]["content"])
    decision = str(parsed.get("decision", "unresolved")).strip().lower()
    if decision in {"use", "pick", "choose", "selected", "select", "apply"}:
        decision = "resolved"
    if decision not in {"resolved", "unresolved"}:
        decision = "resolved" if parsed.get("source") else "unresolved"

    source = str(parsed.get("source", "") or "").strip()
    provider = str(parsed.get("provider", "") or "").strip()
    source = _normalize_huggingface_file_url(source)
    if source and not _looks_like_model_file(source):
        matched = next(
            (
                item for item in candidates
                if str(item.get("candidate_url", "")).strip() == source
                or str(item.get("label", "")).strip() == source
            ),
            None,
        )
        if matched and _looks_like_model_file(matched.get("candidate_url", "")):
            source = matched.get("candidate_url", "")
            provider = provider or matched.get("provider", "")
        elif "huggingface.co" in source:
            try:
                recovered = _huggingface_candidates_from_url(source, model_name, provider="ai:huggingface")
                if recovered:
                    source = recovered[0].get("candidate_url", source)
                    provider = provider or recovered[0].get("provider", "")
            except Exception:
                pass
    matched_candidate = None
    if source:
        matched_candidate = next(
            (
                item for item in candidates
                if str(item.get("candidate_url", "")).strip() == source
                or str(item.get("label", "")).strip() == source
            ),
            None,
        )
    if decision == "resolved" and matched_candidate and not _is_safe_autoresolve_candidate(model_name, matched_candidate):
        decision = "unresolved"
        source = ""
        provider = ""
        parsed["reason"] = (
            f"Candidate rejected after AI review because semantic match is too weak for '{model_name}'. "
            f"Manual review is required."
        )
    usage = data.get("usage") or {}
    return {
        "decision": decision,
        "provider": provider,
        "source": source,
        "target": target_from_category(model_name, category),
        "reason": parsed.get("reason", ""),
        "mode": "ai",
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", prompt_estimate),
            "completion_tokens": usage.get("completion_tokens", 80),
            "total_tokens": usage.get(
                "total_tokens",
                usage.get("prompt_tokens", prompt_estimate) + usage.get("completion_tokens", 80),
            ),
            "candidates_considered": len(candidates),
            "candidates_found": total_candidates,
        },
    }
