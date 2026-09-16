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

// Per-chunk audio, kept outside `state` because it is owned by the worker and
// refreshed on its own schedule.
const segments = {
  byId: new Map(),
  available: false,
  busy: false,
  stale: false,
  jobKind: null,
  recordingId: null,
  playingId: null,
  pendingId: null,
};

const SEGMENT_LABELS = {
  ready: "Generated",
  stale: "Text changed since generation",
  missing: "No audio",
  skipped: "No text",
  failed: "Failed",
  pending_ingest: "Recorded, not yet processed",
};

const RECORDER_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
];

let recorder = null;
let recorderChunks = [];
let recorderMime = "audio/webm";
let pollTimer = null;
let playbackObjectUrl = null;
let suppressPlayerError = false;

// One player for the page: `render()` replaces the whole workspace, so an
// <audio> inside a chunk card would be destroyed mid-playback.
const player = typeof Audio === "function" ? new Audio() : null;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(apiErrorMessage(text) || response.statusText);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.text();
}

function apiErrorMessage(text) {
  // FastAPI wraps every error as {"detail": "..."}; showing the raw JSON helps nobody.
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // Not JSON, fall through to the raw body.
  }
  return text;
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
  // Spread first: the worker owns `chunk_id` and `audio` on every chunk, and the
  // pipeline also carries fields this editor never shows, such as `dltrans`.
  // Rebuilding from a fixed field list would delete all of them on save.
  return {
    ...item,
    start: String(item.start ?? ""),
    end: String(item.end ?? ""),
    speaker: String(item.speaker ?? ""),
    text: String(item.text ?? ""),
    imp: String(item.imp ?? ""),
  };
}

function isChunkArray(data) {
  // `end` is genuinely optional: the assembler derives a missing end from the
  // next chunk's start, and real transcripts ship chunks without one.
  return (
    Array.isArray(data) &&
    data.length > 0 &&
    data.every((item) => item && typeof item === "object" && "start" in item && ("imp" in item || "text" in item))
  );
}

