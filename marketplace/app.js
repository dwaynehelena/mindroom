// Skill Foundry marketplace SPA — vanilla JS, no build step.
// Mutations always delegate to the backend HTTP endpoints (which call the CLI
// installer), so the UI never touches the filesystem directly.

const state = {
  skills: [],
  query: "",
  tag: "",
};

const API = "/api/marketplace";

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {
      /* keep default detail */
    }
    throw new Error(detail);
  }
  return response.json();
}

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function card(skill) {
  const installed = skill.installed_version != null;
  const status = installed
    ? `<span class="badge installed">installed ${esc(skill.installed_version)}</span>`
    : `<span class="badge available">available</span>`;
  const tags = (skill.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  return `
    <div class="card">
      <h3><a href="#/skill/${encodeURIComponent(skill.name)}">${esc(skill.name)}</a></h3>
      <p>${esc(skill.description)}</p>
      <div class="meta">
        <span>v${esc(skill.latest_version)}</span>
        <span>${esc(skill.author)}</span>
        ${status}
      </div>
      <div class="meta">${tags}</div>
      <div class="card-actions">
        ${installed ? `
          <button data-action="update" data-name="${esc(skill.name)}">Update</button>
          <button data-action="uninstall" data-name="${esc(skill.name)}">Uninstall</button>` : `
          <button data-action="install" data-name="${esc(skill.name)}">Install</button>`}
      </div>
    </div>`;
}

function detailView(skill) {
  const installed = skill.installed_version != null;
  const versions = (skill.all_versions || []).map((v) => `<option>${esc(v)}</option>`).join("");
  const deps = (skill.dependencies || []).length
    ? `<div class="deps"><h4>Dependencies</h4><ul>${skill.dependencies.map((d) => `<li>${esc(d)}</li>`).join("")}</ul></div>`
    : "";
  return `
    <div class="detail">
      <h2>${esc(skill.name)}</h2>
      <p>${esc(skill.description)}</p>
      <div class="meta">
        <span>Author: ${esc(skill.author)}</span>
        <span>License: ${esc(skill.license)}</span>
        <span>Latest: v${esc(skill.latest_version)}</span>
      </div>
      <div class="meta">${(skill.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
      <div class="meta" style="margin-top:12px">
        <label>Version:
          <select id="version-select">${versions}</select>
        </label>
      </div>
      ${deps}
      <div class="card-actions" style="margin-top:16px">
        ${installed
          ? `<button data-action="uninstall" data-name="${esc(skill.name)}">Uninstall</button>
             <button data-action="update" data-name="${esc(skill.name)}">Update</button>`
          : `<button data-action="install" data-name="${esc(skill.name)}">Install</button>`}
      </div>
      <button data-action="view-skillmd" data-name="${esc(skill.name)}" style="margin-top:8px">View SKILL.md</button>
      <pre id="skillmd" style="display:none"></pre>
    </div>`;
}

function renderBrowse() {
  const filtered = state.skills.filter((s) => {
    const q = state.query.toLowerCase();
    const inQuery = !q || s.name.toLowerCase().includes(q)
      || s.description.toLowerCase().includes(q)
      || s.author.toLowerCase().includes(q)
      || (s.tags || []).some((t) => t.toLowerCase().includes(q));
    const inTag = !state.tag || (s.tags || []).includes(state.tag);
    return inQuery && inTag;
  });
  document.getElementById("view").innerHTML = `
    <div class="controls">
      <input type="search" id="search" placeholder="Search skills..." value="${esc(state.query)}" />
      <input type="search" id="tag-search" placeholder="Filter by tag" value="${esc(state.tag)}" style="max-width:180px" />
    </div>
    <div class="grid">${filtered.map(card).join("") || "<p style='color:var(--muted)'>No skills found.</p>"}</div>`;
  document.getElementById("search").addEventListener("input", (e) => {
    state.query = e.target.value;
    renderBrowse();
  });
  document.getElementById("tag-search").addEventListener("input", (e) => {
    state.tag = e.target.value.trim();
    renderBrowse();
  });
}

async function renderInstalled() {
  const installed = await api("/installed");
  document.getElementById("view").innerHTML = `
    <h2>Installed Skills</h2>
    <button id="update-all" style="margin-bottom:16px">Update all</button>
    <div class="grid">${installed.map(card).join("") || "<p style='color:var(--muted)'>Nothing installed yet.</p>"}</div>`;
  document.getElementById("update-all").addEventListener("click", async () => {
    try {
      const result = await api("/update-all", { method: "POST" });
      toast(`Updated ${result.updated} skill(s)`);
      await route();
    } catch (error) {
      toast(`Error: ${error.message}`);
    }
  });
}

async function renderDetail(name) {
  const detail = await api(`/skill/${encodeURIComponent(name)}`);
  document.getElementById("view").innerHTML = detailView(detail);
  document.getElementById("skillmd");
}

function handleAction(target) {
  const { action, name } = target.dataset;
  if (!action) return;
  const run = async () => {
    try {
      if (action === "install") await api(`/install`, { method: "POST", body: JSON.stringify({ name }), headers: { "Content-Type": "application/json" } });
      if (action === "uninstall") await api(`/uninstall`, { method: "POST", body: JSON.stringify({ name }), headers: { "Content-Type": "application/json" } });
      if (action === "update") await api(`/update`, { method: "POST", body: JSON.stringify({ name }), headers: { "Content-Type": "application/json" } });
      toast(`${action} ${name} succeeded`);
      await route();
    } catch (error) {
      toast(`Error: ${error.message}`);
    }
  };
  run();
}

async function route() {
  const hash = window.location.hash || "#/";
  document.querySelectorAll("nav a").forEach((a) => a.classList.remove("active"));

  if (hash.startsWith("#/skill/")) {
    document.getElementById("nav-browse").classList.add("active");
    const name = decodeURIComponent(hash.slice("#/skill/".length));
    await renderDetail(name);
    return;
  }
  if (hash.startsWith("#/installed")) {
    document.getElementById("nav-installed").classList.add("active");
    await renderInstalled();
    return;
  }
  document.getElementById("nav-browse").classList.add("active");
  renderBrowse();
}

document.addEventListener("click", (event) => {
  const actionTarget = event.target.closest("[data-action]");
  if (actionTarget) {
    handleAction(actionTarget);
    return;
  }
  const versionSelect = event.target.closest("#version-select");
  if (versionSelect) {
    // Version selection for future re-install support.
    return;
  }
  const skillmdButton = event.target.closest('[data-action="view-skillmd"]');
  if (skillmdButton) {
    const pre = document.getElementById("skillmd");
    if (pre) {
      if (pre.style.display === "none") {
        api(`/skill/${encodeURIComponent(skillmdButton.dataset.name)}/skillmd`)
          .then((d) => { pre.textContent = d.content; pre.style.display = "block"; });
      } else {
        pre.style.display = "none";
      }
    }
  }
});

async function boot() {
  try {
    state.skills = await api("/skills");
  } catch (error) {
    document.getElementById("view").innerHTML = `<p style="color:var(--red)">Failed to load skills: ${esc(error.message)}</p>`;
  }
  window.addEventListener("hashchange", route);
  route();
}

boot();