let selectedProject = null;
let refreshTimer = null;
let improvedDraft = "";
let improvedDraftProjectId = null;
let improvedDraftFilename = null;
let improvedDirty = false;
let improvedLoading = false;
let currentDetailSig = null;
let actionMessage = { text: "", kind: "" };

const providerStatus = document.querySelector("#provider-status");
const projectsEl = document.querySelector("#projects");
const detailEl = document.querySelector("#detail");
const form = document.querySelector("#project-form");
const formMessage = document.querySelector("#form-message");
const bemaImportButton = document.querySelector("#import-bema");
const stageSelectAllButton = document.querySelector("#stages-select-all");
const stageDeselectAllButton = document.querySelector("#stages-deselect-all");
const stageCheckboxes = Array.from(document.querySelectorAll('input[name="stage"]'));

const PROVIDER_LABELS = {
  openai: "OpenAI",
  elevenlabs: "ElevenLabs",
  deepl: "DeepL",
  deepseek: "DeepSeek",
  azure: "Azure",
  anthropic: "Anthropic",
  google: "Google",
};

const PIPELINE_ORDER = ["transcribe", "translate", "customize", "improve", "voiceover"];

function cleanError(text, fallback) {
  const stripped = String(text || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!stripped) return fallback;
  return stripped.length > 240 ? `${stripped.slice(0, 240)}\u2026` : stripped;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(cleanError(text, response.statusText));
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

function statusClass(state) {
  if (state === "completed") return "status completed";
  if (state === "failed") return "status failed";
  return "status";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
}

function setFormMessage(text, kind = "") {
  formMessage.textContent = text || "";
  formMessage.className = `message${kind ? ` ${kind}` : ""}`;
}

function setActionMessage(text, kind = "") {
  actionMessage = { text: text || "", kind };
  const node = detailEl.querySelector("#d-action-msg");
  if (node) {
    node.textContent = actionMessage.text;
    node.className = `message${actionMessage.kind ? ` ${actionMessage.kind}` : ""}`;
  }
}

function humanizeStage(name) {
  if (!name) return "Stage";
  return String(name)
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function selectedStagesValue() {
  const selected = stageCheckboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
  return selected.join("+");
}

function setAllStages(checked) {
  stageCheckboxes.forEach((checkbox) => {
    checkbox.checked = checked;
  });
}

function renderProviders(providers) {
  const entries = Object.entries(providers || {});
  if (!entries.length) {
    providerStatus.innerHTML = `<span class="muted">No providers reported.</span>`;
    return;
  }
  providerStatus.innerHTML = entries
    .map(([name, ok]) => {
      const label = PROVIDER_LABELS[name.toLowerCase()] || humanizeStage(name);
      return `<span class="provider-chip ${ok ? "ok" : "missing"}">${escapeHtml(label)} ${ok ? "connected" : "not set"}</span>`;
    })
    .join("");
}

function renderProjects(projects) {
  if (!projects.length) {
    projectsEl.innerHTML = `
      <div class="empty-state">
        <span class="empty-illo">[ new ]</span>
        <p class="empty">No projects yet. Create one above to get started.</p>
      </div>`;
    return;
  }

  projectsEl.innerHTML = "";
  for (const project of projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project ${selectedProject === project.id ? "active" : ""}`;
    const metadata = project.metadata || {};
    const status = project.status || {};
    const progress = Number(status.progress || 0);
    button.innerHTML = `
      <strong>${escapeHtml(metadata.source_filename || project.id)}</strong>
      <div class="project-meta">
        <span class="${statusClass(status.state)}">${escapeHtml(status.state || "unknown")}</span>
        <span>${escapeHtml(metadata.target_language || "")}</span>
        ${status.stage ? `<span>&middot; ${escapeHtml(humanizeStage(status.stage))}</span>` : ""}
      </div>
      <div class="progress"><div style="width:${progress}%"></div></div>
    `;
    button.addEventListener("click", () => {
      if (selectedProject !== project.id) {
        selectedProject = project.id;
        currentDetailSig = null;
        actionMessage = { text: "", kind: "" };
      }
      renderDetail(project);
      loadProjects();
    });
    projectsEl.appendChild(button);
  }
}

function logsAtBottom(pre) {
  if (!pre) return true;
  return pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
}

function pipelineSteps(stages, status) {
  const list = stages.length ? stages.map((item) => item.name) : PIPELINE_ORDER;
  const overall = status.state;
  return list
    .map((name) => {
      const record = stages.find((item) => item.name === name) || {};
      let state = "upcoming";
      if (overall === "completed" || record.status === "completed") {
        state = "completed";
      }
      if (record.status === "failed" || (overall === "failed" && name === status.stage)) {
        state = "failed";
      }
      if (state === "upcoming" && name === status.stage && overall !== "completed") {
        state = "active";
      }
      const aria = state === "active" ? ' aria-current="step"' : "";
      return `
        <div class="pipeline-step ${state}"${aria} title="${escapeHtml(humanizeStage(name))}: ${escapeHtml(record.status || state)}">
          <span class="pipeline-dot">${state === "completed" ? "&#10003;" : state === "failed" ? "&#33;" : ""}</span>
          <span class="pipeline-label">${escapeHtml(humanizeStage(name))}</span>
        </div>`;
    })
    .join("");
}

function detailSignature(project, hasImproved, improvedFilename, stages, artifacts) {
  return [
    project.id,
    hasImproved ? "1" : "0",
    improvedFilename || "",
    stages.map((item) => item.name).join(","),
    artifacts.map((item) => item.name).join(","),
  ].join("|");
}

async function renderDetail(project) {
  const status = project.status || {};
  const metadata = project.metadata || {};
  const manifest = project.manifest || {};
  const stages = Array.isArray(manifest.stages) ? manifest.stages : [];
  const artifacts = await api(`/api/projects/${project.id}/artifacts`);
  const logs = await api(`/api/projects/${project.id}/logs`);
  const improvedArtifact = artifacts.find((item) => item.name.endsWith(".improved.json"));
  const improvedFilename = improvedArtifact?.name || null;
  const hasImproved = Boolean(improvedArtifact);
  const sig = detailSignature(project, hasImproved, improvedFilename, stages, artifacts);
  const needsFullBuild = sig !== currentDetailSig;

  if (needsFullBuild) {
    if (improvedDirty && improvedDraftProjectId === project.id) {
      // keep current draft
    } else if (hasImproved) {
      improvedLoading = true;
      try {
        improvedDraft = await api(`/api/projects/${project.id}/files/${improvedFilename}`);
        improvedDraftProjectId = project.id;
        improvedDraftFilename = improvedFilename;
        improvedDirty = false;
      } catch {
        improvedDraft = "";
        improvedDraftProjectId = null;
        improvedDraftFilename = null;
        improvedDirty = false;
      } finally {
        improvedLoading = false;
      }
    } else {
      improvedDraft = "";
      improvedDraftProjectId = null;
      improvedDraftFilename = null;
      improvedDirty = false;
    }
  }

  const progress = Number(status.progress || 0);

  if (needsFullBuild) {
    detailEl.className = "detail";
    detailEl.innerHTML = `
      <div class="detail-head">
        <span class="detail-title">${escapeHtml(metadata.source_filename || project.id)}</span>
        <span class="detail-id">${escapeHtml(project.id)}</span>
      </div>
      <div class="pipeline" id="d-pipeline" role="list" aria-label="Pipeline progress">
        ${pipelineSteps(stages, status)}
      </div>
      <p>
        <span class="${statusClass(status.state)}" id="d-status">${escapeHtml(status.state || "unknown")}</span>
        <span class="muted" id="d-meta"> &middot; ${escapeHtml(humanizeStage(status.stage) || "unknown")} &middot; ${progress}%</span>
      </p>
      <p class="muted" id="d-statusmsg">${escapeHtml(status.message || "")}</p>
      <div class="progress"><div id="d-progress" style="width:${progress}%"></div></div>
      <p class="message ${actionMessage.kind}" id="d-action-msg">${escapeHtml(actionMessage.text)}</p>
      <div class="stage-summary">
        <h3>Stage history</h3>
        <ul class="stage-list" id="d-stages">
          ${stages
            .map(
              (item) => `
                <li class="${item.status || ""}">
                  <span>${escapeHtml(humanizeStage(item.name) || "Stage")}</span>
                  <span class="muted">${escapeHtml(item.status || "unknown")}</span>
                </li>`,
            )
            .join("")}
        </ul>
      </div>
      ${hasImproved ? `
        <div class="editor">
          <h3>Improved transcript</h3>
          <textarea id="improved-editor" spellcheck="false" ${improvedLoading ? "disabled" : ""}>${escapeHtml(improvedDraft)}</textarea>
          <div class="editor-actions">
            <button type="button" id="save-improved" class="secondary" ${improvedLoading ? "disabled" : ""}>Save</button>
            <a class="artifact" href="/api/projects/${project.id}/download/${encodeURIComponent(improvedFilename)}" download>Download</a>
            <a class="artifact" href="/improved.html?project=${encodeURIComponent(project.id)}&file=${encodeURIComponent(improvedFilename)}">Open editor</a>
            <button type="button" id="start-voiceover" ${improvedLoading ? "disabled" : ""}>Start voiceover</button>
          </div>
          <p class="muted">Edit the improved JSON, save it, or queue voiceover from the saved version.</p>
        </div>
      ` : ""}
      <div class="logs-head">
        <h3>Logs</h3>
      </div>
      <pre id="d-logs">${escapeHtml(logs)}</pre>
      <div class="artifacts">
        ${artifacts.map((item) => `<a class="artifact" href="${item.download_url}">${escapeHtml(item.name)}</a>`).join("")}
        <a class="artifact" href="/api/projects/${project.id}/support-bundle">Support bundle</a>
      </div>
    `;

    const editor = detailEl.querySelector("#improved-editor");
    if (editor) {
      editor.addEventListener("input", () => {
        improvedDraft = editor.value;
        improvedDraftProjectId = project.id;
        improvedDirty = true;
      });
    }
    const saveButton = detailEl.querySelector("#save-improved");
    if (saveButton) {
      saveButton.addEventListener("click", () => {
        void saveImproved(project.id);
      });
    }
    const voiceoverButton = detailEl.querySelector("#start-voiceover");
    if (voiceoverButton) {
      voiceoverButton.addEventListener("click", () => {
        void startVoiceoverFromImproved(project.id);
      });
    }

    currentDetailSig = sig;
    const pre = detailEl.querySelector("#d-logs");
    if (pre) pre.scrollTop = pre.scrollHeight;
    return;
  }

  // Patch dynamic parts only (no flicker, preserves editor focus/scroll).
  const statusNode = detailEl.querySelector("#d-status");
  if (statusNode) {
    statusNode.className = statusClass(status.state);
    statusNode.textContent = status.state || "unknown";
  }
  const metaNode = detailEl.querySelector("#d-meta");
  if (metaNode) metaNode.innerHTML = ` &middot; ${escapeHtml(humanizeStage(status.stage) || "unknown")} &middot; ${progress}%`;
  const statusMsg = detailEl.querySelector("#d-statusmsg");
  if (statusMsg) statusMsg.textContent = status.message || "";
  const progressNode = detailEl.querySelector("#d-progress");
  if (progressNode) progressNode.style.width = `${progress}%`;
  const pipelineNode = detailEl.querySelector("#d-pipeline");
  if (pipelineNode) pipelineNode.innerHTML = pipelineSteps(stages, status);
  const stagesNode = detailEl.querySelector("#d-stages");
  if (stagesNode) {
    stagesNode.innerHTML = stages
      .map(
        (item) => `
          <li class="${item.status || ""}">
            <span>${escapeHtml(humanizeStage(item.name) || "Stage")}</span>
            <span class="muted">${escapeHtml(item.status || "unknown")}</span>
          </li>`,
      )
      .join("");
  }
  const pre = detailEl.querySelector("#d-logs");
  if (pre) {
    const wasAtBottom = logsAtBottom(pre);
    if (pre.textContent !== logs) {
      pre.textContent = logs;
      if (wasAtBottom) pre.scrollTop = pre.scrollHeight;
    }
  }
}

async function saveImproved(projectId) {
  const editor = detailEl.querySelector("#improved-editor");
  if (!editor) return;
  const filename = improvedDraftProjectId === projectId ? improvedDraftFilename : null;
  if (!filename) {
    setActionMessage("Improved file is missing.", "error");
    return;
  }
  const saveButton = detailEl.querySelector("#save-improved");
  const previousLabel = saveButton?.textContent || "Save";
  if (saveButton) {
    saveButton.disabled = true;
    saveButton.textContent = "Saving...";
  }
  try {
    await api(`/api/projects/${projectId}/files/${filename}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: editor.value }),
    });
    improvedDraft = editor.value;
    improvedDraftProjectId = projectId;
    improvedDraftFilename = filename;
    improvedDirty = false;
    setActionMessage("Saved.", "ok");
    await loadProjects();
  } catch (error) {
    setActionMessage(error.message, "error");
  } finally {
    if (saveButton) {
      saveButton.disabled = false;
      saveButton.textContent = previousLabel;
    }
  }
}