function serializeChunks(chunks) {
  // An empty optional field means absent. Writing `"end": ""` back would make
  // the assembler build the filename `0738-.ogg` and drop the chunk from the mix.
  const cleaned = chunks.map((chunk) => {
    const copy = { ...chunk };
    for (const field of ["end", "speaker"]) {
      if (copy[field] === "") delete copy[field];
    }
    return copy;
  });
  return JSON.stringify(cleaned, null, 2);
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

/* ----------------------------- Segments ----------------------------- */

function segmentInfo(chunk) {
  return (chunk && chunk.chunk_id && segments.byId.get(chunk.chunk_id)) || null;
}

function segmentLabel(info) {
  if (!info) return "No audio";
  if (info.status === "ready" && info.source === "recording") return "Recorded";
  return SEGMENT_LABELS[info.status] || info.status;
}

function segmentTone(info) {
  if (!info) return "";
  if (info.status === "failed") return "danger";
  if (info.status === "stale" || info.status === "pending_ingest") return "warning";
  if (info.status === "ready") return "ok";
  return "";
}

function formatDuration(ms) {
  if (!ms && ms !== 0) return "";
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function extensionForMime(mime) {
  if (!mime) return "webm";
  if (mime.includes("ogg")) return "ogg";
  if (mime.includes("mp4")) return "m4a";
  if (mime.includes("mpeg")) return "mp3";
  if (mime.includes("wav")) return "wav";
  return "webm";
}

async function loadSegments() {
  if (!projectId) return;
  try {
    const payload = await api(`/api/projects/${projectId}/segments`);
    segments.byId = new Map((payload.segments || []).map((item) => [item.chunk_id, item]));
    segments.available = true;
    segments.busy = Boolean(payload.busy);
    segments.stale = Boolean(payload.stale);
    segments.jobKind = payload.job_kind || null;
  } catch {
    // A transcript the editor can only show as raw JSON has no per-chunk audio.
    segments.available = false;
    segments.byId = new Map();
  }
}

function segmentSummary() {
  if (!segments.available) return "";
  const counts = { ready: 0, recorded: 0, stale: 0, missing: 0, failed: 0 };
  for (const info of segments.byId.values()) {
    if (info.status === "ready") counts[info.source === "recording" ? "recorded" : "ready"] += 1;
    else if (info.status === "stale") counts.stale += 1;
    else if (info.status === "failed") counts.failed += 1;
    else if (info.status === "missing") counts.missing += 1;
  }
  const parts = [];
  if (counts.ready) parts.push(`${counts.ready} generated`);
  if (counts.recorded) parts.push(`${counts.recorded} recorded`);
  if (counts.stale) parts.push(`${counts.stale} stale`);
  if (counts.missing) parts.push(`${counts.missing} missing`);
  if (counts.failed) parts.push(`${counts.failed} failed`);
  return parts.join(" \u00b7 ");
}

// Recording and regeneration both stamp the chunk's saved text, so unsaved edits
// would produce audio that is marked stale the moment it lands.
function segmentActionsLocked() {
  return segments.busy || state.dirty || state.isSaving;
}

function lockReason() {
  if (segments.busy) return "A job is running for this project";
  if (state.dirty || state.isSaving) return "Save your changes first";
  return "";
}

function segmentRowHtml(chunk) {
  if (!segments.available || !chunk.chunk_id) return "";
  const chunkId = chunk.chunk_id;
  const info = segmentInfo(chunk);
  const isRecording = segments.recordingId === chunkId;
  const isPlaying = segments.playingId === chunkId;
  const isPending = segments.pendingId === chunkId;
  const otherRecording = Boolean(segments.recordingId) && !isRecording;
  const locked = segmentActionsLocked() || isPending || otherRecording;
  const hasAudio = Boolean(info && info.has_audio);
  const hasText = Boolean(info && info.status !== "skipped");
  const reason = lockReason();
  const duration = formatDuration(info && info.duration_ms);

  return `
    <div class="segment-row" data-segment="${escapeHtml(chunkId)}">
      <button type="button" class="segment-button${isRecording ? " recording" : ""}" data-segment-action="record"
        title="${isRecording ? "Stop recording" : "Record this chunk"}" ${locked && !isRecording ? "disabled" : ""}>
        ${isRecording ? "\u23F9" : "\uD83C\uDFA4"}
      </button>
      <button type="button" class="segment-button" data-segment-action="play"
        title="Play" ${!hasAudio || isRecording ? "disabled" : ""}>${isPlaying ? "\u23F8" : "\u25B6"}</button>
      <button type="button" class="segment-button" data-segment-action="regenerate"
        title="Regenerate this chunk with TTS" ${locked || !hasText ? "disabled" : ""}>\u27F3</button>
      <button type="button" class="segment-button danger" data-segment-action="delete"
        title="Delete this chunk's audio" ${!hasAudio || locked ? "disabled" : ""}>\uD83D\uDDD1</button>
      <span class="segment-status ${segmentTone(info)}">${escapeHtml(segmentLabel(info))}</span>
      ${duration ? `<span class="muted segment-meta">${escapeHtml(duration)}</span>` : ""}
      ${isPending ? '<span class="muted segment-meta">Working\u2026</span>' : ""}
      ${info && info.error ? `<span class="segment-meta danger-text">${escapeHtml(info.error)}</span>` : ""}
      ${locked && !isPending && reason ? `<span class="muted segment-meta">${escapeHtml(reason)}</span>` : ""}
    </div>
  `;
}

async function uploadRecording(chunkId, blob) {
  segments.pendingId = chunkId;
  render();
  try {
    const form = new FormData();
    form.append("file", blob, `${chunkId}.${extensionForMime(blob.type)}`);
    await api(`/api/projects/${projectId}/segments/${chunkId}/audio`, { method: "PUT", body: form });
    await loadSegments();
    setMessage("Recording saved.");
  } catch (error) {
    setError(error.message);
  } finally {
    segments.pendingId = null;
    render();
  }
}

async function startRecording(chunkId) {
  if (segments.recordingId) return;
  if (!navigator.mediaDevices || typeof MediaRecorder === "undefined") {
    setError("This browser cannot record audio.");
    render();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = RECORDER_MIME_TYPES.find((candidate) => MediaRecorder.isTypeSupported(candidate));
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    recorderMime = recorder.mimeType || mime || "audio/webm";
    recorderChunks = [];
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) recorderChunks.push(event.data);
    };
    recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(recorderChunks, { type: recorderMime });
      recorder = null;
      recorderChunks = [];
      segments.recordingId = null;
      if (blob.size > 0) await uploadRecording(chunkId, blob);
      else render();
    };
    recorder.start();
    segments.recordingId = chunkId;
    setError("");
    render();
  } catch {
    setError("Microphone access denied.");
    render();
  }
}

