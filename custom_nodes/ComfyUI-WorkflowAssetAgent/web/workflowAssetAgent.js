import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

if (window.__workflowAssetAgentLoaded) {
  console.log("[WorkflowAssetAgent.UI] already loaded, skipping duplicate registration");
} else {
  window.__workflowAssetAgentLoaded = true;

console.log("[WorkflowAssetAgent.UI] main module imported");

function normalizeWorkflowName(value) {
  if (!value) return "";
  const raw = String(value)
    .trim()
    .split("/")
    .pop()
    .split("\\")
    .pop()
    .trim()
    .replace(/^[*•\s]+/, "")
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
    .replace(/[.\s]+$/g, "");
  if (!raw) return "";
  return raw.endsWith(".json") ? raw : `${raw}.json`;
}

function workflowBaseName(value) {
  return normalizeWorkflowName(value).replace(/\.json$/i, "");
}

function workflowNameLooksUsable(value) {
  const normalized = normalizeWorkflowName(value);
  if (!normalized) return false;
  const base = workflowBaseName(normalized);
  if (!base) return false;
  const lowered = base.toLowerCase();
  const blocked = new Set([
    "current-workflow",
    "unsaved workflow",
    "unsaved-workflow",
    "workflow",
    "logs",
    "log",
    "логи",
    "ошибки",
    "errors",
    "console",
  ]);
  if (blocked.has(lowered)) return false;
  if (base.length < 4) return false;
  if (!/[a-z0-9]/i.test(base)) return false;
  if (!/[a-z]/i.test(base)) return false;
  return true;
}

function workflowNameTokens(value) {
  return workflowBaseName(value)
    .toLowerCase()
    .split(/[^a-z0-9]+/i)
    .filter((token) => token && token.length >= 3);
}

function workflowNameSimilarity(left, right) {
  const a = new Set(workflowNameTokens(left));
  const b = new Set(workflowNameTokens(right));
  if (!a.size || !b.size) return 0;
  let shared = 0;
  for (const token of a) {
    if (b.has(token)) shared += 1;
  }
  return shared / Math.max(a.size, b.size);
}

function workflowNameFromDom() {
  const selectors = [
    '[role="tab"][aria-selected="true"]',
    '[data-state="active"][role="tab"]',
    '.tabs-container [data-state="active"]',
    '.tab.active',
  ];

  for (const selector of selectors) {
    const el = document.querySelector(selector);
    const text = normalizeWorkflowName(el?.textContent);
    if (workflowNameLooksUsable(text) && text !== "current-workflow.json" && text !== "unsaved workflow.json") {
      return text;
    }
  }

  const titleText = normalizeWorkflowName(document.title?.split(" - ")[0]);
  if (workflowNameLooksUsable(titleText) && titleText !== "current-workflow.json" && titleText !== "unsaved workflow.json") {
    return titleText;
  }

  return "";
}

function guessWorkflowName() {
  const candidates = [
    app?.workflowManager?.activeWorkflow?.path,
    app?.workflowManager?.activeWorkflow?.filename,
    app?.workflowManager?.activeWorkflow?.name,
    app?.graph?.extra?.workflow?.filename,
    app?.graph?.extra?.workflow?.path,
    app?.graph?.extra?.workflow?.name,
    app?.graph?.extra?.workflowName,
    app?.graph?.extra?.ds?.filename,
    workflowNameFromDom(),
  ]
    .map(normalizeWorkflowName)
    .filter(workflowNameLooksUsable);

  return candidates[0] || "current-workflow.json";
}

function currentWorkflowData() {
  return app?.graph?.serialize?.() || { nodes: [] };
}

async function fetchJson(path, options = {}) {
  const response = await api.fetchApi(path, options);
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!response.ok) {
    throw new Error(data.stderr || data.error || data.raw || response.statusText);
  }
  return data;
}

function uiLog(...args) {
  console.log("[WorkflowAssetAgent.UI]", ...args);
}

function inferProviderMode(searchSettings = {}) {
  const explicit = String(searchSettings?.provider_mode || "").trim().toLowerCase();
  if (explicit === "hf_only") return explicit;
  return "hf_only";
}

function ensureCss() {
  if (document.querySelector('link[data-waa-css="1"]')) return;
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/extensions/ComfyUI-WorkflowAssetAgent/workflowAssetAgent.css";
  css.dataset.waaCss = "1";
  document.head.appendChild(css);
}

class WorkflowAssetAgentSidebar {
  constructor(root) {
    this.root = root;
    this.state = {
      workflowName: guessWorkflowName(),
      detectedWorkflowName: guessWorkflowName(),
      manifestPath: "",
      entries: [],
      settings: null,
      runtimeStatus: null,
      workflowWatchTimer: null,
      workflowNamePinned: false,
      scanQueue: Promise.resolve(),
      lastAutoScannedWorkflowName: "",
      activeDownloadJobId: "",
      activeDownloadReason: "",
      queueStatus: null,
      queuePollTimer: null,
    };
    this.renderShell();
    this.bind();
  }

