// Shared shell: config, formatting helpers, API client, and the hash router.
// Plain scripts on purpose (no `type="module"`) - this dashboard is opened
// directly as a local file (file://), and Chrome blocks ES module imports
// under file://. Every view file below attaches itself to window.Views.

// Change this if you redeploy the stack and get a different API URL
// (see CloudFormation output "DashboardApiUrl").
const API_BASE = "https://a8w6yda6fh.execute-api.us-east-1.amazonaws.com";

const EXEC_META = {
  fresh:      { label: "Fresh",      cls: "fresh" },
  delayed:    { label: "Delayed",    cls: "delayed" },
  failed:     { label: "Failed",     cls: "failed" },
  stale:      { label: "Stale",      cls: "stale" },
  running:    { label: "Running",    cls: "running" },
  never_run:  { label: "Never Run",  cls: "never_run" },
  unknown:    { label: "Unknown",    cls: "unknown" },
};
const DATA_META = {
  fresh:                       { label: "Fresh",                cls: "fresh" },
  delayed:                     { label: "Delayed",               cls: "delayed" },
  stale:                       { label: "Stale",                 cls: "stale" },
  unknown:                     { label: "Unknown",                cls: "unknown" },
  not_configured:               { label: "Not Configured",        cls: "not_configured" },
  // Auto-discovered pipelines only - see resource_scanner.py. Distinguishes
  // "nothing found" from "found something, couldn't resolve it", instead of
  // collapsing both into the same generic "Not Configured" a registry
  // pipeline shows when its output was deliberately left unconfigured.
  source_not_detected:          { label: "Output Source Not Detected", cls: "not_configured" },
  source_detected_unavailable:  { label: "Freshness Unavailable",      cls: "not_configured" },
};
const STATUS_EXPLANATIONS = {
  fresh: "Last successful execution completed within the expected interval.",
  delayed: "Expected execution has not completed within the configured grace period.",
  failed: "The latest execution failed, timed out, or was aborted.",
  stale: "The pipeline may have run, but the expected output data was not updated in time.",
  running: "The pipeline is currently executing.",
  never_run: "No executions were found for this state machine.",
  not_configured: "No output freshness source is configured for this pipeline.",
  source_not_detected: "No recognizable AWS resource references were found in this state machine's definition.",
  source_detected_unavailable: "Possible output resources were detected, but not resolved to a specific, checkable location.",
  unknown: "The monitor could not reliably determine the status.",
};
const SUMMARY_ORDER = ["fresh", "delayed", "failed", "stale", "running", "never_run", "unknown"];
const SUMMARY_LABELS = {
  fresh: "Healthy / Fresh", delayed: "Delayed", failed: "Failed", stale: "Stale",
  running: "Running", never_run: "Never Run", unknown: "Configuration Issues",
};

// Shared brand mark + a small set of general-purpose icons reused across
// Home, the navbar, and the Lineage empty state. The logo image itself is
// the company's actual brand asset (dashboard/assets/athivatech-logo.jpg,
// sourced from the devops-onboarding-platform repo), not a recreation -
// callers size it by adding a modifier class onto .brand-mark-wrap.
const BRAND_LOGO = `<span class="brand-mark-wrap"><img src="assets/athivatech-logo.jpg" alt="Athivatech" class="brand-mark-img" /></span>`;

const ICONS = {
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 4.5-3 8-8 9-5-1-8-4.5-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/></svg>',
  speed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 13l3.5-3.5"/><path d="M9 4.5h6"/></svg>',
  eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>',
  bulb: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.5.4.8 1 .8 1.6V16h5.4v-.5c0-.6.3-1.2.8-1.6A6 6 0 0 0 12 3z"/></svg>',
  network: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.6"/><circle cx="18" cy="6" r="2.6"/><circle cx="12" cy="18" r="2.6"/><path d="M8.2 7.3L11 15.5M15.8 7.3L13 15.5M8.6 6h6.8"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>',
  pulse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg>',
};

