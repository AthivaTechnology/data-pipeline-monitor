Views.home = async function (container) {
  let status = null;
  let error = null;
  try {
    status = await Api.fetchStatus();
  } catch (e) {
    error = e.message;
  }

  let lineageSummary = null;
  try {
    lineageSummary = await Api.fetchLineageSummary();
  } catch (e) {
    // Lineage card just falls back to its "not yet run" state below - a
    // failure to load it should never block the rest of the homepage.
  }

  const s = status ? status.summary : {};
  const total = status ? status.total_pipelines : null;

  // Each stat links straight into Pipeline Monitor pre-filtered to that
  // status (?status=<key>, read by pipelineMonitor.js on load) - clicking a
  // number takes you to exactly the pipelines behind it, not just the page.
  const METRIC_DEFS = [
    { key: "total", label: "Total", cls: "mm-total", value: total, href: "#/pipeline-monitor" },
    { key: "fresh", label: "Healthy", cls: "mm-fresh", value: s.fresh || 0, href: "#/pipeline-monitor?status=fresh" },
    { key: "failed", label: "Failed", cls: "mm-failed", value: s.failed || 0, href: "#/pipeline-monitor?status=failed" },
    { key: "delayed", label: "Delayed", cls: "mm-delayed", value: s.delayed || 0, href: "#/pipeline-monitor?status=delayed" },
    { key: "stale", label: "Stale", cls: "mm-stale", value: s.stale || 0, href: "#/pipeline-monitor?status=stale" },
  ];
  const monitorMetrics = status
    ? `<div class="m-metrics">
        ${METRIC_DEFS.map((m) => `
          <a class="mm ${m.cls}" href="${m.href}" onclick="event.stopPropagation()">
            <div class="n">${m.value}</div><div class="l">${m.label}</div>
          </a>`).join("")}
      </div>`
    : `<div class="reason">${error ? `Could not load live metrics: ${Utils.escapeHtml(error)}` : "No live data available."}</div>`;

  const benefits = [
    { icon: ICONS.shield, title: "Improved Data Reliability", desc: "Catch silent failures before they reach a dashboard." },
    { icon: ICONS.speed, title: "Faster Issue Detection", desc: "Know within minutes when a pipeline stalls or fails." },
    { icon: ICONS.eye, title: "Better Data Visibility", desc: "One place to see execution health and data freshness." },
    { icon: ICONS.bulb, title: "Informed Decision Making", desc: "Act on real status, not assumptions about what ran." },
  ];

  container.innerHTML = `
    <div class="hero">
      <div class="hero-blob hero-blob-a"></div>
      <div class="hero-blob hero-blob-b"></div>
      <div class="hero-content">
        ${BRAND_LOGO.replace('class="brand-mark-wrap"', 'class="brand-mark-wrap hero-logo"')}
        <h1 class="hero-title">Athivatech</h1>
        <div class="hero-subtitle">Data Reliability Platform</div>
        <p class="hero-tagline">Monitor. Understand. Trust your data.</p>
      </div>
    </div>

    <div class="module-cards">
      <div class="module-card module-card-clickable" onclick="location.hash='#/pipeline-monitor'">
        <span class="m-badge m-badge-live">Operational</span>
        <span class="m-icon">${ICONS.pulse}</span>
        <h2>Pipeline Monitor</h2>
        <p class="m-desc">Monitor pipeline execution health and data freshness across registered AWS Step Functions.</p>
        ${monitorMetrics}
        <div class="m-footer"><a class="btn btn-primary" href="#/pipeline-monitor" onclick="event.stopPropagation()">Open Monitor &rarr;</a></div>
      </div>

      <a class="module-card module-card-link" href="#/lineage">
        ${lineageSummary && lineageSummary.discovery_has_run
          ? `<span class="m-badge m-badge-live">Active</span>`
          : `<span class="m-badge m-badge-soon">Discovery pending</span>`}
        <span class="m-icon m-icon-alt">${ICONS.network}</span>
        <h2>AWS Data Lineage &amp; Catalog</h2>
        <p class="m-desc">Explore AWS resources, data flows, dependencies, and impact analysis.</p>
        <div class="reason">
          ${lineageSummary && lineageSummary.discovery_has_run
            ? `${lineageSummary.total_resources} resource${lineageSummary.total_resources === 1 ? "" : "s"} and ${lineageSummary.total_relationships} relationship${lineageSummary.total_relationships === 1 ? "" : "s"} discovered across ${lineageSummary.total_pipelines_scanned} pipeline${lineageSummary.total_pipelines_scanned === 1 ? "" : "s"}.`
            : "Discovery runs automatically once a day — results will appear here after the first run."}
        </div>
        <div class="m-footer" style="margin-top:16px"><span class="btn">Explore Lineage →</span></div>
      </a>
    </div>

    <div class="benefits">
      ${benefits.map((b) => `
        <div class="benefit">
          <span class="benefit-icon">${b.icon}</span>
          <div class="benefit-title">${b.title}</div>
          <div class="benefit-desc">${b.desc}</div>
        </div>`).join("")}
    </div>

    <div class="site-footer">
      ${BRAND_LOGO.replace('class="brand-mark-wrap"', 'class="brand-mark-wrap footer-logo"')}
      <div class="site-footer-brand">Athivatech</div>
      <div>Data Reliability Platform</div>
      <div class="footer-tagline">Built for a more reliable data future</div>
    </div>
  `;
};
