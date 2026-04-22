from __future__ import annotations

import difflib
import re
from collections import OrderedDict

URL_RE = re.compile(r"https?://[^\s<>\"]+")
MODEL_REF_RE = re.compile(r"\b[^\s<>\"]+\.(?:safetensors|gguf|pth|pt|onnx)\b", flags=re.IGNORECASE)

UNET_NODE_TYPES = {
    "UNETLoader",
    "LoaderGGUF",
    "UnetLoaderGGUFDisTorchMultiGPU",
    "UnetLoaderGGUFDisTorch2MultiGPU",
    "UnetLoaderGGUFMultiGPU",
    "WanVideoModelLoader",
    "WanVideoVACEModelSelect",
}
VAE_NODE_TYPES = {"VAELoader", "VAELoaderKJ", "WanVideoVAELoader", "LTXVAudioVAELoader"}
TEXT_NODE_TYPES = {
    "CLIPLoader",
    "DualCLIPLoaderGGUF",
    "LTXAVTextEncoderLoader",
    "LTXVGemmaCLIPModelLoader",
    "LoadWanVideoT5TextEncoder",
}
LORA_NODE_TYPES = {"LoraLoaderModelOnly", "WanVideoLoraSelect"}
CHECKPOINT_NODE_TYPES = {"CheckpointLoaderSimple", "easy ckptNames"}

KNOWN_SPECIAL_NAMES = {"Qwen3-VL-4B-Instruct", "qwen2.5vl:7b"}
MODEL_SUFFIXES = (".safetensors", ".gguf", ".pth", ".pt", ".onnx")
MODEL_NODE_TYPES = UNET_NODE_TYPES | VAE_NODE_TYPES | TEXT_NODE_TYPES | LORA_NODE_TYPES | CHECKPOINT_NODE_TYPES


def is_model_ref(value: str) -> bool:
    stripped = str(value).strip()
    if stripped.startswith(("http://", "https://")):
        return False
    return stripped.endswith(MODEL_SUFFIXES) or stripped in KNOWN_SPECIAL_NAMES


def canonical_model_name(value: str) -> str:
    raw = str(value).strip().replace("\\", "/")
    return raw.split("/")[-1]


def _clean_url(value: str) -> str:
    return str(value or "").strip().rstrip(").,;]")


def _path_name(value: str) -> str:
    return canonical_model_name(value)


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _path_name(value).lower())


def _name_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", _path_name(value).lower()) if len(token) >= 2}


def _match_score(left: str, right: str) -> float:
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return difflib.SequenceMatcher(None, left_norm, right_norm).ratio()


def _token_overlap_score(left: str, right: str) -> float:
    left_tokens = _name_tokens(left)
    right_tokens = _name_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), 1)


def _match_strength(left: str, right: str) -> float:
    return max(_match_score(left, right), _token_overlap_score(left, right))


def note_like_node(node_type: str) -> bool:
    lowered = str(node_type or "").strip().lower()
    return any(token in lowered for token in ("note", "markdown", "readme", "helper", "doc"))


def reference_like_node(node_type: str, text_blob: str, urls: list[str], model_refs: list[str]) -> bool:
    lowered = str(node_type or "").strip().lower()
    if node_type in MODEL_NODE_TYPES:
        return False
    if "loader" in lowered or lowered.startswith("load"):
        return False
    if note_like_node(node_type):
        return True
    if urls:
        return True
    if len(model_refs) >= 2:
        return True
    return lowered in {"string", "text", "showtext"} and (urls or model_refs)


def iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_strings(item)
    elif isinstance(obj, dict):
        for item in obj.values():
            yield from iter_strings(item)


def iter_model_metadata(node: dict):
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return
    models = properties.get("models")
    if not isinstance(models, list):
        return
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        directory = str(item.get("directory") or "").strip()
        if not name and not url:
            continue
        yield {
            "name": canonical_model_name(name) if name else "",
            "url": url,
            "directory": directory,
        }


