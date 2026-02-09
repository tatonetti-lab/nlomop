// ── Element references ──
const messagesEl = document.getElementById("messages");
const form = document.getElementById("input-form");
const questionEl = document.getElementById("question");
const sendBtn = document.getElementById("send-btn");
const sqlEditor = document.getElementById("sql-editor");
const sqlRunBtn = document.getElementById("sql-run-btn");
const sqlResults = document.getElementById("sql-results");
const sqlMeta = document.getElementById("sql-meta");
const modelBadge = document.getElementById("model-badge");
const settingsBtn = document.getElementById("settings-btn");
const settingsOverlay = document.getElementById("settings-overlay");
const settingsClose = document.getElementById("settings-close");
const modelSelect = document.getElementById("model-select");
const customModelInput = document.getElementById("custom-model");
const customModelBtn = document.getElementById("custom-model-btn");
const helpBtn = document.getElementById("help-btn");

// ── Help ──
const _helpHtml = `
    <div class="help-content">
      <h3>What can you ask?</h3>
      <p>Ask clinical questions about <strong>11,463 synthetic patients</strong> in an OMOP CDM database. Questions are translated to SQL or routed to built-in statistical analyses.</p>

      <div class="help-section">
        <h4>SQL Queries</h4>
        <p>Any question answerable with a database query:</p>
        <ul>
          <li>Patient counts &amp; prevalence &mdash; <em>"How many patients have diabetes?"</em></li>
          <li>Distributions &amp; averages &mdash; <em>"What is the average BMI?"</em></li>
          <li>Drug prescribing patterns &mdash; <em>"Most common drug after a diabetes diagnosis?"</em></li>
          <li>Temporal questions &mdash; <em>"Average time between diabetes diagnosis and first metformin prescription?"</em></li>
          <li>Cohort building &mdash; <em>"Patients with diabetes, HbA1c &gt; 6.5%, and on metformin"</em></li>
          <li>Demographics, procedures, visits, costs</li>
        </ul>
      </div>

      <div class="help-section">
        <h4>Statistical Analyses</h4>
        <p>These run multiple queries and compute statistics automatically:</p>
        <table>
          <thead><tr><th>Analysis</th><th>Example question</th></tr></thead>
          <tbody>
            <tr><td><strong>Survival</strong><br><span class="help-detail">Kaplan-Meier curves</span></td><td><em>"What is the 5-year survival of patients with type 2 diabetes?"</em></td></tr>
            <tr><td><strong>Pre/Post</strong><br><span class="help-detail">Paired t-test</span></td><td><em>"What is the effect of statins on total cholesterol within 30 days?"</em></td></tr>
            <tr><td><strong>Comparative</strong><br><span class="help-detail">Two-group comparison</span></td><td><em>"Compare ACE inhibitors vs ARBs for blood pressure outcomes"</em></td></tr>
            <tr><td><strong>Odds Ratio</strong><br><span class="help-detail">2&times;2 contingency table</span></td><td><em>"What is the odds ratio of chronic kidney disease given diabetes?"</em></td></tr>
            <tr><td><strong>Correlation</strong><br><span class="help-detail">Pearson &amp; Spearman</span></td><td><em>"Is there a correlation between BMI and systolic blood pressure?"</em></td></tr>
          </tbody>
        </table>
      </div>

      <p class="help-hint">Tip: Use the test questions in the sidebar to try examples, or type your own question below.</p>
    </div>
`;

function showHelp() {
  // Toggle: if help card exists and is visible, remove it
  const existing = document.getElementById("help-card");
  if (existing) {
    existing.remove();
    return;
  }

  const div = document.createElement("div");
  div.className = "msg assistant";
  div.id = "help-card";
  div.innerHTML = _helpHtml;
  messagesEl.prepend(div);
  document.getElementById("chat-scroll").scrollTop = 0;
}

helpBtn.addEventListener("click", showHelp);

// Show help on first load
showHelp();

// ── Settings ──
settingsBtn.addEventListener("click", () => {
  settingsOverlay.classList.remove("hidden");
});

settingsClose.addEventListener("click", () => {
  settingsOverlay.classList.add("hidden");
});

settingsOverlay.addEventListener("click", (e) => {
  if (e.target === settingsOverlay) settingsOverlay.classList.add("hidden");
});

modelSelect.addEventListener("change", () => {
  if (modelSelect.value) setModel(modelSelect.value);
});

customModelBtn.addEventListener("click", () => {
  const v = customModelInput.value.trim();
  if (v) setModel(v);
});

customModelInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const v = customModelInput.value.trim();
    if (v) setModel(v);
  }
});

async function setModel(name) {
  try {
    const resp = await fetch("/api/settings/model", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: name }),
    });
    const data = await resp.json();
    modelBadge.textContent = data.current_model;
    modelSelect.value = data.current_model;
    customModelInput.value = "";
    settingsOverlay.classList.add("hidden");
  } catch (err) {
    // ignore
  }
}

