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
    needs_review: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
    needs_attention: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><circle cx="12" cy="16.3" r="0.6" fill="currentColor" stroke="none"/><path d="M10.3 3.9L2.5 18a1.6 1.6 0 0 0 1.4 2.4h16.2a1.6 1.6 0 0 0 1.4-2.4L13.7 3.9a1.6 1.6 0 0 0-2.8 0z"/></svg>',
  };

  // Data-freshness statuses (see resource_scanner.py / data_freshness.py) that
  // count toward "Needs Attention" alongside the execution-health ones -
  // deliberately a separate list from _ATTENTION_EXECUTION_STATUSES below
  // since these two enums are independent axes.
  const _ATTENTION_EXECUTION_STATUSES = new Set(["failed", "delayed", "stale"]);
  const _ATTENTION_DATA_STATUSES = new Set(["stale", "delayed", "source_detected_unavailable"]);

  // Hex values matching the CSS custom properties in style.css, needed here
  // because the donut chart's conic-gradient is built as an inline style
  // string (CSS var() works fine in most contexts but conic-gradient stop
  // lists are simplest to compute in JS with concrete colors).
  const STATUS_HEX = {
    fresh: "#1a7f37", delayed: "#9a6700", failed: "#cf222e", stale: "#7c3aed",
    running: "#0969da", never_run: "#57606a", unknown: "#bc4c00",
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
    { key: "needs_review", statusValue: "__needs_review__", label: "Needs Review", icon: ICONS.needs_review },
    { key: "needs_attention", statusValue: "__needs_attention__", label: "Needs Attention", icon: ICONS.needs_attention },
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

      <div id="pm-summary" class="summary"></div>

      <div class="pm-layout">
        <div class="pm-main">
          <div class="controls-row">
            <input type="text" id="pm-search" placeholder="Search pipeline name…" />
            <select id="pm-env-filter"><option value="">All environments</option></select>
            <select id="pm-status-filter">
              <option value="">All statuses</option>
              ${Object.entries(EXEC_META).map(([k, m]) => `<option value="${k}">${m.label}</option>`).join("")}
              <option value="__needs_review__">Needs Review</option>
              <option value="__needs_attention__">Needs Attention</option>
            </select>
            <span class="spacer"></span>
          </div>

          <div id="pm-active-filter"></div>
          <div id="pm-table-wrap" class="table-wrap">${Utils.loadingState("Loading pipelines…")}</div>
          <p class="footer-note">Click on any status card above to filter pipelines. Use the search and filters to refine your view.</p>
        </div>

        <div class="pm-side">
          <div class="side-panel">
            <h3>Pipeline Status Overview</h3>
            <div id="pm-donut"></div>
          </div>
          <div class="side-panel">
            <h3>Recent Activity <a href="javascript:void(0)" onclick="Views._pipelineMonitorClearFilters()">View All</a></h3>
            <div id="pm-activity"></div>
          </div>
          <div class="side-panel">
            <h3>Quick Insights</h3>
            <div id="pm-insights"></div>
          </div>
        </div>
      </div>
    `;

    document.getElementById("pm-refresh-btn").addEventListener("click", () => loadData(true));
    document.getElementById("pm-search").addEventListener("input", renderContent);
    document.getElementById("pm-env-filter").addEventListener("change", renderContent);
    document.getElementById("pm-status-filter").addEventListener("change", renderContent);

    // Deep-link support: Home's stat links navigate to
    // #/pipeline-monitor?status=<key> to arrive here pre-filtered.
    const initialStatus = initialStatusFromHash();
    if (initialStatus) document.getElementById("pm-status-filter").value = initialStatus;

    await loadData(false);

    refreshTimer = setInterval(() => {
      if (!/^#\/pipeline-monitor\/?(?:\?.*)?$/.test(location.hash)) {
        clearInterval(refreshTimer);
        return;
      }
      countdown -= 1;
      if (countdown <= 0) loadData(false);
      const el = document.getElementById("pm-auto-refresh");
      if (el) el.textContent = `auto-refreshing in ${Math.max(countdown, 0)}s`;
    }, 1000);
  };

  function initialStatusFromHash() {
    const queryPart = location.hash.split("?")[1];
    if (!queryPart) return "";
    return new URLSearchParams(queryPart).get("status") || "";
  }

  async function loadData(isManual) {
    const btn = document.getElementById("pm-refresh-btn");
    if (btn && isManual) { btn.disabled = true; btn.textContent = "Refreshing…"; }
    try {
      const data = await Api.fetchStatus();
      allPipelines = data.pipelines;
      lastSummary = data.summary;
      populateEnvFilter();
      renderContent();
      renderSidePanels();
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

  // Sentinel for the env filter's "Not automatically detected" option -
  // distinct from "" (All environments), since a real environment value can
  // never equal this (environment is either a real AWS-derived string or
  // null, never this literal).
  const _ENV_NOT_DETECTED = "__env_not_detected__";

  function populateEnvFilter() {
    const select = document.getElementById("pm-env-filter");
    if (!select) return;
    const current = select.value;
    const envs = Array.from(new Set(allPipelines.map((p) => p.environment).filter(Boolean))).sort();
    const hasUndetected = allPipelines.some((p) => !p.environment);
    select.innerHTML = `<option value="">All environments</option>` +
      envs.map((e) => `<option value="${Utils.escapeHtml(e)}">${Utils.escapeHtml(e)}</option>`).join("") +
      (hasUndetected ? `<option value="${_ENV_NOT_DETECTED}">Not automatically detected</option>` : "");
    select.value = (envs.includes(current) || current === _ENV_NOT_DETECTED) ? current : "";
  }

  function getFilters() {
    return {
      search: (document.getElementById("pm-search")?.value || "").trim().toLowerCase(),
      env: document.getElementById("pm-env-filter")?.value || "",
      status: document.getElementById("pm-status-filter")?.value || "",
    };
  }

  function isNeedsAttention(p) {
    return _ATTENTION_EXECUTION_STATUSES.has(p.execution_status) || _ATTENTION_DATA_STATUSES.has(p.data_status);
  }

  function filteredPipelines() {
    const f = getFilters();
    return allPipelines.filter((p) => {
      if (f.search && !p.pipeline_name.toLowerCase().includes(f.search)) return false;
      if (f.env === _ENV_NOT_DETECTED && p.environment) return false;
      if (f.env && f.env !== _ENV_NOT_DETECTED && p.environment !== f.env) return false;
      if (f.status === "__needs_review__") return p.review_status === "needs_review";
      if (f.status === "__needs_attention__") return isNeedsAttention(p);
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
      let count;
      if (def.key === "total") count = allPipelines.length;
      else if (def.key === "needs_review") count = allPipelines.filter((p) => p.review_status === "needs_review").length;
      else if (def.key === "needs_attention") count = allPipelines.filter(isNeedsAttention).length;
      else count = lastSummary[def.key] || 0;
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
    if (f.env) parts.push(`Environment: ${f.env === _ENV_NOT_DETECTED ? "Not automatically detected" : Utils.escapeHtml(f.env)}`);
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
          <div class="pname">${Utils.escapeHtml(p.pipeline_name)} ${p.review_status === "needs_review" ? `<span class="badge badge-not_configured">${Utils.reviewStatusLabel(p)}</span>` : ""}</div>
          <div class="psub">${Utils.notAssigned(p.owner)}</div>
        </td>
        <td class="nowrap">${Utils.envBadge(p)}</td>
        <td>${Utils.badge(p.execution_status, EXEC_META)}<div class="reason" title="${Utils.escapeHtml(p.execution_reason || "")}">${p.execution_reason || ""}</div></td>
        <td>${Utils.badge(p.data_status, DATA_META)}<div class="reason" title="${Utils.escapeHtml(p.data_reason || "")}">${p.data_reason || ""}</div></td>
        <td class="nowrap">${Utils.fmtTime(p.last_successful_execution_at, "No successful run yet")}</td>
        <td class="nowrap">${Utils.nextRunLabel(p)}</td>
        <td class="nowrap">${Utils.fmtDuration(p.last_execution_duration_seconds, "Not available")}</td>
        <td class="nowrap">${Utils.escapeHtml(Utils.scheduleLabel(p))}</td>
        <td class="nowrap"><button class="view-btn" onclick="event.stopPropagation(); location.hash='#/pipeline-monitor/${encodeURIComponent(p.pipeline_name)}'">View</button></td>
      </tr>
    `);

    wrap.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Pipeline</th><th>Environment</th><th>Execution Health</th><th>Data Freshness</th>
            <th>Last Successful Run</th><th>Next Run</th><th>Duration</th><th>Trigger</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>${rows.join("")}</tbody>
      </table>`;
  }

  // ---------------- Sidebar: all derived from allPipelines/lastSummary,
  // already sitting in memory - no extra API calls, no invented numbers. ----

  function renderSidePanels() {
    renderDonut();
    renderActivity();
    renderInsights();
  }

  function renderDonut() {
    const el = document.getElementById("pm-donut");
    if (!el) return;
    const total = allPipelines.length;
    if (total === 0) {
      el.innerHTML = `<div class="muted" style="font-size:12.5px">No pipelines yet.</div>`;
      return;
    }

    let cursor = 0;
    const stops = [];
    const legend = [];
    for (const key of SUMMARY_ORDER) {
      const count = lastSummary[key] || 0;
      if (count === 0) continue;
      const pct = (count / total) * 100;
      const color = STATUS_HEX[key];
      stops.push(`${color} ${cursor}% ${cursor + pct}%`);
      legend.push(`
        <div class="li">
          <span class="dot" style="background:${color}"></span>
          <span class="li-label">${SUMMARY_LABELS[key]}</span>
          <span class="li-pct">${count} (${Math.round(pct)}%)</span>
        </div>`);
      cursor += pct;
    }
    const gradient = stops.length ? stops.join(", ") : "var(--neutral-bg) 0 100%";

    el.innerHTML = `
      <div class="donut-wrap">
        <div class="donut" style="--slices: ${gradient}">
          <div class="donut-hole"><div class="n">${total}</div><div class="l">Total</div></div>
        </div>
        <div class="donut-legend">${legend.join("")}</div>
      </div>`;
  }

  function renderActivity() {
    const el = document.getElementById("pm-activity");
    if (!el) return;
    const recent = allPipelines
      .filter((p) => p.last_execution_at)
      .sort((a, b) => new Date(b.last_execution_at) - new Date(a.last_execution_at))
      .slice(0, 5);

    if (recent.length === 0) {
      el.innerHTML = `<div class="muted" style="font-size:12.5px">No executions recorded yet.</div>`;
      return;
    }

    el.innerHTML = recent.map((p) => `
      <div class="activity-item" onclick="location.hash='#/pipeline-monitor/${encodeURIComponent(p.pipeline_name)}'">
        <span class="activity-dot" style="background:${STATUS_HEX[p.execution_status] || "#57606a"}"></span>
        <div class="activity-body">
          <div class="activity-name">${Utils.escapeHtml(p.pipeline_name)}</div>
          <div class="activity-meta">${Utils.dash(p.last_execution_status)} · ${Utils.relativeTime(p.last_execution_at)}</div>
        </div>
      </div>`).join("");
  }

  function renderInsights() {
    const el = document.getElementById("pm-insights");
    if (!el) return;
    const insights = [];
    const neverRun = lastSummary.never_run || 0;
    const failed = lastSummary.failed || 0;
    const configIssues = lastSummary.unknown || 0;
    const needsReview = allPipelines.filter((p) => p.review_status === "needs_review").length;
    const envs = Array.from(new Set(allPipelines.map((p) => p.environment).filter(Boolean)));

    if (needsReview > 0) {
      insights.push({
        icon: ICONS.needs_review,
        title: `${needsReview} auto-discovered pipeline${needsReview === 1 ? "" : "s"} awaiting review`,
        desc: "Found via account-wide discovery but not yet in config/registry.yaml.",
      });
    }
    if (neverRun > 0) {
      insights.push({ icon: ICONS.never_run, title: `${neverRun} pipeline${neverRun === 1 ? "" : "s"} have never run`, desc: "Check schedules and permissions." });
    }
    if (failed > 0) {
      insights.push({ icon: ICONS.failed, title: `${failed} pipeline${failed === 1 ? "" : "s"} failed in their last execution`, desc: "Review logs for details." });
    }
    if (configIssues > 0) {
      insights.push({ icon: ICONS.unknown, title: `${configIssues} pipeline${configIssues === 1 ? "" : "s"} need configuration`, desc: "Schedule or grace period isn't fully set up." });
    }
    if (envs.length === 1) {
      insights.push({ icon: ICONS.fresh, title: `All pipelines are in ${envs[0]} environment`, desc: "No other environments configured.", ok: true });
    } else if (envs.length > 1) {
      insights.push({ icon: ICONS.total, title: `Pipelines span ${envs.length} environments`, desc: envs.join(", ") });
    }
    if (insights.length === 0) {
      insights.push({ icon: ICONS.fresh, title: "All pipelines are healthy", desc: "No issues detected right now.", ok: true });
    }

    el.innerHTML = `<div class="insight-list">${insights.map((i) => `
      <div class="insight-item">
        <span class="insight-icon${i.ok ? " ok" : ""}">${i.icon}</span>
        <div>
          <div class="insight-title">${i.title}</div>
          <div class="insight-desc">${i.desc}</div>
        </div>
      </div>`).join("")}</div>`;
  }

  // Exposed on Views so inline onclick handlers (rendered as raw HTML
  // strings) can reach these without polluting window's global scope.
  Views._pipelineMonitorSelectCard = selectCard;
  Views._pipelineMonitorClearFilters = clearFilters;
})();
