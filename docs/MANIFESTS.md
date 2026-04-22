# Manifests

Workflow Asset Agent generates and consumes plain-text manifests.

## Generated locations

- `config/manifests/by_workflow/`
- `config/manifests/generated_runtime/`

These files are user/runtime state and are intentionally ignored by `.gitignore`.

## Example line formats

```text
FILE|https://host/model.safetensors|loras/my_model.safetensors
SNAPSHOT|owner/repo|checkpoints/my_snapshot
INCLUDE|config/manifests/examples/example-models-manifest.txt
```

## Recommendation

Commit only reusable examples. Do not commit your personal generated manifests unless you explicitly want to share a workflow-specific cache of resolved sources.