async function loadSettings() {
  try {
    const resp = await fetch("/api/settings");
    const data = await resp.json();
    modelBadge.textContent = data.current_model;

    // Populate select
    modelSelect.innerHTML = "";
    for (const m of data.available_models) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      if (m === data.current_model) opt.selected = true;
      modelSelect.appendChild(opt);
    }
    // If current model is not in the list, add it
    if (!data.available_models.includes(data.current_model)) {
      const opt = document.createElement("option");
      opt.value = data.current_model;
      opt.textContent = data.current_model + " (current)";
      opt.selected = true;
      modelSelect.prepend(opt);
    }
  } catch (err) {
    // ignore
  }
}

loadSettings();

// ── Tab switching ──
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(tab.dataset.tab).classList.add("active");
  });
});

// ── Sidebar: clicking a test question populates the chat input ──
document.querySelectorAll(".sample-q").forEach((btn) => {
  btn.addEventListener("click", () => {
    // Switch to chat view if not already there
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    document.querySelector('[data-tab="chat-view"]').classList.add("active");
    document.getElementById("chat-view").classList.add("active");

    questionEl.value = btn.dataset.q;
    questionEl.focus();
  });
});

// ── Chat: submit question ──
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = questionEl.value.trim();
  if (!q) return;

  addMessage(q, "user");
  questionEl.value = "";
  sendBtn.disabled = true;

  const loadingEl = addMessage("Thinking", "assistant");
  loadingEl.querySelector(".explanation").classList.add("loading-dots");

  try {
    const resp = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    const data = await resp.json();
    loadingEl.remove();
    renderResult(data);
  } catch (err) {
    loadingEl.remove();
    addMessage("Network error: " + err.message, "assistant error");
  } finally {
    sendBtn.disabled = false;
    questionEl.focus();
  }
});

function addMessage(text, cls) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  if (cls.includes("user")) {
    div.textContent = text;
  } else {
    div.innerHTML = '<div class="explanation">' + escapeHtml(text) + "</div>";
  }
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function renderResult(data) {
  const div = document.createElement("div");
  div.className = "msg assistant" + (data.error ? " error" : "");

  let html = "";

  if (data.error) {
    html += '<div class="explanation">' + escapeHtml(data.error) + "</div>";
  }

  if (data.explanation) {
    html += '<div class="explanation">' + escapeHtml(data.explanation) + "</div>";
  }

  // SQL in collapsible + "Run in IDE" button
  if (data.sql) {
    html += "<details><summary>SQL query";
    html += ' <button class="copy-sql-btn" data-sql="' + escapeAttr(data.sql) + '">Run in IDE</button>';
    html += "</summary><pre>" + escapeHtml(data.sql) + "</pre></details>";
  }

  // Analysis result rendering
  if (data.analysis_result) {
    html += renderAnalysisResult(data.analysis_result);
  }

  // Analysis sub-queries in collapsible block
  if (data.analysis_queries && data.analysis_queries.length > 0) {
    html += "<details><summary>Analysis queries (" + data.analysis_queries.length + ")</summary>";
    for (const q of data.analysis_queries) {
      html += "<pre>" + escapeHtml(q) + "</pre>";
    }
    html += "</details>";
  }

  if (data.rows && data.rows.length > 0) {
    if (data.columns.length === 1 && data.rows.length === 1) {
      html +=
        '<div class="explanation"><strong>' +
        escapeHtml(data.columns[0]) +
        ": " +
        escapeHtml(String(data.rows[0][0])) +
        "</strong></div>";
    } else {
      html += buildTable(data.columns, data.rows);
    }
  }

  const metaParts = [];
  if (data.model) metaParts.push(escapeHtml(data.model));
  if (data.elapsed_s) metaParts.push(data.elapsed_s + "s");
  if (data.row_count > 0)
    metaParts.push(data.row_count + " row" + (data.row_count > 1 ? "s" : ""));
  if (data.concepts_used && data.concepts_used.length > 0) {
    const ctext = data.concepts_used
      .map((c) => c.name + " (" + c.id + ")")
      .join(", ");
    metaParts.push(
      '<span class="concepts">Concepts: ' + escapeHtml(ctext) + "</span>"
    );
  }
  if (metaParts.length) {
    html += '<div class="meta">' + metaParts.join(" &middot; ") + "</div>";
  }

  div.innerHTML = html;

  // Wire up "Run in IDE" button
  const copyBtn = div.querySelector(".copy-sql-btn");
  if (copyBtn) {
    copyBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sql = copyBtn.dataset.sql;
      sqlEditor.value = sql;
      // Switch to SQL IDE tab
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      document.querySelector('[data-tab="sql-view"]').classList.add("active");
      document.getElementById("sql-view").classList.add("active");
      sqlEditor.focus();
    });
  }

  messagesEl.appendChild(div);
  scrollToBottom();
}