function stopRecording() {
  if (!recorder) {
    segments.recordingId = null;
    render();
    return;
  }
  recorder.stop();
}

function releasePlaybackUrl() {
  if (!playbackObjectUrl) return;
  URL.revokeObjectURL(playbackObjectUrl);
  playbackObjectUrl = null;
}

async function togglePlayback(chunkId) {
  if (!player) return;
  if (segments.playingId === chunkId && !player.paused) {
    player.pause();
    return;
  }
  try {
    const response = await fetch(`/api/projects/${projectId}/segments/${chunkId}/audio`);
    if (!response.ok) throw new Error(apiErrorMessage(await response.text()) || "Playback failed");
    const blob = await response.blob();
    releasePlaybackUrl();
    playbackObjectUrl = URL.createObjectURL(blob);
    player.src = playbackObjectUrl;
    player.currentTime = 0;
    segments.playingId = chunkId;
    render();
    await player.play();
  } catch (error) {
    segments.playingId = null;
    setError(error.message || "Playback failed.");
    render();
  }
}

async function deleteSegmentAudio(chunkId) {
  if (!confirm(`Delete audio for chunk ${chunkId}?`)) return;
  segments.pendingId = chunkId;
  render();
  try {
    if (segments.playingId === chunkId && player) {
      suppressPlayerError = true;
      player.pause();
      player.removeAttribute("src");
      segments.playingId = null;
      releasePlaybackUrl();
    }
    await api(`/api/projects/${projectId}/segments/${chunkId}/audio`, { method: "DELETE" });
    await loadSegments();
    setMessage("Audio deleted.");
  } catch (error) {
    setError(error.message);
  } finally {
    segments.pendingId = null;
    render();
  }
}

async function regenerateSegment(chunkId) {
  segments.pendingId = chunkId;
  render();
  try {
    await api(`/api/projects/${projectId}/segments/${chunkId}/regenerate`, { method: "POST" });
    setMessage(`Queued regeneration of ${chunkId}.`);
    await loadSegments();
    scheduleStatusPoll();
  } catch (error) {
    setError(error.message);
  } finally {
    segments.pendingId = null;
    render();
  }
}

async function queueVoiceover(endpoint, successMessage) {
  if (!projectId) return;
  setError("");
  setMessage("");
  try {
    if (state.dirty) await save();
    await api(`/api/projects/${projectId}/${endpoint}`, { method: "POST" });
    setMessage(successMessage);
    await loadSegments();
    scheduleStatusPoll();
  } catch (error) {
    setError(error.message);
  } finally {
    render();
  }
}

async function stopProcessing() {
  if (!projectId) return;
  setError("");
  setMessage("");
  try {
    const result = await api(`/api/projects/${projectId}/cancel`, { method: "POST" });
    setMessage(
      result.status?.state === "cancelling"
        ? "Stopping. Chunks already generated are kept."
        : "Stopped.",
    );
    await loadSegments();
    scheduleStatusPoll();
  } catch (error) {
    setError(error.message);
  } finally {
    render();
  }
}

function scheduleStatusPoll() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    const stillBusy = await refreshStatus();
    if (!stillBusy) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    render();
  }, 4000);
}