async function startVoiceoverFromImproved(projectId) {
  try {
    await api(`/api/projects/${projectId}/voiceover`, { method: "POST" });
    selectedProject = projectId;
    setActionMessage("Voiceover queued.", "ok");
    await loadProjects();
  } catch (error) {
    setActionMessage(error.message, "error");
  }
}

async function importBemaEpisode() {
  const episodeInput = form.querySelector("input[name=bema_episode]");
  const episode = Number(episodeInput.value.trim());
  if (!Number.isFinite(episode) || episode <= 0) {
    setFormMessage("Enter a valid BEMA episode number.", "error");
    return;
  }

  const previousLabel = bemaImportButton.textContent;
  bemaImportButton.disabled = true;
  bemaImportButton.textContent = "Importing...";
  setFormMessage("");
  try {
    const project = await api("/api/projects/bema", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ episode: Math.floor(episode), include_transcript: true }),
    });
    selectedProject = project.id;
    currentDetailSig = null;
    episodeInput.value = "";
    setFormMessage(`Imported BEMA episode ${Math.floor(episode)}.`, "ok");
    await loadProjects();
  } catch (error) {
    setFormMessage(error.message, "error");
  } finally {
    bemaImportButton.disabled = false;
    bemaImportButton.textContent = previousLabel;
  }
}