  renderShell() {
    this.root.innerHTML = `
      <div class="waa-sidebar">
        <div class="waa-sidebar-header">
          <div>
            <div class="waa-title">Workflow Agent</div>
            <div class="waa-subtitle">Models, manifests, downloads</div>
          </div>
          <div id="waa-ai-chip" class="waa-chip waa-chip-muted">AI status unknown</div>
        </div>
        <div class="waa-topline">
          <div class="waa-topline-item">
            <span class="waa-kicker">Workflow</span>
            <span id="waa-summary-workflow" class="waa-topline-value">-</span>
          </div>
          <div class="waa-topline-item">
            <span class="waa-kicker">Search</span>
            <span id="waa-summary-provider" class="waa-topline-value">HF + Notes</span>
          </div>
          <div class="waa-topline-item">
            <span class="waa-kicker">Storage</span>
            <span id="waa-summary-download-mode" class="waa-topline-value">temp_assets</span>
          </div>
        </div>

        <section class="waa-panel waa-section">
          <div class="waa-section-title">Workflow</div>
          <div class="waa-sidebar-section">
            <label class="waa-field-label" for="waa-workflow-name">Workflow Name</label>
            <input id="waa-workflow-name" placeholder="current-workflow.json" title="Name of the currently opened workflow. It is auto-detected, but you can pin a different name manually." />
          </div>
          <div class="waa-form-grid">
            <div class="waa-sidebar-section">
              <label class="waa-field-label" for="waa-mode">Download Mode</label>
              <select id="waa-mode" title="Choose where resolved models should be downloaded. Temp Assets uses the fast temporary asset root. Custom Root downloads into the path below.">
                <option value="temp_assets">Temp Assets</option>
                <option value="custom_root">Custom Root</option>
              </select>
            </div>
            <div class="waa-sidebar-section">
              <label class="waa-field-label">Search Strategy</label>
              <div id="waa-search-strategy" class="waa-note-box" title="Workflow Agent searches workflow notes first, then Hugging Face API, then Hugging Face web discovery. Civitai is disabled.">Notes -> Hugging Face -> Web Hugging Face</div>
            </div>
          </div>
          <div class="waa-sidebar-section">
            <label class="waa-field-label" for="waa-custom-root">Custom Root</label>
            <input id="waa-custom-root" placeholder="/tmp/comfy_assets or another mapped root" title="Used only when Download Mode is set to Custom Root." />
          </div>
          <div id="waa-meta" class="waa-meta"></div>
        </section>

        <section class="waa-panel waa-section">
          <div class="waa-section-title">Actions</div>
          <div class="waa-actions-grid">
            <button class="primary" id="waa-scan" title="Read the current workflow and reuse an existing manifest when possible.">Scan Current</button>
            <button id="waa-rescan" title="Ignore the existing manifest and rebuild it from the currently opened workflow.">Regenerate Manifest</button>
            <button id="waa-find-selected" title="Search providers for the selected missing models using normal search logic.">Find Selected</button>
            <button id="waa-ai-selected" title="Use AI to rank and choose the best source for the selected missing models after search candidates are collected.">AI Resolve Selected</button>
            <button id="waa-download-selected" title="Immediately download all selected resolved models that are not already on disk.">Download Selected</button>
          </div>
        </section>

        <section class="waa-panel waa-section">
          <div class="waa-section-title">Status</div>
          <div id="waa-preflight" class="waa-note-box">No disk preflight yet.</div>
          <div id="waa-download-status" class="waa-queue-box waa-download-status">No active download.</div>
          <details class="waa-inline-section">
            <summary>Activity Log</summary>
            <div id="waa-log" class="waa-log"></div>
          </details>
        </section>

        <details class="waa-panel waa-advanced-panel">
          <summary class="waa-advanced-summary" title="AI is optional. It ranks or picks the best source after candidates are collected from search providers and related links.">AI & Advanced</summary>
          <div class="waa-advanced-body">
            <div class="waa-sidebar-section">
              <label class="waa-field-label">AI Model</label>
              <input id="waa-ai-model" placeholder="gpt-5.4-mini or another configured model" title="OpenAI-compatible model used only for AI resolve." />
            </div>
            <div class="waa-form-grid">
              <div>
                <label class="waa-field-label">API Key Env</label>
                <input id="waa-ai-key-env" placeholder="OPENAI_API_KEY" title="Environment variable name that contains the API key." />
              </div>
              <div>
                <label class="waa-field-label">Base URL</label>
                <input id="waa-ai-base-url" placeholder="https://api.openai.com/v1" title="OpenAI-compatible API base URL." />
              </div>
            </div>
            <div id="waa-ai-status-box" class="waa-queue-box">AI status unknown.</div>
            <div class="waa-button-row">
              <button id="waa-save-settings" title="Save Workflow Agent settings, including AI model, API env name, download mode, and search provider mode.">Save Settings</button>
              <button id="waa-rebuild-registry" title="Rebuild the shared registry from all saved manifests. Use this if manifests were edited or migrated outside the panel.">Rebuild Registry</button>
            </div>
          </div>
        </details>

        <details class="waa-panel waa-advanced-panel">
          <summary class="waa-advanced-summary" title="Install or update a custom node repository by Git URL. This is separate from model download logic.">Custom Nodes</summary>
          <div class="waa-advanced-body">
            <div class="waa-sidebar-section">
              <label class="waa-field-label">Repository URL</label>
              <input id="waa-custom-node-url" placeholder="https://github.com/owner/repo" title="GitHub repository URL for the custom node you want to install or update." />
              <button id="waa-install-custom-node" title="Clone or update a custom node repository and install its Python dependencies.">Install / Update Custom Node</button>
              <div id="waa-custom-node-status" class="waa-queue-box">No custom node install yet.</div>
            </div>
          </div>
        </details>

        <section class="waa-panel waa-section">
          <div class="waa-section-title">Selection</div>
          <div class="waa-list-toolbar waa-list-toolbar-native">
            <label class="waa-select-all"><input type="checkbox" id="waa-select-all"> Select all</label>
            <div class="waa-pill-row">
              <button id="waa-select-missing" class="waa-mini-btn" title="Select only models that still have no resolved source.">Missing</button>
              <button id="waa-select-downloadable" class="waa-mini-btn" title="Select models that already have a source and can be downloaded now.">Downloadable</button>
              <button id="waa-select-downloaded" class="waa-mini-btn" title="Select models that are already present on disk.">Downloaded</button>
              <button id="waa-clear-selection" class="waa-mini-btn" title="Clear the current selection.">Clear</button>
            </div>
          </div>
        </section>

        <div id="waa-rows" class="waa-card-list"></div>
      </div>
    `;
  }

