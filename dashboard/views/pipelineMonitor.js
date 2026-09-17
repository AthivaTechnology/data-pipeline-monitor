(function () {
  const AUTO_REFRESH_SECONDS = 60;
  let allPipelines = [];
  let lastSummary = {};
  let refreshTimer = null;
  let countdown = AUTO_REFRESH_SECONDS;

  // Minimal inline SVG icons (stroke uses currentColor, colored via CSS on
  // .card .icon) - no icon font/library, matching the project's
  // zero-dependency approach. One consistent circle-based visual language
  // for every status card; "Total" gets a distinct grid icon since it isn't
  // a status.
  const ICONS = {
    total: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
    fresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12l2.5 2.5L16 9"/></svg>',
    delayed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 3.2"/></svg>',
    failed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5l5 5M14.5 9.5l-5 5"/></svg>',
    stale: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20L12 3z"/><path d="M12 9.5v4"/><circle cx="12" cy="16.8" r="0.6" fill="currentColor" stroke="none"/></svg>',
    running: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M10 8.5l6 3.5-6 3.5v-7z" fill="currentColor" stroke="none"/></svg>',
    never_run: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
    unknown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v4.5"/><circle cx="12" cy="16" r="0.6" fill="currentColor" stroke="none"/></svg>',
  };

  // Defines every summary card: which pipelines it represents (a predicate
  // over the already-fetched data, or null for "all") and its display info.
  // Clicking a card sets the existing status-filter <select> to `statusValue`
  // and re-renders from data already in memory - no new fetch, no new filter
  // dimension, just reusing the filter machinery that already existed.
  const CARD_DEFS = [
    { key: "total", statusValue: "", label: "Total Pipelines", icon: ICONS.total },
    { key: "fresh", statusValue: "fresh", label: "Healthy / Fresh", icon: ICONS.fresh },
    { key: "delayed", statusValue: "delayed", label: "Delayed", icon: ICONS.delayed },
    { key: "failed", statusValue: "failed", label: "Failed", icon: ICONS.failed },
    { key: "stale", statusValue: "stale", label: "Stale", icon: ICONS.stale },
    { key: "running", statusValue: "running", label: "Running", icon: ICONS.running },
    { key: "never_run", statusValue: "never_run", label: "Never Run", icon: ICONS.never_run },
    { key: "unknown", statusValue: "unknown", label: "Configuration Issues", icon: ICONS.unknown },
  ];

  Views.pipelineMonitor = async function (container) {
    if (refreshTimer) clearInterval(refreshTimer);

    container.innerHTML = `
      ${Utils.pageHeader({
        title: "Pipeline Monitor",
        subtitle: "Monitor pipeline execution health and data freshness.",
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
      <div id="pm-active-filter"></div>
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

  // Clicking a card only ever touches the DOM filter controls and re-renders
  // from `allPipelines`/`lastSummary`, already sitting in memory from the
  // last fetch - it never triggers a new API call.
  function selectCard(statusValue) {
    const statusSelect = document.getElementById("pm-status-filter");
    if (statusSelect) statusSelect.value = statusValue;
    renderContent();
  }

  function clearFilters() {
    document.getElementById("pm-search").value = "";
    document.getElementById("pm-env-filter").value = "";
    document.getElementById("pm-status-filter").value = "";
    renderContent();
  }

  function renderContent() {
    renderSummary();
    renderActiveFilterChip();
    renderTable(filteredPipelines());
  }

  function renderSummary() {
    // Summary cards reflect the full registered set (matches the backend's
    // own computed summary), independent of the table's active filters -
    // filtering is a view of the table, not a re-scoping of "the truth".
    const currentStatus = getFilters().status;
    const cards = CARD_DEFS.map((def) => {
      const count = def.key === "total" ? allPipelines.length : (lastSummary[def.key] || 0);
      const isActive = currentStatus === def.statusValue;
      return `
        <div class="card c-${def.key}${isActive ? " active" : ""}" onclick="Views._pipelineMonitorSelectCard('${def.statusValue}')">
          <span class="icon">${def.icon}</span>
          <div class="card-body">
            <div class="n">${count}</div>
            <div class="l">${def.label}</div>
          </div>
        </div>`;
    });
    document.getElementById("pm-summary").innerHTML = cards.join("");
  }

  function renderActiveFilterChip() {
    const f = getFilters();
    const box = document.getElementById("pm-active-filter");
    const parts = [];
    if (f.status) {
      const def = CARD_DEFS.find((d) => d.statusValue === f.status);
      parts.push(`Status: ${def ? def.label : f.status}`);
    }
    if (f.env) parts.push(`Environment: ${f.env}`);
    if (f.search) parts.push(`Search: "${Utils.escapeHtml(f.search)}"`);

    if (parts.length === 0) {
      box.innerHTML = "";
      return;
    }
    box.innerHTML = `
      <div class="active-filter-chip">
        Showing: ${parts.join(" · ")}
        <button title="Clear filters" onclick="Views._pipelineMonitorClearFilters()">✕</button>
      </div>`;
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
      <tr class="clickable${p.execution_status === "failed" ? " row-failed" : ""}" onclick="location.hash='#/pipeline-monitor/${encodeURIComponent(p.pipeline_name)}'">
        <td>
          <div class="pname">${Utils.escapeHtml(p.pipeline_name)} ${p.review_status && p.review_status !== "confirmed" ? '<span class="badge badge-delayed">Pending Review</span>' : ""}</div>
          <div class="psub">${Utils.notAssigned(p.owner)}</div>
        </td>
        <td class="nowrap">${Utils.dash(p.environment)}</td>
        <td>${Utils.badge(p.execution_status, EXEC_META)}<div class="reason">${p.execution_reason || ""}</div></td>
        <td>${Utils.badge(p.data_status, DATA_META)}<div class="reason">${p.data_reason || ""}</div></td>
        <td class="nowrap">${Utils.fmtTime(p.last_successful_execution_at)}</td>
        <td class="nowrap">${Utils.dash(p.last_execution_status)}<div class="psub">${Utils.fmtTime(p.last_execution_at)}</div></td>
        <td class="nowrap">${Utils.fmtTime(p.expected_next_run)}</td>
        <td class="nowrap">${Utils.fmtDuration(p.last_execution_duration_seconds)}</td>
        <td class="nowrap">${p.alerting_enabled ? "Enabled" : "Disabled"}</td>
        <td class="nowrap">${Utils.fmtTime(p.last_checked_at)}</td>
        <td class="nowrap"><button class="view-btn" onclick="event.stopPropagation(); location.hash='#/pipeline-monitor/${encodeURIComponent(p.pipeline_name)}'">View</button></td>
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

  // Exposed on Views so inline onclick handlers (rendered as raw HTML
  // strings) can reach these without polluting window's global scope.
  Views._pipelineMonitorSelectCard = selectCard;
  Views._pipelineMonitorClearFilters = clearFilters;
})();
