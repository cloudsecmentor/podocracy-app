const searchParams = new URLSearchParams(window.location.search);
const projectId = searchParams.get("project") || "";
let improvedFilename = searchParams.get("file") || "";

const workspace = document.querySelector("#improved-workspace");

const state = {
  projectStatus: "Loading...",
  projectLabel: "",
  chunks: [],
  rawFallback: null,
  isLoading: true,
  isSaving: false,
  dirty: false,
  message: "",
  error: "",
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.text();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
}

function blankChunk(seed = {}) {
  return {
    start: seed.start || "",
    end: seed.end || "",
    speaker: seed.speaker || "",
    text: seed.text || "",
    imp: seed.imp || "",
  };
}

function normalizeChunk(item) {
  return {
    start: String(item.start ?? ""),
    end: String(item.end ?? ""),
    speaker: String(item.speaker ?? ""),
    text: String(item.text ?? ""),
    imp: String(item.imp ?? ""),
  };
}

function isChunkArray(data) {
  return Array.isArray(data) && data.every((item) => item && typeof item === "object" && "start" in item && "end" in item && "text" in item && "imp" in item);
}

function serializeChunks(chunks) {
  return JSON.stringify(chunks, null, 2);
}

function setDirty(value) {
  state.dirty = value;
}

function setError(message) {
  state.error = message || "";
}

function setMessage(message) {
  state.message = message || "";
}

function updateChunk(index, field, value) {
  state.chunks[index] = { ...state.chunks[index], [field]: value };
  state.dirty = true;
}

function insertChunk(index, position) {
  const ref = state.chunks[index];
  const seed = ref
    ? { start: position === "before" ? ref.start : ref.end, end: position === "before" ? ref.start : ref.end, speaker: ref.speaker }
    : {};
  const next = [...state.chunks];
  next.splice(position === "before" ? index : index + 1, 0, blankChunk(seed));
  state.chunks = next;
  state.dirty = true;
  render();
  requestAnimationFrame(() => {
    const card = workspace.querySelector(`[data-index="${position === "before" ? index : index + 1}"]`);
    card?.querySelector("input,textarea")?.focus();
  });
}

function deleteChunk(index) {
  if (!confirm(`Delete chunk #${index + 1}?`)) return;
  state.chunks = state.chunks.filter((_, i) => i !== index);
  state.dirty = true;
  render();
}

function loadImprovedBody(raw) {
  let parsed = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = null;
  }

  if (isChunkArray(parsed)) {
    state.chunks = parsed.map(normalizeChunk);
    state.rawFallback = null;
  } else {
    state.chunks = [];
    state.rawFallback = raw;
  }
}

function workspaceHtml() {
  if (!projectId) {
    return `
      <div class="panel">
        <p class="error">Missing project id.</p>
      </div>
    `;
  }

  const downloadHref = improvedFilename ? `/api/projects/${encodeURIComponent(projectId)}/download/${encodeURIComponent(improvedFilename)}` : "#";
  const openHref = improvedFilename ? `/improved.html?project=${encodeURIComponent(projectId)}&file=${encodeURIComponent(improvedFilename)}` : "#";
  const chunkCount = state.rawFallback === null ? `${state.chunks.length} chunks` : "raw JSON";

  return `
    <div class="editor-head">
      <div>
        <p class="muted">Project</p>
        <h2>${escapeHtml(state.projectLabel || projectId)}</h2>
        <p class="muted">${escapeHtml(projectId)}${improvedFilename ? ` · ${escapeHtml(improvedFilename)}` : ""}</p>
      </div>
      <div class="editor-status">${escapeHtml(state.projectStatus)}</div>
    </div>

    ${state.error ? `<p class="message error">${escapeHtml(state.error)}</p>` : ""}
    ${state.message ? `<p class="message ok">${escapeHtml(state.message)}</p>` : ""}

    <div class="toolbar">
      <button type="button" id="save-improved" ${state.isSaving ? "disabled" : ""}>${state.isSaving ? "Saving..." : "Save"}</button>
      <button type="button" id="add-chunk" class="secondary" ${state.isSaving || state.rawFallback !== null ? "disabled" : ""}>Add chunk</button>
      <a class="artifact" href="${downloadHref}" ${improvedFilename ? "" : 'aria-disabled="true" tabindex="-1"'} download>Download</a>
      <a class="artifact" href="${openHref}" ${improvedFilename ? "" : 'aria-disabled="true" tabindex="-1"'}>Open improved page</a>
      <button type="button" id="start-voiceover" class="secondary" ${state.isSaving ? "disabled" : ""}>Start voiceover</button>
      <a class="secondary" href="/">Back</a>
      <span class="badge">${chunkCount}</span>
      ${state.dirty ? '<span class="badge warning">Unsaved changes</span>' : ""}
    </div>

    ${state.rawFallback !== null ? `
      <div class="chunk-card raw-editor">
        <div class="chunk-card-head">
          <strong>Raw JSON fallback</strong>
          <span class="muted">Edit the file directly because it is not a chunk array.</span>
        </div>
        <textarea id="raw-json-editor" spellcheck="false">${escapeHtml(state.rawFallback)}</textarea>
      </div>
    ` : `
      <div class="chunk-list">
        ${state.chunks.length ? state.chunks.map((chunk, index) => `
          <section class="chunk-card" data-index="${index}">
            <div class="chunk-card-head">
              <strong>#${index + 1}</strong>
              <span class="muted">chunk</span>
            </div>
            <div class="chunk-grid">
              <label>
                <span>Start</span>
                <input data-field="start" value="${escapeHtml(chunk.start)}">
              </label>
              <label>
                <span>End</span>
                <input data-field="end" value="${escapeHtml(chunk.end)}">
              </label>
              <label class="span-2">
                <span>Speaker</span>
                <input data-field="speaker" value="${escapeHtml(chunk.speaker)}">
              </label>
              <label class="span-2">
                <span>Original text</span>
                <textarea data-field="text" spellcheck="false">${escapeHtml(chunk.text)}</textarea>
              </label>
              <label class="span-2">
                <span>Improved text</span>
                <textarea data-field="imp" spellcheck="false">${escapeHtml(chunk.imp)}</textarea>
              </label>
            </div>
            <div class="chunk-actions">
              <button type="button" class="secondary" data-action="insert-before">+ Before</button>
              <button type="button" class="secondary" data-action="insert-after">+ After</button>
              <button type="button" class="secondary danger" data-action="delete">Delete</button>
            </div>
          </section>
        `).join("") : `
          <div class="empty-state">
            <p class="muted">No chunks yet.</p>
            <button type="button" id="add-first-chunk">Add first chunk</button>
          </div>
        `}
      </div>
    `}
  `;
}