def iter_node_collections(obj):
    if isinstance(obj, dict):
        nodes = obj.get("nodes")
        if isinstance(nodes, list):
            yield nodes
        for value in obj.values():
            yield from iter_node_collections(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_node_collections(item)


def category_for(node_type: str, model_name: str) -> str:
    if node_type in CHECKPOINT_NODE_TYPES:
        return "checkpoints"
    if node_type in VAE_NODE_TYPES:
        return "vae"
    if node_type in TEXT_NODE_TYPES:
        return "text_encoders"
    if node_type in LORA_NODE_TYPES:
        return "loras"
    if "DepthAnything" in node_type or model_name == "da3_large.safetensors":
        return "depthanything3"
    if "MelBandRoFormer" in node_type:
        return "whisper"
    if "DWPreprocessor" in node_type:
        return "other/dwpose"
    if model_name == "Qwen3-VL-4B-Instruct":
        return "LLM/Qwen-VL"
    if node_type in UNET_NODE_TYPES or model_name.lower().endswith(".gguf"):
        return "diffusion_models"
    if model_name.lower().endswith(".onnx") or model_name.lower().endswith(".pth"):
        return "other"
    return "other"


def scan_workflow(workflow: dict) -> list[dict]:
    seen_collections: set[int] = set()
    nodes: list[dict] = []
    for collection in iter_node_collections(workflow):
        marker = id(collection)
        if marker in seen_collections:
            continue
        seen_collections.add(marker)
        nodes.extend(item for item in collection if isinstance(item, dict))
    results: OrderedDict[str, dict] = OrderedDict()
    note_nodes: list[dict] = []

    for node in nodes:
        node_type = node.get("type", "")
        node_id = node.get("id", "unknown")
        values = list(iter_strings(node))
        text_blob = "\n".join(values)
        urls = [_clean_url(url) for url in URL_RE.findall(text_blob)]
        model_refs = [canonical_model_name(match) for match in MODEL_REF_RE.findall(text_blob)]
        model_refs = list(OrderedDict.fromkeys(model_refs))
        if reference_like_node(node_type, text_blob, urls, model_refs):
            pos = node.get("pos") or [0, 0]
            note_nodes.append(
                {
                    "node_id": node_id,
                    "urls": urls,
                    "model_refs": model_refs,
                    "text_blob": text_blob[:4000],
                    "x": float(pos[0]) if len(pos) > 0 else 0.0,
                    "y": float(pos[1]) if len(pos) > 1 else 0.0,
                }
            )

    for node in nodes:
        node_type = node.get("type", "")
        node_id = node.get("id", "unknown")
        pos = node.get("pos") or [0, 0]
        node_x = float(pos[0]) if len(pos) > 0 else 0.0
        node_y = float(pos[1]) if len(pos) > 1 else 0.0

        containers = [
            ("widgets_values", node.get("widgets_values", [])),
            ("properties", node.get("properties", {})),
            ("inputs", node.get("inputs", [])),
        ]

        for source_name, payload in containers:
            for value in iter_strings(payload):
                if not is_model_ref(value):
                    continue
                model_name = canonical_model_name(value)
                entry = results.setdefault(
                    model_name,
                    {
                        "model_name": model_name,
                        "category": category_for(node_type, model_name),
                        "node_types": [],
                        "sources": [],
                        "note_urls": [],
                        "note_model_hints": [],
                    },
                )
                if node_type and node_type not in entry["node_types"]:
                    entry["node_types"].append(node_type)
                source_ref = f"{source_name}@node:{node_id}:{value}"
                if source_ref not in entry["sources"]:
                    entry["sources"].append(source_ref)

                for meta in iter_model_metadata(node):
                    meta_name = meta.get("name", "")
                    meta_url = meta.get("url", "")
                    if not meta_url:
                        continue
                    if meta_name and meta_name != model_name:
                        continue
                    if meta_url not in entry["note_urls"]:
                        entry["note_urls"].append(meta_url)

                nearest = []
                for note in note_nodes:
                    dx = abs(note["x"] - node_x)
                    dy = abs(note["y"] - node_y)
                    if dx <= 1400 and dy <= 900:
                        nearest.append((dx + dy, note))
                nearest.sort(key=lambda item: item[0])

                for _, note in nearest[:3]:
                    for url in note["urls"]:
                        if url not in entry["note_urls"]:
                            entry["note_urls"].append(url)
                    for hint in note.get("model_refs", []):
                        if hint != model_name and hint not in entry["note_model_hints"]:
                            entry["note_model_hints"].append(hint)

                global_note_matches = []
                for note in note_nodes:
                    if note.get("node_id") == node_id:
                        continue
                    hint_scores = [
                        (
                            max(_token_overlap_score(model_name, hint), _match_score(model_name, hint)),
                            hint,
                        )
                        for hint in note.get("model_refs", [])
                        if hint != model_name
                    ]
                    hint_scores = [item for item in hint_scores if item[0] >= 0.58]
                    if not hint_scores:
                        continue
                    hint_scores.sort(key=lambda item: item[0], reverse=True)
                    global_note_matches.append((hint_scores[0][0], note, hint_scores))

                global_note_matches.sort(key=lambda item: item[0], reverse=True)

                for _, note, hint_scores in global_note_matches[:3]:
                    matched_hints = [hint for _, hint in hint_scores[:6]]
                    for hint in matched_hints:
                        if hint not in entry["note_model_hints"]:
                            entry["note_model_hints"].append(hint)
                    for url in note.get("urls", []):
                        url_name = canonical_model_name(url)
                        if not is_model_ref(url_name):
                            continue
                        if any(max(_token_overlap_score(url_name, hint), _match_score(url_name, hint)) >= 0.58 for hint in matched_hints):
                            if url not in entry["note_urls"]:
                                entry["note_urls"].append(url)

    return list(results.values())