async function loadProjects() {
  try {
    const [providers, projects] = await Promise.all([api("/api/providers"), api("/api/projects")]);
    renderProviders(providers);
    renderProjects(projects);
    if (selectedProject) {
      const project = projects.find((item) => item.id === selectedProject);
      if (project) {
        await renderDetail(project);
      }
    }
  } catch (error) {
    setFormMessage(`Could not reach the worker portal: ${error.message}`, "error");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const sourceFile = form.querySelector("input[name=source]").files[0];
  const sourceUrl = form.querySelector("input[name=source_url]").value.trim();
  if (!sourceFile && !sourceUrl) {
    setFormMessage("Upload a source file or paste a link first.", "error");
    return;
  }
  const formData = new FormData(form);
  const stagesToRun = selectedStagesValue();
  if (stagesToRun) {
    formData.set("stages_to_run", stagesToRun);
  }
  const submit = form.querySelector("button[type=submit]");
  submit.disabled = true;
  submit.textContent = "Creating...";
  setFormMessage("");
  try {
    const project = await api("/api/projects", { method: "POST", body: formData });
    selectedProject = project.id;
    currentDetailSig = null;
    form.reset();
    setFormMessage("Project created and queued.", "ok");
    await loadProjects();
  } catch (error) {
    setFormMessage(error.message, "error");
  } finally {
    submit.disabled = false;
    submit.textContent = "Create project";
  }
});

document.querySelector("#refresh").addEventListener("click", loadProjects);
stageSelectAllButton.addEventListener("click", () => setAllStages(true));
stageDeselectAllButton.addEventListener("click", () => setAllStages(false));
bemaImportButton.addEventListener("click", () => {
  void importBemaEpisode();
});

loadProjects();
refreshTimer = setInterval(loadProjects, 5000);
