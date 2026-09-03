const state = { sessions: [], selectedId: null, client: "all", query: "", activeTab: "result" };

const elements = {
  list: document.querySelector("#session-list"),
  count: document.querySelector("#session-count"),
  main: document.querySelector("#main"),
  search: document.querySelector("#search"),
  toast: document.querySelector("#toast"),
};

const escapeHtml = (value = "") => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const statusClass = (status) => status === "passed" ? "passed" : status === "active" ? "active" : "failed";
const prettyStatus = (status) => String(status || "unknown").replaceAll("_", " ");
const formatNumber = (value) => typeof value === "number" ? new Intl.NumberFormat().format(value) : "—";
const formatDuration = (seconds) => {
  if (typeof seconds !== "number") return "—";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
};
const formatDate = (epoch) => {
  if (typeof epoch !== "number") return "Unknown time";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(epoch * 1000));
};
const jsonText = (value) => {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  return JSON.stringify(value, null, 2);
};

function filteredSessions() {
  const query = state.query.trim().toLowerCase();
  return state.sessions.filter((session) => {
    const matchesClient = state.client === "all" || session.client === state.client;
    const searchable = `${session.id} ${session.problem_id} ${session.problem_title} ${session.model} ${session.status}`.toLowerCase();
    return matchesClient && (!query || searchable.includes(query));
  });
}

function renderList() {
  const sessions = filteredSessions();
  elements.count.textContent = `${sessions.length} session${sessions.length === 1 ? "" : "s"}`;
  elements.list.innerHTML = sessions.map((session) => `
    <button class="session-card ${session.id === state.selectedId ? "active" : ""}" data-session-id="${escapeHtml(session.id)}">
      <span class="agent-dot ${escapeHtml(session.client)}"></span>
      <span>
        <span class="session-name">${escapeHtml(session.problem_id)} · ${escapeHtml(session.problem_title)}</span>
        <span class="session-meta">${escapeHtml(formatDate(session.started_at_epoch))}</span>
      </span>
      <span class="mini-status ${statusClass(session.status)}">${escapeHtml(prettyStatus(session.status))}</span>
    </button>
  `).join("");

  elements.list.querySelectorAll("[data-session-id]").forEach((button) => {
    button.addEventListener("click", () => selectSession(button.dataset.sessionId));
  });
}

function eventMarkup(event, index) {
  const icon = event.kind === "message" ? "A" : event.kind === "error" ? "!" : event.kind === "result" ? "✓" : "↗";
  const text = event.text ? `<p class="event-text">${escapeHtml(event.text)}</p>` : "";
  const args = event.arguments !== undefined ? `
    <details><summary>Input</summary><pre class="json">${escapeHtml(jsonText(event.arguments))}</pre></details>` : "";
  const result = event.result !== undefined ? `
    <details ${event.kind === "result" ? "open" : ""}><summary>Output</summary><pre class="json">${escapeHtml(jsonText(event.result))}</pre></details>` : "";
  return `
    <article class="event ${escapeHtml(event.kind)}">
      <span class="event-icon" aria-hidden="true">${icon}</span>
      <div class="event-body">
        <div class="event-head">
          <span class="event-title">${escapeHtml(event.title)}</span>
          <span class="event-kind">${escapeHtml(event.status || event.kind)} · ${index + 1}</span>
        </div>
        ${text}${args}${result}
      </div>
    </article>`;
}

