from .config import EXTENSION_ROOT, WEB_DIRECTORY
from . import routes  # noqa: F401
from .registry import sync_registry_from_manifests

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

try:
    synced = sync_registry_from_manifests(write_back=True)
    print(
        f"[WorkflowAssetAgent] Loaded from {EXTENSION_ROOT} | "
        f"registry sync touched {synced} entries"
    )
except Exception as exc:
    print(f"[WorkflowAssetAgent] Registry sync warning: {exc}")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