function bindWorkspace() {
  const saveButton = document.querySelector("#save-improved");
  const addChunkButton = document.querySelector("#add-chunk");
  const startVoiceoverButton = document.querySelector("#start-voiceover");
  const rawEditor = document.querySelector("#raw-json-editor");
  const addFirstChunkButton = document.querySelector("#add-first-chunk");

  if (saveButton) {
    saveButton.addEventListener("click", () => {
      void save();
    });
  }

  if (addChunkButton) {
    addChunkButton.addEventListener("click", () => {
      state.chunks = [...state.chunks, blankChunk()];
      state.dirty = true;
      render();
    });
  }

  if (addFirstChunkButton) {
    addFirstChunkButton.addEventListener("click", () => {
      state.chunks = [blankChunk()];
      state.dirty = true;
      render();
    });
  }

  if (startVoiceoverButton) {
    startVoiceoverButton.addEventListener("click", () => {
      void startVoiceover();
    });
  }

  if (rawEditor) {
    rawEditor.addEventListener("input", () => {
      state.rawFallback = rawEditor.value;
      state.dirty = true;
    });
  }

  workspace.querySelectorAll("[data-index]").forEach((card) => {
    const index = Number(card.getAttribute("data-index"));
    card.querySelectorAll("[data-field]").forEach((fieldEl) => {
      const field = fieldEl.getAttribute("data-field");
      const handler = () => updateChunk(index, field, fieldEl.value);
      fieldEl.addEventListener("input", handler);
      fieldEl.addEventListener("change", handler);
    });

    card.querySelectorAll("[data-action]").forEach((button) => {
      const action = button.getAttribute("data-action");
      button.addEventListener("click", () => {
        if (action === "insert-before") insertChunk(index, "before");
        if (action === "insert-after") insertChunk(index, "after");
        if (action === "delete") deleteChunk(index);
      });
    });
  });
}

function render() {
  workspace.innerHTML = workspaceHtml();
  bindWorkspace();
}

async function save() {
  if (!projectId || !improvedFilename) return;
  state.isSaving = true;
  state.error = "";
  state.message = "";
  render();
  try {
    const content = state.rawFallback !== null ? state.rawFallback : serializeChunks(state.chunks);
    await api(`/api/projects/${projectId}/files/${encodeURIComponent(improvedFilename)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    state.dirty = false;
    state.message = "Saved.";
  } catch (error) {
    state.error = error.message;
  } finally {
    state.isSaving = false;
    render();
  }
}

async function startVoiceover() {
  if (!projectId) return;
  state.isSaving = true;
  state.error = "";
  state.message = "";
  render();
  try {
    await save();
    await api(`/api/projects/${projectId}/voiceover`, { method: "POST" });
    state.message = "Voiceover queued.";
  } catch (error) {
    state.error = error.message;
  } finally {
    state.isSaving = false;
    render();
  }
}

async function load() {
  if (!projectId) {
    state.isLoading = false;
    state.projectStatus = "Missing project id.";
    render();
    return;
  }

  try {
    const project = await api(`/api/projects/${projectId}`);
    const status = project.status || {};
    state.projectLabel = project.metadata?.source_filename || projectId;
    state.projectStatus = `${status.state || "unknown"} | ${status.stage || "unknown"} | ${Number(status.progress || 0)}%`;

    if (!improvedFilename) {
      const fileInfo = await api(`/api/projects/${projectId}/improved-file`);
      improvedFilename = fileInfo.filename || "";
    }
    if (!improvedFilename) {
      throw new Error("Improved transcript not found.");
    }

    const raw = await api(`/api/projects/${projectId}/files/${encodeURIComponent(improvedFilename)}`);
    loadImprovedBody(raw);
    state.error = "";
    state.message = "";
  } catch (error) {
    state.error = error.message;
  } finally {
    state.isLoading = false;
    render();
  }
}

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

window.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    void save();
  }
});

render();
load().catch((error) => {
  state.error = error.message;
  render();
});