// ── SQL IDE ──
sqlRunBtn.addEventListener("click", runSql);
sqlEditor.addEventListener("keydown", (e) => {
  // Cmd/Ctrl+Enter to run
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    runSql();
  }
  // Tab inserts spaces
  if (e.key === "Tab") {
    e.preventDefault();
    const start = sqlEditor.selectionStart;
    const end = sqlEditor.selectionEnd;
    sqlEditor.value =
      sqlEditor.value.substring(0, start) +
      "  " +
      sqlEditor.value.substring(end);
    sqlEditor.selectionStart = sqlEditor.selectionEnd = start + 2;
  }
});

async function runSql() {
  const sql = sqlEditor.value.trim();
  if (!sql) return;

  sqlRunBtn.disabled = true;
  sqlMeta.textContent = "Running...";
  sqlResults.innerHTML = "";

  try {
    const resp = await fetch("/api/sql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql: sql }),
    });
    const data = await resp.json();

    if (data.error) {
      sqlResults.innerHTML = '<div class="sql-error">' + escapeHtml(data.error) + "</div>";
      sqlMeta.textContent = data.elapsed_s ? data.elapsed_s + "s" : "";
    } else if (!data.rows || data.rows.length === 0) {
      sqlResults.innerHTML = '<div class="sql-empty">Query returned 0 rows</div>';
      sqlMeta.textContent = data.elapsed_s ? data.elapsed_s + "s · 0 rows" : "0 rows";
    } else {
      sqlResults.innerHTML = buildTable(data.columns, data.rows);
      sqlMeta.textContent =
        (data.elapsed_s ? data.elapsed_s + "s · " : "") +
        data.row_count +
        " row" +
        (data.row_count > 1 ? "s" : "");
    }
  } catch (err) {
    sqlResults.innerHTML =
      '<div class="sql-error">Network error: ' + escapeHtml(err.message) + "</div>";
    sqlMeta.textContent = "";
  } finally {
    sqlRunBtn.disabled = false;
  }
}

// ── Analysis result rendering ──
function renderAnalysisResult(result) {
  let html = '<div class="analysis-card">';

  // Title
  const typeLabels = {
    survival: "Kaplan-Meier Survival Analysis",
    pre_post: "Pre/Post Treatment Comparison",
    comparative: "Comparative Effectiveness",
    odds_ratio: "Odds Ratio Analysis",
    correlation: "Correlation Analysis",
  };
  html +=
    "<h4>" +
    escapeHtml(typeLabels[result.analysis_type] || result.analysis_type) +
    "</h4>";

  // Warnings
  if (result.warnings && result.warnings.length > 0) {
    for (const w of result.warnings) {
      html += '<div class="analysis-warning">' + escapeHtml(w) + "</div>";
    }
  }

  // Summary stats
  if (result.summary) {
    html += '<div class="analysis-stats">';
    for (const [key, value] of Object.entries(result.summary)) {
      if (value === null || value === undefined) continue;
      const label = formatStatLabel(key);
      const displayVal = formatStatValue(key, value);
      const sigClass =
        key === "p_value" && typeof value === "number" && value < 0.05
          ? " significant"
          : "";
      html += '<div class="analysis-stat">';
      html += '<div class="stat-label">' + escapeHtml(label) + "</div>";
      html +=
        '<div class="stat-value' +
        sigClass +
        '">' +
        escapeHtml(displayVal) +
        "</div>";
      html += "</div>";
    }
    html += "</div>";
  }

  // Detail table
  if (
    result.detail_columns &&
    result.detail_rows &&
    result.detail_rows.length > 0
  ) {
    html += buildTable(result.detail_columns, result.detail_rows);
  }

  html += "</div>";
  return html;
}

function formatStatLabel(key) {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatStatValue(key, value) {
  if (typeof value === "number") {
    if (key === "p_value" || key === "pearson_p" || key === "spearman_p") {
      return value < 0.001 ? "< 0.001" : value.toFixed(4);
    }
    if (
      key.includes("rate") ||
      key.includes("survival") ||
      key.includes("_r") ||
      key === "pearson_r" ||
      key === "spearman_r" ||
      key === "cohens_d"
    ) {
      return value.toFixed(3);
    }
    if (Number.isInteger(value)) return value.toLocaleString();
    return value.toFixed(2);
  }
  return String(value);
}

// ── Shared helpers ──
function buildTable(columns, rows) {
  let html = "<table><thead><tr>";
  for (const col of columns) {
    html += "<th>" + escapeHtml(col) + "</th>";
  }
  html += "</tr></thead><tbody>";
  for (const row of rows) {
    html += "<tr>";
    for (const val of row) {
      html += "<td>" + escapeHtml(val == null ? "" : String(val)) + "</td>";
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  return html;
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function escapeAttr(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function scrollToBottom() {
  const el = document.getElementById("chat-scroll");
  el.scrollTop = el.scrollHeight;
}