const Utils = {
  dash(v) { return (v === null || v === undefined || v === "") ? "—" : v; },

  // Distinct from dash(): owner/contact are a human-curation gap, not just
  // "data missing" - said explicitly so it reads as an actionable state.
  notAssigned(v) { return (v === null || v === undefined || v === "") ? "Not assigned" : v; },

  fmtTime(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  },

  fmtDuration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const s = Number(seconds);
    if (s < 60) return `${s.toFixed(1)}s`;
    const m = Math.floor(s / 60);
    const rem = Math.round(s % 60);
    return `${m}m ${rem}s`;
  },

  badge(status, meta, big) {
    const m = meta[status] || { label: status || "Unknown", cls: "unknown" };
    return `<span class="badge ${big ? "badge-lg" : ""} badge-${m.cls}">${m.label}</span>`;
  },

  escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },

  pageHeader({ title, subtitle, metaHtml }) {
    return `<div class="page-header">
      <div><h1>${title}</h1>${subtitle ? `<p class="desc">${subtitle}</p>` : ""}</div>
      ${metaHtml ? `<div class="page-header-meta">${metaHtml}</div>` : ""}
    </div>`;
  },

  breadcrumbs(parts) {
    const html = parts.map((p, i) => i < parts.length - 1
      ? `<a href="${p.href}">${p.label}</a><span class="sep">/</span>`
      : `<span>${p.label}</span>`
    ).join("");
    return `<div class="breadcrumbs">${html}</div>`;
  },

  loadingState(label) {
    return `<div class="state-box card-style"><div class="big">${label || "Loading…"}</div></div>`;
  },

  errorState(message, retryHash) {
    return `<div class="state-box card-style err-box">
      <div class="big">Failed to load data</div>
      <div>${Utils.escapeHtml(message)}</div>
      ${retryHash ? `<p style="margin-top:14px"><a class="btn" href="${retryHash}" onclick="location.reload()">Retry</a></p>` : ""}
    </div>`;
  },

  emptyState(title, body) {
    return `<div class="state-box card-style"><div class="big">${title}</div><div>${body || ""}</div></div>`;
  },

  scheduleLabel(p) {
    // Auto-discovered pipelines never have a registry schedule (that's the
    // whole reason they're auto-discovered) - show what trigger_scanner.py
    // actually found in the account instead of the registry-oriented
    // "requires configuration" wording meant for a mis-set registry entry.
    if (p.source === "discovered") return p.detected_trigger || "Trigger not identified";
    if (p.schedule_type === "custom") {
      if (p.schedule_interval_minutes != null) return `Custom — every ${p.schedule_interval_minutes} minutes`;
      // A verified cron can exist even when we didn't derive a trustworthy
      // interval from it (irregular, bounded-hours, or likely-misconfigured
      // schedules) - show the raw fact instead of a fabricated number.
      return p.schedule_cron_utc
        ? `Custom — requires configuration (raw cron: ${p.schedule_cron_utc} UTC)`
        : "Custom — requires configuration";
    }
    if (p.schedule_type === "hourly") return "Hourly";
    if (p.schedule_type === "daily") return `Daily${p.schedule_cron_utc ? ` (cron: ${p.schedule_cron_utc} UTC)` : ""}`;
    return Utils.dash(p.schedule_type);
  },

  gracePeriodLabel(p) {
    if (p.grace_period_minutes != null) return `${p.grace_period_minutes} minutes`;
    return p.source === "discovered" ? "Not set (auto-discovered)" : "Requires configuration";
  },

  reviewStatusLabel(p) {
    if (p.review_status === "confirmed") return "Confirmed";
    if (p.review_status === "pending_review") return "Pending Review";
    if (p.review_status === "needs_review") return "Needs Review";
    return Utils.dash(p.review_status);
  },

  envBadge(env) {
    if (!env) return "—";
    return `<span class="badge badge-env">${Utils.escapeHtml(env)}</span>`;
  },

  // Coarse, human "x ago" phrasing for the Recent Activity list - purely a
  // display formatter over a real timestamp already in the API response,
  // never a source of data itself.
  relativeTime(iso) {
    if (!iso) return "—";
    const then = new Date(iso).getTime();
    if (isNaN(then)) return "—";
    const diffMin = Math.round((Date.now() - then) / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.round(diffHr / 24);
    return `${diffDay}d ago`;
  },
};

const Api = {
  async fetchStatus() {
    const res = await fetch(`${API_BASE}/status`, { cache: "no-store" });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch (e) { throw new Error("API returned invalid JSON"); }
    if (!res.ok) throw new Error(data.error || data.message || `HTTP ${res.status}`);
    if (typeof data.total_pipelines !== "number" || !Array.isArray(data.pipelines)) {
      throw new Error("API response is missing expected fields");
    }
    return data;
  },

  async fetchPipeline(name) {
    const res = await fetch(`${API_BASE}/status/${encodeURIComponent(name)}`, { cache: "no-store" });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch (e) { throw new Error("API returned invalid JSON"); }
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(data.error || data.message || `HTTP ${res.status}`);
    return data;
  },
};

const Views = {}; // populated by dashboard/views/*.js

// ---------------- Router ----------------
const ROUTES = [
  { pattern: /^#\/?$/, view: "home", nav: "home" },
  { pattern: /^#\/pipeline-monitor\/?(?:\?.*)?$/, view: "pipelineMonitor", nav: "pipeline-monitor" },
  { pattern: /^#\/pipeline-monitor\/([^/]+)\/?$/, view: "pipelineDetail", params: ["name"], nav: "pipeline-monitor" },
  { pattern: /^#\/lineage\/?$/, view: "lineage", nav: "lineage" },
  { pattern: /^#\/lineage\/catalog\/?$/, view: "catalog", nav: "lineage" },
  { pattern: /^#\/lineage\/resource\/([^/]+)\/?$/, view: "resourceDetail", params: ["id"], nav: "lineage" },
  { pattern: /^#\/settings\/monitoring\/?$/, view: "settingsMonitoring", nav: "settings" },
];

function setActiveNav(navKey) {
  document.querySelectorAll(".navbar .nav-link").forEach((el) => {
    el.classList.toggle("active", el.dataset.nav === navKey);
  });
}

function router() {
  const hash = location.hash || "#/";
  for (const route of ROUTES) {
    const m = hash.match(route.pattern);
    if (m) {
      const params = {};
      (route.params || []).forEach((p, i) => { params[p] = decodeURIComponent(m[i + 1]); });
      setActiveNav(route.nav);
      const container = document.getElementById("app");
      const viewFn = Views[route.view];
      if (typeof viewFn !== "function") {
        container.innerHTML = Utils.errorState(`View "${route.view}" failed to load.`);
        return;
      }
      container.innerHTML = Utils.loadingState();
      Promise.resolve(viewFn(container, params)).catch((e) => {
        container.innerHTML = Utils.errorState(e.message);
      });
      return;
    }
  }
  document.getElementById("app").innerHTML = Utils.emptyState("Page not found", `No route matches "${hash}".`);
}

window.addEventListener("hashchange", router);
// Initial render is triggered explicitly by index.html's closing inline
// script, not DOMContentLoaded - by the time these scripts run (placed at
// the end of body), the DOM is already fully parsed and available, and a
// DOMContentLoaded listener registered here would fire *again* shortly
// after and double-render (and double-fetch the API) on every page load.
