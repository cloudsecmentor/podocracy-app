let selectedProject = null;
let refreshTimer = null;
let improvedDraft = "";
let improvedDraftProjectId = null;
let improvedDraftFilename = null;
let improvedDirty = false;
let improvedLoading = false;

const providerStatus = document.querySelector("#provider-status");
const projectsEl = document.querySelector("#projects");
const detailEl = document.querySelector("#detail");
const form = document.querySelector("#project-form");
const bemaImportButton = document.querySelector("#import-bema");
const stageSelectAllButton = document.querySelector("#stages-select-all");
const stageDeselectAllButton = document.querySelector("#stages-deselect-all");
const stageCheckboxes = Array.from(document.querySelectorAll('input[name="stage"]'));

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
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

function selectedStagesValue() {
  const selected = stageCheckboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
  return selected.join("+");
}

function setAllStages(checked) {
  stageCheckboxes.forEach((checkbox) => {
    checkbox.checked = checked;
  });
}

function stageButtonsEnabled() {
  return stageCheckboxes.length > 0;
}

function renderProviders(providers) {
  const parts = Object.entries(providers).map(([name, ok]) => `${name}: ${ok ? "set" : "missing"}`);
  providerStatus.textContent = parts.join(" | ");
}

function renderProjects(projects) {
  if (!projects.length) {
    projectsEl.innerHTML = "<p class=\"empty\">No projects yet.</p>";
    return;
  }

  projectsEl.innerHTML = "";
  for (const project of projects) {
    const button = document.createElement("button");
    button.className = `project ${selectedProject === project.id ? "active" : ""}`;
    const metadata = project.metadata || {};
    const status = project.status || {};
    button.innerHTML = `
      <strong>${metadata.source_filename || project.id}</strong>
      <span class="${statusClass(status.state)}">${status.state || "unknown"}</span>
      <span class="muted">${metadata.target_language || ""} ${status.stage ? `- ${status.stage}` : ""}</span>
      <div class="progress"><div style="width:${Number(status.progress || 0)}%"></div></div>
    `;
    button.addEventListener("click", () => {
      selectedProject = project.id;
      renderDetail(project);
      loadProjects();
    });
    projectsEl.appendChild(button);
  }
}

function scrollLogsToBottom() {
  const logs = detailEl.querySelector("pre");
  if (!logs) return;
  logs.scrollTop = logs.scrollHeight;
}

function scheduleScrollLogsToBottom() {
  requestAnimationFrame(() => {
    scrollLogsToBottom();
    requestAnimationFrame(scrollLogsToBottom);
  });
  setTimeout(scrollLogsToBottom, 0);
  setTimeout(scrollLogsToBottom, 100);
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

  detailEl.className = "detail";
  detailEl.innerHTML = `
    <p><strong>${metadata.source_filename || project.id}</strong></p>
    <p class="muted">${project.id}</p>
    <p><span class="${statusClass(status.state)}">${status.state || "unknown"}</span></p>
    <p class="muted">Stage: ${status.stage || "unknown"} | Progress: ${Number(status.progress || 0)}%</p>
    <p>${status.message || ""}</p>
    <div class="progress"><div style="width:${Number(status.progress || 0)}%"></div></div>
    <div class="stage-summary">
      <strong>Stage history</strong>
      <ol class="stage-list">
        ${stages
          .map(
            (item) => `
              <li class="${item.status || ""}">
                <span>${item.name || "stage"}</span>
                <span class="muted">${item.status || "unknown"}</span>
              </li>`,
          )
          .join("")}
      </ol>
    </div>
    ${hasImproved ? `
      <div class="editor">
        <strong>Improved transcript</strong>
        <textarea id="improved-editor" ${improvedLoading ? "disabled" : ""}>${escapeHtml(improvedDraft)}</textarea>
        <div class="editor-actions">
          <button type="button" id="save-improved" class="secondary" ${improvedLoading ? "disabled" : ""}>Save</button>
          <a class="artifact" href="/api/projects/${project.id}/download/${encodeURIComponent(improvedFilename)}" download>Download</a>
          <a class="artifact" href="/improved.html?project=${encodeURIComponent(project.id)}&file=${encodeURIComponent(improvedFilename)}">Open improved page</a>
          <button type="button" id="start-voiceover" class="secondary" ${improvedLoading ? "disabled" : ""}>Start voiceover</button>
        </div>
        <p class="muted">Edit the improved JSON, save it, or queue voiceover from the saved version.</p>
      </div>
    ` : ""}
    <div class="artifacts">
      ${artifacts.map((item) => `<a class="artifact" href="${item.download_url}">${item.name}</a>`).join("")}
      <a class="artifact" href="/api/projects/${project.id}/support-bundle">support bundle</a>
    </div>
    <h2 style="margin-top:18px">Logs</h2>
    <pre>${escapeHtml(logs)}</pre>
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

  scheduleScrollLogsToBottom();
}

async function saveImproved(projectId) {
  const editor = detailEl.querySelector("#improved-editor");
  if (!editor) return;
  const filename = improvedDraftProjectId === projectId ? improvedDraftFilename : null;
  if (!filename) {
    alert("Improved file is missing.");
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
    await loadProjects();
  } catch (error) {
    alert(error.message);
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
    await loadProjects();
  } catch (error) {
    alert(error.message);
  }
}

async function importBemaEpisode() {
  const episodeInput = form.querySelector("input[name=bema_episode]");
  const episode = Number(episodeInput.value.trim());
  if (!Number.isFinite(episode) || episode <= 0) {
    alert("Enter a valid BEMA episode number.");
    return;
  }

  const previousLabel = bemaImportButton.textContent;
  bemaImportButton.disabled = true;
  bemaImportButton.textContent = "Importing...";
  try {
    const project = await api("/api/projects/bema", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ episode: Math.floor(episode), include_transcript: true }),
    });
    selectedProject = project.id;
    episodeInput.value = "";
    await loadProjects();
  } catch (error) {
    alert(error.message);
  } finally {
    bemaImportButton.disabled = false;
    bemaImportButton.textContent = previousLabel;
  }
}

async function loadProjects() {
  const [providers, projects] = await Promise.all([api("/api/providers"), api("/api/projects")]);
  renderProviders(providers);
  renderProjects(projects);
  if (selectedProject) {
    const project = projects.find((item) => item.id === selectedProject);
    if (project) {
      await renderDetail(project);
      scheduleScrollLogsToBottom();
    }
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const sourceFile = form.querySelector("input[name=source]").files[0];
  const sourceUrl = form.querySelector("input[name=source_url]").value.trim();
  if (!sourceFile && !sourceUrl) {
    alert("Upload a source file or provide a source URL.");
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
  try {
    const project = await api("/api/projects", { method: "POST", body: formData });
    selectedProject = project.id;
    form.reset();
    await loadProjects();
  } catch (error) {
    alert(error.message);
  } finally {
    submit.disabled = false;
    submit.textContent = "Create Project";
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