  bind() {
    this.root.querySelector("#waa-scan").onclick = () => this.requestScan(false);
    this.root.querySelector("#waa-rescan").onclick = () => this.requestScan(true);
    this.root.querySelector("#waa-find-selected").onclick = () => this.findSelected();
    this.root.querySelector("#waa-ai-selected").onclick = () => this.resolveSelectedAI();
    this.root.querySelector("#waa-save-settings").onclick = () => this.saveSettings();
    this.root.querySelector("#waa-rebuild-registry").onclick = () => this.rebuildRegistry();
    this.root.querySelector("#waa-download-selected").onclick = () => this.download(false);
    this.root.querySelector("#waa-select-missing").onclick = () => this.setSelectionBy((entry) => !entry.resolved);
    this.root.querySelector("#waa-select-downloadable").onclick = () => this.setSelectionBy((entry) => entry.resolved && !entry.local_exists);
    this.root.querySelector("#waa-select-downloaded").onclick = () => this.setSelectionBy((entry) => !!entry.local_exists);
    this.root.querySelector("#waa-clear-selection").onclick = () => this.setSelectionBy(() => false);
    this.root.querySelector("#waa-install-custom-node").onclick = () => this.installCustomNode();
    this.root.querySelector("#waa-select-all").onchange = (event) => {
      this.root.querySelectorAll(".waa-row-check").forEach((el) => {
        el.checked = event.target.checked;
      });
      this.syncSelectionControls();
    };
    this.root.querySelector("#waa-workflow-name").oninput = (event) => {
      const typed = normalizeWorkflowName(event.target.value);
      this.state.workflowNamePinned = !!typed && typed !== this.state.detectedWorkflowName;
    };
  }

  async mount() {
    uiLog("Sidebar mounted");
    await this.loadSettings();
    await this.requestScan(false);
    this.startWorkflowWatcher();
  }

  selectedModels() {
    return [...this.root.querySelectorAll(".waa-row-check:checked")].map((el) => el.dataset.model);
  }