async function refreshStatus() {
  try {
    const project = await api(`/api/projects/${projectId}`);
    const status = project.status || {};
    const kind = status.job_kind ? ` | ${status.job_kind}` : "";
    state.projectStatus = `${status.state || "unknown"} | ${status.stage || "unknown"}${kind} | ${Number(status.progress || 0)}%`;
    await loadSegments();
    return segments.busy;
  } catch {
    return false;
  }
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
  const summary = segmentSummary();
  const segmentSummaryBadge = summary ? `<span class="badge">${escapeHtml(summary)}</span>` : "";

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
      <a class="artifact" href="${openHref}" ${improvedFilename ? "" : 'aria-disabled="true" tabindex="-1"'}>Open editor</a>
      <button type="button" id="start-voiceover" class="secondary" ${state.isSaving || segments.busy ? "disabled" : ""}>Start voiceover</button>
      ${segments.available ? `
        <button type="button" id="generate-missing" class="secondary" ${state.isSaving || segments.busy ? "disabled" : ""}>Generate missing</button>
        <button type="button" id="build-voiceover" class="secondary" ${state.isSaving || segments.busy ? "disabled" : ""}>Build voiceover</button>
      ` : ""}
      ${segments.busy || segments.stale ? `
        <button type="button" id="stop-processing" class="secondary danger">${segments.stale ? "Reset stuck state" : "Stop processing"}</button>
      ` : ""}
      <a class="secondary" href="/">Back</a>
      <span class="badge">${chunkCount}</span>
      ${segmentSummaryBadge}
      ${segments.busy ? '<span class="badge warning">Job running</span>' : ""}
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
            ${segmentRowHtml(chunk)}
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

  const stopButton = document.querySelector("#stop-processing");
  if (stopButton) {
    stopButton.addEventListener("click", () => {
      void stopProcessing();
    });
  }

  const generateMissingButton = document.querySelector("#generate-missing");
  if (generateMissingButton) {
    generateMissingButton.addEventListener("click", () => {
      void queueVoiceover("voiceover/synthesize", "Generation of missing chunks queued.");
    });
  }

  const buildVoiceoverButton = document.querySelector("#build-voiceover");
  if (buildVoiceoverButton) {
    buildVoiceoverButton.addEventListener("click", () => {
      void queueVoiceover("voiceover/build", "Voiceover assembly queued.");
    });
  }

  workspace.querySelectorAll("[data-segment]").forEach((row) => {
    const chunkId = row.getAttribute("data-segment");
    row.querySelectorAll("[data-segment-action]").forEach((button) => {
      const action = button.getAttribute("data-segment-action");
      button.addEventListener("click", () => {
        if (action === "record") {
          if (segments.recordingId === chunkId) stopRecording();
          else void startRecording(chunkId);
        }
        if (action === "play") void togglePlayback(chunkId);
        if (action === "regenerate") void regenerateSegment(chunkId);
        if (action === "delete") void deleteSegmentAudio(chunkId);
      });
    });
  });

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
  const topStatus = document.querySelector("#project-status");
  if (topStatus) topStatus.textContent = state.error || state.projectStatus;
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
    // The API stamps stable chunk ids onto anything newly inserted, so re-read
    // rather than leaving memory and disk holding different chunks.
    await loadSegments();
    const refreshed = await api(`/api/projects/${projectId}/files/${encodeURIComponent(improvedFilename)}`);
    loadImprovedBody(refreshed);
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
    await loadSegments();
    scheduleStatusPoll();
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
    const kind = status.job_kind ? ` | ${status.job_kind}` : "";
    state.projectStatus = `${status.state || "unknown"} | ${status.stage || "unknown"}${kind} | ${Number(status.progress || 0)}%`;

    if (!improvedFilename) {
      const fileInfo = await api(`/api/projects/${projectId}/improved-file`);
      improvedFilename = fileInfo.filename || "";
    }
    if (!improvedFilename) {
      throw new Error("Improved transcript not found.");
    }

    // Before reading the transcript: this assigns and persists stable chunk ids,
    // and reading afterwards is what puts them in memory for the next save.
    await loadSegments();

    const raw = await api(`/api/projects/${projectId}/files/${encodeURIComponent(improvedFilename)}`);
    loadImprovedBody(raw);
    state.error = "";
    state.message = "";
    if (segments.busy) scheduleStatusPoll();
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

if (player) {
  player.addEventListener("ended", () => {
    segments.playingId = null;
    render();
  });
  player.addEventListener("pause", () => {
    if (!segments.playingId) return;
    segments.playingId = null;
    render();
  });
  player.addEventListener("error", () => {
    if (suppressPlayerError) {
      suppressPlayerError = false;
      return;
    }
    if (!segments.playingId) return;
    segments.playingId = null;
    setError("Unable to play this chunk's audio.");
    render();
  });
}

render();
load().catch((error) => {
  state.error = error.message;
  render();
});
