(function () {
  const AUTO_REFRESH_SECONDS = 60;
  let allPipelines = [];
  let lastSummary = {};
  let refreshTimer = null;
  let countdown = AUTO_REFRESH_SECONDS;

  Views.pipelineMonitor = async function (container) {
    if (refreshTimer) clearInterval(refreshTimer);

    container.innerHTML = `
      ${Utils.pageHeader({
        title: "Pipeline Monitor",
        subtitle: "Execution health and data freshness for every registered pipeline.",
        metaHtml: `
          <div class="pill-row">
            <span class="auto-refresh" id="pm-auto-refresh"></span>
            <button class="btn btn-primary" id="pm-refresh-btn">Refresh</button>
          </div>
          <div class="last-updated" id="pm-last-updated"></div>`,
      })}

      <div class="controls-row">
        <input type="text" id="pm-search" placeholder="Search pipeline name…" />
        <select id="pm-env-filter"><option value="">All environments</option></select>
        <select id="pm-status-filter">
          <option value="">All statuses</option>
          ${Object.entries(EXEC_META).map(([k, m]) => `<option value="${k}">${m.label}</option>`).join("")}
        </select>
        <span class="spacer"></span>
      </div>

      <div id="pm-summary" class="summary"></div>
      <div id="pm-table-wrap" class="table-wrap">${Utils.loadingState("Loading pipelines…")}</div>
    `;

    document.getElementById("pm-refresh-btn").addEventListener("click", () => loadData(true));
    document.getElementById("pm-search").addEventListener("input", renderContent);
    document.getElementById("pm-env-filter").addEventListener("change", renderContent);
    document.getElementById("pm-status-filter").addEventListener("change", renderContent);

    await loadData(false);

    refreshTimer = setInterval(() => {
      if (!/^#\/pipeline-monitor\/?$/.test(location.hash)) {
        clearInterval(refreshTimer);
        return;
      }
      countdown -= 1;
      if (countdown <= 0) loadData(false);
      const el = document.getElementById("pm-auto-refresh");
      if (el) el.textContent = `auto-refreshing in ${Math.max(countdown, 0)}s`;
    }, 1000);
  };

  async function loadData(isManual) {
    const btn = document.getElementById("pm-refresh-btn");
    if (btn && isManual) { btn.disabled = true; btn.textContent = "Refreshing…"; }
    try {
      const data = await Api.fetchStatus();
      allPipelines = data.pipelines;
      lastSummary = data.summary;
      populateEnvFilter();
      renderContent();
      const lu = document.getElementById("pm-last-updated");
      if (lu) lu.textContent = `Last updated: ${new Date().toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" })} (your local time)`;
      countdown = AUTO_REFRESH_SECONDS;
    } catch (e) {
      const wrap = document.getElementById("pm-table-wrap");
      if (wrap) wrap.innerHTML = Utils.errorState(e.message);
    } finally {
      if (btn && isManual) { btn.disabled = false; btn.textContent = "Refresh"; }
    }
  }

  function populateEnvFilter() {
    const select = document.getElementById("pm-env-filter");
    if (!select) return;
    const current = select.value;
    const envs = Array.from(new Set(allPipelines.map((p) => p.environment).filter(Boolean))).sort();
    select.innerHTML = `<option value="">All environments</option>` + envs.map((e) => `<option value="${e}">${e}</option>`).join("");
    select.value = envs.includes(current) ? current : "";
  }

  function getFilters() {
    return {
      search: (document.getElementById("pm-search")?.value || "").trim().toLowerCase(),
      env: document.getElementById("pm-env-filter")?.value || "",
      status: document.getElementById("pm-status-filter")?.value || "",
    };
  }

  function filteredPipelines() {
    const f = getFilters();
    return allPipelines.filter((p) => {
      if (f.search && !p.pipeline_name.toLowerCase().includes(f.search)) return false;
      if (f.env && p.environment !== f.env) return false;
      if (f.status && p.execution_status !== f.status) return false;
      return true;
    });
  }

  function renderContent() {
    renderSummary();
    renderTable(filteredPipelines());
  }

  function renderSummary() {
    // Summary cards reflect the full registered set (matches the backend's
    // own computed summary), independent of the table's active filters -
    // filtering is a view of the table, not a re-scoping of "the truth".
    const cards = [`<div class="card c-total"><div class="n">${allPipelines.length}</div><div class="l">Total Pipelines</div></div>`];
    for (const key of SUMMARY_ORDER) {
      cards.push(`<div class="card c-${key}"><div class="n">${lastSummary[key] || 0}</div><div class="l">${SUMMARY_LABELS[key]}</div></div>`);
    }
    document.getElementById("pm-summary").innerHTML = cards.join("");
  }

  function renderTable(pipelines) {
    const wrap = document.getElementById("pm-table-wrap");
    if (allPipelines.length === 0) {
      wrap.innerHTML = Utils.emptyState("No pipelines registered", "Add a pipeline to config/registry.yaml to start monitoring it.");
      return;
    }
    if (pipelines.length === 0) {
      wrap.innerHTML = Utils.emptyState("No pipelines match your filters", "Try clearing the search box or filters above.");
      return;
    }

    const rows = pipelines.map((p) => `
      <tr class="clickable" onclick="location.hash='#/pipeline-monitor/${encodeURIComponent(p.pipeline_name)}'">
        <td>
          <div class="pname">${Utils.escapeHtml(p.pipeline_name)}</div>
          <div class="psub">${Utils.dash(p.owner)}</div>
        </td>
        <td>${Utils.dash(p.environment)}</td>
        <td>${Utils.badge(p.execution_status, EXEC_META)}<div class="reason">${p.execution_reason || ""}</div></td>
        <td>${Utils.badge(p.data_status, DATA_META)}<div class="reason">${p.data_reason || ""}</div></td>
        <td>${Utils.fmtTime(p.last_successful_execution_at)}</td>
        <td>${Utils.dash(p.last_execution_status)}<div class="psub">${Utils.fmtTime(p.last_execution_at)}</div></td>
        <td>${Utils.fmtTime(p.expected_next_run)}</td>
        <td>${Utils.fmtDuration(p.last_execution_duration_seconds)}</td>
        <td>${p.alerting_enabled ? "Enabled" : "Disabled"}</td>
        <td>${Utils.fmtTime(p.last_checked_at)}</td>
        <td><button class="view-btn" onclick="event.stopPropagation(); location.hash='#/pipeline-monitor/${encodeURIComponent(p.pipeline_name)}'">View</button></td>
      </tr>
    `);

    wrap.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Pipeline</th><th>Env</th><th>Execution Health</th><th>Data Freshness</th>
            <th>Last Successful Run</th><th>Last Execution</th><th>Expected Next Run</th>
            <th>Duration</th><th>Alerting</th><th>Last Checked</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>${rows.join("")}</tbody>
      </table>`;
  }
})();
