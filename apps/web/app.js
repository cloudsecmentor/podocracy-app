let selectedProject = null;
let refreshTimer = null;

const providerStatus = document.querySelector("#provider-status");
const projectsEl = document.querySelector("#projects");
const detailEl = document.querySelector("#detail");
const form = document.querySelector("#project-form");

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

async function renderDetail(project) {
  const status = project.status || {};
  const metadata = project.metadata || {};
  const artifacts = await api(`/api/projects/${project.id}/artifacts`);
  const logs = await api(`/api/projects/${project.id}/logs`);
  detailEl.className = "detail";
  detailEl.innerHTML = `
    <p><strong>${metadata.source_filename || project.id}</strong></p>
    <p class="muted">${project.id}</p>
    <p><span class="${statusClass(status.state)}">${status.state || "unknown"}</span></p>
    <p>${status.message || ""}</p>
    <div class="progress"><div style="width:${Number(status.progress || 0)}%"></div></div>
    <div class="artifacts">
      ${artifacts.map((item) => `<a class="artifact" href="${item.download_url}">${item.name}</a>`).join("")}
      <a class="artifact" href="/api/projects/${project.id}/support-bundle">support bundle</a>
    </div>
    <h2 style="margin-top:18px">Logs</h2>
    <pre>${logs.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]))}</pre>
  `;
}

async function loadProjects() {
  const [providers, projects] = await Promise.all([api("/api/providers"), api("/api/projects")]);
  renderProviders(providers);
  renderProjects(projects);
  if (selectedProject) {
    const project = projects.find((item) => item.id === selectedProject);
    if (project) await renderDetail(project);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(form);
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

loadProjects();
refreshTimer = setInterval(loadProjects, 5000);