function renderDetail() {
  const session = state.sessions.find((item) => item.id === state.selectedId);
  if (!session) {
    elements.main.innerHTML = '<div class="empty-state">Select a session to see its result and trace.</div>';
    return;
  }
  const usage = session.usage || {};
  const inputTokens = usage.input_tokens;
  const outputTokens = usage.output_tokens;
  const attempts = session.final_attempts_used == null ? "—" : `${session.final_attempts_used} / ${session.max_final_attempts ?? "—"}`;
  const result = session.result
    ? `<div class="result-card"><pre id="result-text">${escapeHtml(session.result)}</pre></div>`
    : '<div class="no-content">The agent did not return a final message.</div>';
  const artifacts = session.artifacts.length
    ? session.artifacts.map((artifact, index) => `
      <article class="artifact">
        <div class="artifact-name"><span>${escapeHtml(artifact.path)}</span><button class="copy-button" data-copy-artifact="${index}">Copy</button></div>
        <pre>${escapeHtml(artifact.content)}</pre>
      </article>`).join("")
    : '<div class="no-content">This session has no artifact files.</div>';
  const timeline = session.events.length
    ? session.events.map(eventMarkup).join("")
    : '<div class="no-content">This session has no readable trace events.</div>';
  const history = session.submission_history || [];
  const historyMarkup = history.length
    ? history.map((version) => `
      <article class="version-card">
        <header class="version-head">
          <div>
            <span class="version-number">Version ${version.version}</span>
            <span class="version-source">${escapeHtml(version.source)}${version.event_number ? ` · Trace event ${version.event_number}` : ""}</span>
          </div>
          <div class="version-actions">
            <span>${formatNumber(version.bytes)} bytes</span>
            <button class="copy-button" data-copy-version="${version.version - 1}">Copy</button>
          </div>
        </header>
        <pre class="version-code"><code>${escapeHtml(version.content)}</code></pre>
      </article>`).join("")
    : '<div class="no-content">The trace has no recorded submission.bf versions.</div>';

  elements.main.innerHTML = `
    <div class="detail">
      <header class="detail-head">
        <div>
          <div class="detail-kicker">
            <span class="client-pill ${escapeHtml(session.client)}">${escapeHtml(session.client)}</span>
            <span>${escapeHtml(session.problem_id)}</span>
            <span>·</span>
            <span>${escapeHtml(formatDate(session.started_at_epoch))}</span>
          </div>
          <h2>${escapeHtml(session.problem_title)}</h2>
          <p class="problem-description">${escapeHtml(session.problem_description)}</p>
        </div>
        <button class="refresh" id="refresh" aria-label="Refresh session data">
          <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 7v5h-5"></path><path d="M4 17v-5h5"></path><path d="M6.1 8A7 7 0 0 1 18 6l2 2"></path><path d="M17.9 16A7 7 0 0 1 6 18l-2-2"></path></svg>
          Refresh
        </button>
      </header>

      <dl class="metrics">
        <div class="metric"><dt>Status</dt><dd class="status-text ${statusClass(session.status)}">${escapeHtml(prettyStatus(session.status))}</dd></div>
        <div class="metric"><dt>Duration</dt><dd>${escapeHtml(formatDuration(session.duration_seconds))}</dd></div>
        <div class="metric"><dt>Final attempts</dt><dd>${escapeHtml(attempts)}</dd></div>
        <div class="metric"><dt>Model</dt><dd title="${escapeHtml(session.model)}">${escapeHtml(session.model)}</dd></div>
      </dl>

      <div class="tabs" role="tablist" aria-label="Session details">
        <button class="tab ${state.activeTab === "result" ? "active" : ""}" role="tab" aria-selected="${state.activeTab === "result"}" data-tab="result">Result</button>
        <button class="tab ${state.activeTab === "trace" ? "active" : ""}" role="tab" aria-selected="${state.activeTab === "trace"}" data-tab="trace">Agent trace <span>${session.event_count}</span></button>
        <button class="tab ${state.activeTab === "history" ? "active" : ""}" role="tab" aria-selected="${state.activeTab === "history"}" data-tab="history">submission.bf history <span>${history.length}</span></button>
      </div>

      <section class="tab-panel ${state.activeTab === "result" ? "active" : ""}" data-panel="result">
        <div class="content-grid">
          <div class="stack">
            <section class="section">
              <div class="section-title"><h3>Final result</h3>${session.result ? '<button class="copy-button" data-copy-result>Copy</button>' : ""}</div>
              ${result}
            </section>
          </div>
          <aside class="stack">
            <section class="section">
              <div class="section-title"><h3>Artifacts</h3><span class="count">${session.artifacts.length} files</span></div>
              ${artifacts}
            </section>
            <section class="section">
              <div class="section-title"><h3>Token use</h3></div>
              <dl class="metrics" style="grid-template-columns: 1fr 1fr; margin: 0">
                <div class="metric"><dt>Input</dt><dd>${formatNumber(inputTokens)}</dd></div>
                <div class="metric"><dt>Output</dt><dd>${formatNumber(outputTokens)}</dd></div>
              </dl>
            </section>
          </aside>
        </div>
      </section>

      <section class="tab-panel ${state.activeTab === "trace" ? "active" : ""}" data-panel="trace">
        <div class="section-title"><h3>Agent trace</h3><span class="count">${session.event_count} events</span></div>
        <div class="timeline">${timeline}</div>
      </section>

      <section class="tab-panel ${state.activeTab === "history" ? "active" : ""}" data-panel="history">
        <div class="section-title"><h3>submission.bf chronology</h3><span class="count">${history.length} versions</span></div>
        <div class="version-list">${historyMarkup}</div>
      </section>
    </div>`;

  document.querySelector("#refresh").addEventListener("click", loadSessions);
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      renderDetail();
    });
  });
  document.querySelector("[data-copy-result]")?.addEventListener("click", () => copyText(session.result));
  document.querySelectorAll("[data-copy-artifact]").forEach((button) => {
    button.addEventListener("click", () => copyText(session.artifacts[Number(button.dataset.copyArtifact)].content));
  });
  document.querySelectorAll("[data-copy-version]").forEach((button) => {
    button.addEventListener("click", () => copyText(history[Number(button.dataset.copyVersion)].content));
  });
}

function selectSession(id) {
  state.selectedId = id;
  history.replaceState(null, "", `#${encodeURIComponent(id)}`);
  renderList();
  renderDetail();
  if (window.innerWidth <= 660) elements.main.scrollIntoView({ behavior: "smooth" });
}

async function copyText(value) {
  await navigator.clipboard.writeText(value);
  elements.toast.textContent = "Copied to clipboard.";
  elements.toast.classList.add("show");
  window.setTimeout(() => elements.toast.classList.remove("show"), 1600);
}

async function loadSessions() {
  try {
    const response = await fetch("/api/sessions", { cache: "no-store" });
    if (!response.ok) throw new Error(`Request failed with status ${response.status}.`);
    const data = await response.json();
    state.sessions = data.sessions || [];
    const hashId = decodeURIComponent(location.hash.slice(1));
    if (!state.sessions.some((session) => session.id === state.selectedId)) {
      state.selectedId = state.sessions.some((session) => session.id === hashId) ? hashId : state.sessions[0]?.id;
    }
    renderList();
    renderDetail();
  } catch (error) {
    elements.main.innerHTML = `<div class="empty-state">Could not read session data. ${escapeHtml(error.message)}</div>`;
  }
}

elements.search.addEventListener("input", (event) => { state.query = event.target.value; renderList(); });
document.querySelectorAll("[data-client]").forEach((button) => {
  button.addEventListener("click", () => {
    state.client = button.dataset.client;
    document.querySelectorAll("[data-client]").forEach((item) => item.classList.toggle("active", item === button));
    renderList();
  });
});

loadSessions();