  safeId(value) {
    return value.replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  entryFor(modelName) {
    return this.state.entries.find((entry) => entry.model_name === modelName);
  }

  requestScan(forceRegenerate, options = {}) {
    this.state.scanQueue = this.state.scanQueue
      .catch(() => {})
      .then(() => this.scan(forceRegenerate, options));
    return this.state.scanQueue;
  }

  syncSelectionControls() {
    const checkboxes = [...this.root.querySelectorAll(".waa-row-check")];
    const selectAll = this.root.querySelector("#waa-select-all");
    if (!selectAll) return;
    selectAll.checked = !!checkboxes.length && checkboxes.every((el) => el.checked);
  }

  async loadSettings() {
    const data = await fetchJson("/workflow-assets/settings");
    const runtime = await fetchJson("/workflow-assets/runtime-status");
    this.state.settings = data.settings;
    this.state.runtimeStatus = runtime;
    this.state.detectedWorkflowName = guessWorkflowName();
    this.state.workflowName = this.state.detectedWorkflowName;
    this.root.querySelector("#waa-workflow-name").value = this.state.workflowName;
    this.root.querySelector("#waa-mode").value = data.settings.download_mode || "temp_assets";
    this.root.querySelector("#waa-custom-root").value = data.settings.custom_root || "";
    this.root.querySelector("#waa-ai-model").value = data.settings.ai?.model || "";
    this.root.querySelector("#waa-ai-key-env").value = data.settings.ai?.api_key_env || "OPENAI_API_KEY";
    this.root.querySelector("#waa-ai-base-url").value = data.settings.ai?.base_url || "https://api.openai.com/v1";
    this.renderRuntimeStatus();
  }

  async saveSettings() {
    const payload = {
      download_mode: this.root.querySelector("#waa-mode").value,
      custom_root: this.root.querySelector("#waa-custom-root").value.trim(),
      ai: {
        model: this.root.querySelector("#waa-ai-model").value.trim(),
        api_key_env: this.root.querySelector("#waa-ai-key-env").value.trim() || "OPENAI_API_KEY",
        base_url: this.root.querySelector("#waa-ai-base-url").value.trim() || "https://api.openai.com/v1",
      },
      search: {
        provider_mode: "hf_only",
        civitai_enabled: false,
        huggingface_enabled: true,
      },
    };
    const data = await fetchJson("/workflow-assets/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    this.state.settings = data.settings;
    this.state.runtimeStatus = await fetchJson("/workflow-assets/runtime-status");
    this.renderRuntimeStatus();
    this.log("Settings saved.");
  }

  renderRuntimeStatus() {
    const ai = this.state.runtimeStatus?.ai || {};
    const chip = this.root.querySelector("#waa-ai-chip");
    const box = this.root.querySelector("#waa-ai-status-box");
    const providerSummary = this.root.querySelector("#waa-summary-provider");
    const workflowSummary = this.root.querySelector("#waa-summary-workflow");
    const downloadSummary = this.root.querySelector("#waa-summary-download-mode");

    if (workflowSummary) workflowSummary.textContent = this.state.workflowName || this.state.detectedWorkflowName || "-";
    if (providerSummary) {
      providerSummary.textContent = "HF + Notes";
    }
    if (downloadSummary) {
      const mode = this.root.querySelector("#waa-mode")?.value || "temp_assets";
      downloadSummary.textContent = mode === "custom_root" ? "Custom root" : "Temp assets";
    }

    let label = "AI disabled";
    let cssClass = "waa-chip-muted";
    if (ai.active) {
      label = `AI ready: ${ai.model || "model not set"}`;
      cssClass = "waa-chip-on";
    } else if (ai.enabled) {
      const missing = [];
      if (!ai.model_present) missing.push("model");
      if (!ai.api_key_present) missing.push(ai.api_key_env || "API key");
      label = `AI fallback: missing ${missing.join(" + ") || "config"}`;
      cssClass = "waa-chip-warn";
    }

    if (chip) {
      chip.className = `waa-chip ${cssClass}`;
      chip.textContent = label;
    }

    if (box) {
      box.textContent = ai.active
        ? `AI is active. Model: ${ai.model}. Key env: ${ai.api_key_env}. Base URL: ${ai.base_url || "-"}`
        : `AI is not active. Fallback mode will use heuristic search. Missing: ${!ai.model_present ? "model " : ""}${!ai.api_key_present ? `key in ${ai.api_key_env}` : ""}`.trim();
    }
  }

  async rebuildRegistry() {
    const data = await fetchJson("/workflow-assets/rebuild-registry", { method: "POST" });
    this.log(`Registry rebuild complete. Touched ${data.touched} entries.`);
  }

  workflowOverlap(previousEntries, nextEntries) {
    const left = new Set((previousEntries || []).map((entry) => entry.model_name));
    const right = new Set((nextEntries || []).map((entry) => entry.model_name));
    if (!left.size || !right.size) return 0;
    let shared = 0;
    for (const item of left) {
      if (right.has(item)) shared += 1;
    }
    return shared / Math.min(left.size, right.size);
  }

  syncDetectedWorkflowName() {
    const detected = guessWorkflowName();
    if (!detected) return false;
    const input = this.root.querySelector("#waa-workflow-name");
    const currentValue = normalizeWorkflowName(input.value);
    const previousDetected = this.state.detectedWorkflowName;
    this.state.detectedWorkflowName = detected;
    if (!this.state.workflowNamePinned || !currentValue || currentValue === previousDetected || currentValue === this.state.workflowName) {
      input.value = detected;
      this.state.workflowName = detected;
      this.state.workflowNamePinned = false;
    }
    return detected !== previousDetected;
  }

  async maybeMigrateManifest(previousWorkflowName, previousManifestPath, previousEntries, nextEntries, usedExistingManifest) {
    if (!previousWorkflowName || !previousManifestPath || usedExistingManifest) return false;
    if (previousWorkflowName === this.state.workflowName) return false;
    if (!workflowNameLooksUsable(previousWorkflowName) || !workflowNameLooksUsable(this.state.workflowName)) return false;
    const overlap = this.workflowOverlap(previousEntries, nextEntries);
    if (overlap < 0.7) return false;
    const nameSimilarity = workflowNameSimilarity(previousWorkflowName, this.state.workflowName);
    if (nameSimilarity < 0.45) return false;
    const shouldMove = window.confirm(
      `This looks like the same workflow under a new name.\n\nMove manifest data from:\n${previousWorkflowName}\n\nto:\n${this.state.workflowName}\n\nThis will preserve previously resolved sources for the renamed workflow.`
    );
    if (!shouldMove) return false;

    const result = await fetchJson("/workflow-assets/migrate-manifest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        old_workflow_name: previousWorkflowName,
        new_workflow_name: this.state.workflowName,
        remove_old: true,
      }),
    });
    this.log(`Migrated manifest to renamed workflow: ${result.new_manifest_path}`);
    return true;
  }

  async scan(forceRegenerate, options = {}) {
    this.syncDetectedWorkflowName();
    const input = this.root.querySelector("#waa-workflow-name");
    const typedValue = normalizeWorkflowName(input.value);
    const guessedValue = this.state.detectedWorkflowName || guessWorkflowName();
    const previousWorkflowName = this.state.workflowName;
    const previousManifestPath = this.state.manifestPath;
    const previousEntries = [...this.state.entries];
    this.state.workflowName =
      !typedValue || typedValue === "current-workflow.json" || typedValue === previousWorkflowName ? guessedValue : typedValue;
    this.state.workflowNamePinned =
      !!typedValue && typedValue !== guessedValue && typedValue !== previousWorkflowName;
    input.value = this.state.workflowName;
    const payload = {
      workflow_name: this.state.workflowName,
      workflow_data: currentWorkflowData(),
      prefer_existing: !forceRegenerate,
      force_regenerate: forceRegenerate,
    };
    const data = await fetchJson("/workflow-assets/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    this.state.entries = data.entries || [];
    this.state.manifestPath = data.manifest_path || "";
    this.state.lastAutoScannedWorkflowName = this.state.workflowName;
    this.render();
    this.renderRuntimeStatus();
    if (!options.quiet) {
      const reason = options.reason ? ` (${options.reason})` : "";
      this.log(`${data.used_existing_manifest ? "Loaded existing manifest" : "Generated manifest"}${reason}: ${this.state.manifestPath}`);
    }
    if (!options.skipMigrationCheck) {
      const migrated = await this.maybeMigrateManifest(
        previousWorkflowName,
        previousManifestPath,
        previousEntries,
        this.state.entries,
        data.used_existing_manifest
      );
      if (migrated) {
        await this.scan(false, { skipMigrationCheck: true, reason: "manifest-migrated" });
      }
    }
  }

  render() {
    const previousSelection = new Set(this.selectedModels());
    this.root.querySelector("#waa-meta").textContent =
      `Workflow: ${this.state.workflowName} | Manifest: ${this.state.manifestPath || "not generated yet"}`;
    const rows = this.root.querySelector("#waa-rows");
    rows.innerHTML = "";

    const groups = [
      ["missing", "Missing sources", this.state.entries.filter((entry) => !entry.resolved), true],
      ["resolved", "Ready to download", this.state.entries.filter((entry) => entry.resolved && !entry.local_exists), true],
      ["downloaded", "Already on disk", this.state.entries.filter((entry) => entry.local_exists), false],
    ];

    for (const [groupKey, title, items, openByDefault] of groups) {
      if (!items.length) continue;
      const section = document.createElement("details");
      section.className = "waa-group";
      section.open = openByDefault;
      section.innerHTML = `
        <summary class="waa-group-summary">
          <span>${title}</span>
          <span class="waa-group-count">${items.length}</span>
        </summary>
        <div class="waa-group-body"></div>
      `;
      const body = section.querySelector(".waa-group-body");
      for (const entry of items) {
        body.appendChild(this.renderEntryCard(entry));
      }
      rows.appendChild(section);
    }

    if (!rows.children.length) {
      rows.innerHTML = `<div class="waa-queue-box">No models detected in this workflow yet.</div>`;
    }

    rows.querySelectorAll("button[data-action='resolve']").forEach((el) => {
      el.onclick = () => this.resolveSingle(el.dataset.model);
    });
    rows.querySelectorAll("button[data-action='apply']").forEach((el) => {
      el.onclick = () => this.applyManualSource(el.dataset.model);
    });
    rows.querySelectorAll("button[data-action='download']").forEach((el) => {
      el.onclick = () => this.downloadSingle(el.dataset.model);
    });
    rows.querySelectorAll(".waa-row-check").forEach((el) => {
      const entry = this.entryFor(el.dataset.model);
      el.checked = previousSelection.size
        ? previousSelection.has(el.dataset.model)
        : !!entry && entry.resolved && !entry.local_exists;
      el.onchange = () => this.syncSelectionControls();
    });
    this.syncSelectionControls();
  }

  renderEntryCard(entry) {
    const sourceLabel = entry.source || "";
    const safeId = this.safeId(entry.model_name);
    const statusText = entry.local_exists ? "downloaded" : entry.resolved ? "resolved" : "missing source";
    const statusClass = entry.local_exists ? "disk" : entry.resolved ? "ok" : "todo";
    const item = document.createElement("div");
    item.className = "waa-card";
    item.innerHTML = `
      <div class="waa-card-top">
        <label class="waa-card-check">
          <input class="waa-row-check" data-model="${entry.model_name}" type="checkbox">
          <span>${entry.model_name}</span>
        </label>
        <span class="waa-status ${statusClass}">${statusText}</span>
      </div>
      <div class="waa-card-grid">
        <div>
          <div class="waa-field-label">Category</div>
          <div class="waa-path">${entry.category || ""}</div>
        </div>
        <div>
          <div class="waa-field-label">Path</div>
          <div class="waa-path">${entry.effective_target || entry.target || ""}</div>
        </div>
      </div>
      <div class="waa-field-label">Source</div>
      <div class="waa-path">${sourceLabel || "-"}</div>
      ${entry.note_urls?.length ? `
      <details class="waa-inline-details">
        <summary>Related links (${entry.note_urls.length})</summary>
        <div class="waa-path">${entry.note_urls.join("\n")}</div>
      </details>
      ` : ""}
      ${entry.note_model_hints?.length ? `
      <details class="waa-inline-details">
        <summary>Related model hints (${entry.note_model_hints.length})</summary>
        <div class="waa-path">${entry.note_model_hints.join("\n")}</div>
      </details>
      ` : ""}
      <input id="waa-source-${safeId}" value="${sourceLabel.replaceAll('"', "&quot;")}" placeholder="Paste exact model URL here" />
      <div class="waa-row-actions">
        <button class="waa-mini-btn" data-action="resolve" data-model="${entry.model_name}" title="Search internet sources for this model.">Find Source</button>
        <button class="waa-mini-btn" data-action="apply" data-model="${entry.model_name}" title="Use the URL written in the input field above as the model source.">Apply URL</button>
        ${entry.resolved && !entry.local_exists ? `<button class="waa-mini-btn" data-action="download" data-model="${entry.model_name}" title="Download this resolved model immediately.">Download</button>` : ""}
      </div>
    `;
    return item;
  }

  async applyManualSource(modelName) {
    const entry = this.entryFor(modelName);
    if (!entry) return;
    const sourceInput = this.root.querySelector(`#waa-source-${this.safeId(modelName)}`);
    const source = sourceInput?.value?.trim() || "";
    if (!source) {
      this.log(`Manual URL missing for ${modelName}`);
      return;
    }
    await fetchJson("/workflow-assets/apply-resolution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workflow_name: this.state.workflowName,
        model_name: entry.model_name,
        category: entry.category,
        kind: "FILE",
        source,
        target: entry.target,
        note: "Applied manually from Workflow Asset Agent sidebar",
      }),
    });
    this.log(`Applied manual URL for ${modelName}`);
    await this.requestScan(false, { reason: `manual-apply:${modelName}` });
  }

  async resolveSingle(modelName, options = {}) {
    const entry = this.entryFor(modelName);
    if (!entry) return;
    const resolveMode = options.resolveMode || "find";
    const autoApply = options.autoApply === true;
    const interactiveCandidates = options.interactiveCandidates !== false;
    const refreshAfterApply = options.refreshAfterApply !== false;
    this.log(
      resolveMode === "ai_deep"
        ? `AI deep search for ${modelName}...`
        : `Searching internet candidates for ${modelName}...`
    );
    const data = await fetchJson("/workflow-assets/resolve-model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model_name: modelName,
        category: entry.category,
        note_urls: entry.note_urls || [],
        note_model_hints: entry.note_model_hints || [],
        provider_mode: "hf_only",
        resolve_mode: resolveMode,
      }),
    });
    const candidates = data.candidates || [];
    const decision = data.decision || {};
    const summary = data.summary || {};
    const errors = data.errors || [];
    this.log(
      `Found ${candidates.length} candidate(s) for ${modelName}. resolve_mode=${data.resolve_mode || resolveMode}, provider_mode=hf_only, notes=${summary.notes || 0}, hf=${summary.huggingface || 0}, web=${summary.web || 0}. Decision: ${decision.decision || "none"} ${decision.provider ? `via ${decision.provider}` : ""} ${decision.mode ? `[${decision.mode}]` : ""}`
    );
    if (errors.length) {
      this.log(`Search errors for ${modelName}: ${errors.join(" | ")}`);
    }
    if (decision.usage?.total_tokens) {
      this.log(
        `AI token usage for ${modelName}: prompt=${decision.usage.prompt_tokens || "?"}, completion=${decision.usage.completion_tokens || "?"}, total=${decision.usage.total_tokens}, ranked=${decision.usage.candidates_considered || "?"}/${decision.usage.candidates_found || candidates.length}`
      );
    }

    if (decision.decision === "resolved" && decision.source) {
      const useAuto = autoApply
        ? true
        : window.confirm(
            `${decision.mode === "ai" ? "AI" : "Heuristic"} found a source for ${modelName}:\n\n${decision.source}\n\nReason: ${decision.reason || "-"}\n\nApply it now?`
          );
      if (useAuto) {
        await fetchJson("/workflow-assets/apply-resolution", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workflow_name: this.state.workflowName,
            model_name: entry.model_name,
            category: entry.category,
            kind: "FILE",
            source: decision.source,
            target: decision.target || entry.target,
            note: decision.reason || "",
          }),
        });
        this.log(`Applied ${decision.mode || "unknown"} resolved source for ${modelName}: ${decision.source}`);
        if (refreshAfterApply) {
          await this.requestScan(false, { reason: `resolved:${modelName}` });
        }
        return { ok: true, autoApplied: true, decision, candidates };
      }
    }

    if (!candidates.length) {
      this.log(`No candidates found for ${modelName}`);
      return { ok: false, autoApplied: false, decision, candidates };
    }

    if (!interactiveCandidates) {
      this.log(`Skipping manual candidate prompt for ${modelName} during batch resolve.`);
      return { ok: false, autoApplied: false, decision, candidates };
    }

    const promptLines = candidates.map((candidate, index) =>
      `${index + 1}. [${candidate.provider}] ${candidate.label} -> ${candidate.candidate_url}`
    );
    const choice = window.prompt(
      `Candidates for ${modelName}:\n\n${promptLines.join("\n")}\n\nEnter candidate number, or paste exact URL manually:`,
      ""
    );
    if (!choice) {
      this.log(`Candidate selection cancelled for ${modelName}`);
      return { ok: false, autoApplied: false, decision, candidates };
    }

    let source = choice.trim();
    const asNumber = Number(source);
    if (!Number.isNaN(asNumber) && asNumber >= 1 && asNumber <= candidates.length) {
      source = candidates[asNumber - 1].candidate_url;
    }

    await fetchJson("/workflow-assets/apply-resolution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workflow_name: this.state.workflowName,
        model_name: entry.model_name,
        category: entry.category,
        kind: "FILE",
        source,
        target: entry.target,
        note: "Selected manually from Workflow Asset Agent candidates",
      }),
    });
    this.log(`Applied selected source for ${modelName}: ${source}`);
    if (refreshAfterApply) {
      await this.requestScan(false, { reason: `candidate-picked:${modelName}` });
    }
    return { ok: true, autoApplied: false, decision, candidates };
  }

  setSelectionBy(predicate) {
    const selectAll = this.root.querySelector("#waa-select-all");
    this.root.querySelectorAll(".waa-row-check").forEach((el) => {
      const entry = this.entryFor(el.dataset.model);
      el.checked = !!entry && predicate(entry);
    });
    if (selectAll) selectAll.checked = false;
    this.syncSelectionControls();
  }

  async resolvePool(entries, options = {}) {
    const pool = entries || [];
    const resolveMode = options.resolveMode || "find";
    const label = options.label || "Resolving";
    if (!pool.length) {
      this.log(`${label}: nothing to do.`);
      return;
    }
    this.log(`${label}: ${pool.length} model(s). Mode=${resolveMode}. Order=sequential.`);
    let applied = 0;
    let skipped = 0;
    let failed = 0;
    for (let index = 0; index < pool.length; index += 1) {
      const entry = pool[index];
      this.log(`[${index + 1}/${pool.length}] ${resolveMode === "ai_deep" ? "AI" : "Find"} ${entry.model_name}...`);
      try {
        const result = await this.resolveSingle(entry.model_name, {
          resolveMode,
          autoApply: true,
          interactiveCandidates: false,
          refreshAfterApply: false,
        });
        if (result?.ok) applied += 1;
        else skipped += 1;
      } catch (error) {
        failed += 1;
        this.log(`Resolve failed for ${entry.model_name}: ${error.message || error}`);
      }
    }
    this.log(`${label} complete. Applied=${applied}, skipped=${skipped}, failed=${failed}.`);
    await this.requestScan(false, { reason: `${resolveMode}-batch` });
  }

  async findSelected() {
    const selected = this.selectedModels();
    const pool = this.state.entries.filter((entry) => selected.includes(entry.model_name));
    await this.resolvePool(pool, { resolveMode: "find", label: "Find selected models" });
  }

  async findMissing() {
    const pool = this.state.entries.filter((entry) => !entry.resolved);
    await this.resolvePool(pool, { resolveMode: "find", label: "Find all unresolved models" });
  }

  async resolveSelectedAI() {
    const selected = this.selectedModels();
    const pool = this.state.entries.filter((entry) => selected.includes(entry.model_name));
    await this.resolvePool(pool, { resolveMode: "ai_deep", label: "AI resolve selected models" });
  }

  async resolveMissing() {
    const pool = this.state.entries.filter((entry) => !entry.resolved);
    await this.resolvePool(pool, { resolveMode: "ai_deep", label: "AI resolve all unresolved models" });
  }

  downloadableEntries() {
    return this.state.entries.filter((entry) => entry.resolved && !entry.local_exists);
  }

  selectedDownloadableModels() {
    const selected = new Set(this.selectedModels());
    return this.downloadableEntries()
      .filter((entry) => selected.has(entry.model_name))
      .map((entry) => entry.model_name);
  }

  async runPreflight(all) {
    const selected = all
      ? this.downloadableEntries().map((entry) => entry.model_name)
      : this.selectedDownloadableModels();
    if (!selected.length) {
      const message = "No downloadable models are selected.";
      this.root.querySelector("#waa-preflight").textContent = message;
      this.log(message);
      return { ok: false, message };
    }
    const payload = {
      workflow_name: this.state.workflowName,
      entries: this.state.entries,
      selected_models: selected,
      download_settings: {
        download_mode: this.root.querySelector("#waa-mode").value,
        custom_root: this.root.querySelector("#waa-custom-root").value.trim(),
      },
    };
    const data = await fetchJson("/workflow-assets/preflight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const preflight = data.preflight || {};
    this.root.querySelector("#waa-preflight").textContent = preflight.message || "No disk preflight yet.";
    this.log(preflight.message || "No disk preflight info.");
    return preflight;
  }

  async download(all) {
    const preflight = await this.runPreflight(all);
    if (!preflight.ok) {
      this.log(`Download blocked: ${preflight.message}`);
      window.alert(preflight.message);
      return;
    }
    const selected = all
      ? this.downloadableEntries().map((entry) => entry.model_name)
      : this.selectedDownloadableModels();
    const payload = {
      workflow_name: this.state.workflowName,
      entries: this.state.entries,
      selected_models: selected,
      download_settings: {
        download_mode: this.root.querySelector("#waa-mode").value,
        custom_root: this.root.querySelector("#waa-custom-root").value.trim(),
      },
    };
    const data = await fetchJson("/workflow-assets/queue-download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const jobId = data.job?.job_id || "";
    this.state.queueStatus = data.job || null;
    this.renderDownloadStatus(this.state.queueStatus);
    this.log(`Download queued. Job=${jobId || "-"} Selected=${selected.length}. ${data.preflight?.message || ""}`.trim());
    if (jobId) {
      this.startQueuePolling(jobId, all ? "download-all-complete" : "download-selected-complete");
    }
  }

  async downloadSingle(modelName) {
    const entry = this.entryFor(modelName);
    if (!entry || !entry.resolved || entry.local_exists) return;
    const payload = {
      workflow_name: this.state.workflowName,
      entries: this.state.entries,
      selected_models: [modelName],
      download_settings: {
        download_mode: this.root.querySelector("#waa-mode").value,
        custom_root: this.root.querySelector("#waa-custom-root").value.trim(),
      },
    };
    const data = await fetchJson("/workflow-assets/queue-download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const jobId = data.job?.job_id || "";
    this.state.queueStatus = data.job || null;
    this.renderDownloadStatus(this.state.queueStatus);
    this.log(`Download queued for ${modelName}. Job=${jobId || "-"}`);
    if (jobId) {
      this.startQueuePolling(jobId, `downloaded:${modelName}`);
    }
  }

  startWorkflowWatcher() {
    if (this.state.workflowWatchTimer) clearInterval(this.state.workflowWatchTimer);
    this.state.workflowWatchTimer = setInterval(() => {
      const changed = this.syncDetectedWorkflowName();
      if (!changed || this.state.workflowNamePinned) return;
      if (!this.state.detectedWorkflowName || this.state.detectedWorkflowName === this.state.lastAutoScannedWorkflowName) return;
      this.log(`Workflow changed to ${this.state.detectedWorkflowName}. Refreshing manifest view...`);
      this.requestScan(false, { reason: "workflow-switch", quiet: true }).catch((error) => {
        this.log(`Workflow refresh failed: ${error.message || error}`);
      });
    }, 1500);
  }

  async installCustomNode() {
    const input = this.root.querySelector("#waa-custom-node-url");
    const status = this.root.querySelector("#waa-custom-node-status");
    const repoUrl = String(input?.value || "").trim();
    if (!repoUrl) {
      this.log("Custom node GitHub URL is empty.");
      return;
    }
    status.textContent = `Installing custom node from ${repoUrl}...`;
    try {
      const data = await fetchJson("/workflow-assets/install-custom-node", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_url: repoUrl,
          update_if_exists: true,
          install_dependencies: true,
        }),
      });
      const actionLines = [...(data.actions || []), ...(data.dependency_actions || [])].map((item) =>
        `${item.ok ? "OK" : "FAIL"} | ${(item.cmd || []).join(" ")}${item.stdout ? `\n${item.stdout}` : ""}${item.stderr ? `\n${item.stderr}` : ""}`
      );
      const warningLines = (data.warnings || []).map((item) => `WARN | ${item}`);
      status.textContent = `${data.ok ? "Installed" : "Install failed"}: ${data.destination || repoUrl}`;
      this.log(
        [
          `Custom node ${data.status || "processed"}: ${data.repo_name || repoUrl}`,
          `Destination: ${data.destination || "-"}`,
          ...warningLines,
          ...actionLines,
        ].join("\n")
      );
    } catch (error) {
      status.textContent = `Install failed: ${error.message || error}`;
      this.log(`Custom node install failed for ${repoUrl}: ${error.message || error}`);
    }
  }

  log(message) {
    const el = this.root.querySelector("#waa-log");
    const now = new Date().toLocaleTimeString();
    el.textContent = `[${now}] ${message}\n\n${el.textContent}`;
  }

  formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "-";
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let current = bytes / 1024;
    let unit = units[0];
    for (let index = 0; index < units.length; index += 1) {
      unit = units[index];
      if (current < 1024 || index === units.length - 1) break;
      current /= 1024;
    }
    return `${current.toFixed(current >= 100 ? 0 : current >= 10 ? 1 : 2)} ${unit}`;
  }

  formatSpeed(value) {
    const speed = Number(value);
    if (!Number.isFinite(speed) || speed <= 0) return "-";
    return `${this.formatBytes(speed)}/s`;
  }

  formatEta(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) return "-";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);
    if (minutes < 60) return `${minutes}m ${rest}s`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }

  renderDownloadStatus(status = null) {
    const box = this.root.querySelector("#waa-download-status");
    if (!box) return;
    const job = status || this.state.queueStatus;
    if (!job || !job.job_id || job.status === "unknown") {
      box.textContent = "No active download.";
      return;
    }

    const totalItems = Number(job.total_items || 0);
    const completedItems = Number(job.completed_items || 0);
    const currentItem = job.current_item || job.last_completed_item || "-";
    const downloadedBytes = Number(job.current_downloaded_bytes || 0);
    const totalBytes = job.current_total_bytes == null ? null : Number(job.current_total_bytes);
    const progressText = totalBytes && totalBytes > 0
      ? `${this.formatBytes(downloadedBytes)} / ${this.formatBytes(totalBytes)}`
      : this.formatBytes(downloadedBytes);
    const speedText = this.formatSpeed(job.current_speed_bps);
    const etaText = this.formatEta(job.current_eta_seconds);
    const itemCounter = totalItems ? `${completedItems}/${totalItems} files` : `${completedItems} file(s)`;
    const stage = job.status === "queued"
      ? "Queued"
      : job.status === "running"
        ? (job.current_stage === "snapshot" ? "Downloading repo snapshot" : "Downloading")
        : job.status === "completed"
          ? "Completed"
          : "Failed";

    box.innerHTML = `
      <div class="waa-download-status-line">
        <strong>${stage}</strong>
        <span class="waa-download-status-meta">${itemCounter}</span>
      </div>
      <div class="waa-download-status-path">${currentItem}</div>
      <div class="waa-download-status-line">
        <span>${progressText}</span>
        <span class="waa-download-status-meta">Speed: ${speedText} | ETA: ${etaText}</span>
      </div>
    `;
  }

  stopQueuePolling() {
    if (this.state.queuePollTimer) {
      clearInterval(this.state.queuePollTimer);
      this.state.queuePollTimer = null;
    }
  }

  async pollQueueStatus() {
    if (!this.state.activeDownloadJobId) return;
    try {
      const data = await fetchJson(`/workflow-assets/queue-status?job_id=${encodeURIComponent(this.state.activeDownloadJobId)}`);
      this.state.queueStatus = data;
      this.renderDownloadStatus(data);
      if (data.status === "completed" || data.status === "failed") {
        this.stopQueuePolling();
        const summary = data.status === "completed"
          ? "Download job finished successfully."
          : "Download job finished with errors.";
        this.log(
          [
            summary,
            data.stdout || "",
            data.stderr || "",
          ].filter(Boolean).join("\n")
        );
        const reason = this.state.activeDownloadReason || "download-complete";
        this.state.activeDownloadJobId = "";
        this.state.activeDownloadReason = "";
        await this.requestScan(false, { reason, quiet: true });
      }
    } catch (error) {
      this.log(`Queue status refresh failed: ${error.message || error}`);
      this.stopQueuePolling();
    }
  }

  startQueuePolling(jobId, reason = "download-complete") {
    this.state.activeDownloadJobId = jobId;
    this.state.activeDownloadReason = reason;
    this.stopQueuePolling();
    this.pollQueueStatus();
    this.state.queuePollTimer = setInterval(() => {
      this.pollQueueStatus();
    }, 1000);
  }
}

app.registerExtension({
  name: "WorkflowAssetAgent.Sidebar",
  async setup() {
    ensureCss();
    uiLog("Setup started");
    if (!app?.extensionManager?.registerSidebarTab) {
      console.error("[WorkflowAssetAgent.UI] registerSidebarTab API is not available", app?.extensionManager);
      return;
    }
    uiLog("Registering official sidebar tab");
    app.extensionManager.registerSidebarTab({
      id: "workflow-asset-agent",
      icon: "pi pi-download",
      title: "Workflow Agent",
      tooltip: "Workflow model manifests, resolution, and downloads",
      type: "custom",
      render: async (el) => {
        uiLog("Rendering sidebar tab");
        const sidebar = new WorkflowAssetAgentSidebar(el);
        await sidebar.mount();
        el.__workflowAssetAgentSidebar = sidebar;
      },
    });
  },
});
}
