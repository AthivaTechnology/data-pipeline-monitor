Views.home = async function (container) {
  let status = null;
  let error = null;
  try {
    status = await Api.fetchStatus();
  } catch (e) {
    error = e.message;
  }

  const s = status ? status.summary : {};
  const total = status ? status.total_pipelines : null;

  // Exactly the 4 metrics requested for the Home page card - a different,
  // smaller subset than the Pipeline Monitor page's own summary cards.
  const monitorMetrics = status
    ? `<div class="m-metrics">
        <div class="mm"><div class="n">${total}</div><div class="l">Total</div></div>
        <div class="mm"><div class="n">${s.fresh || 0}</div><div class="l">Fresh</div></div>
        <div class="mm"><div class="n">${s.failed || 0}</div><div class="l">Failed</div></div>
        <div class="mm"><div class="n">${s.never_run || 0}</div><div class="l">Never Run</div></div>
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
        ${BRAND_LOGO.replace('viewBox="0 0 40 40"', 'viewBox="0 0 40 40" class="hero-logo"')}
        <h1 class="hero-title">Athivatech</h1>
        <div class="hero-subtitle">Data Reliability Platform</div>
        <p class="hero-tagline">Monitor. Understand. Trust your data.</p>
      </div>
    </div>

    <div class="module-cards">
      <a class="module-card module-card-link" href="#/pipeline-monitor">
        <span class="m-badge m-badge-live">Operational</span>
        <span class="m-icon">${ICONS.pulse}</span>
        <h2>Pipeline Monitor</h2>
        <p class="m-desc">Monitor pipeline execution health and data freshness across registered AWS Step Functions.</p>
        ${monitorMetrics}
        <div class="m-footer"><span class="btn btn-primary">Open Monitor →</span></div>
      </a>

      <a class="module-card module-card-link" href="#/lineage">
        <span class="m-badge m-badge-soon">Coming Soon</span>
        <span class="m-icon m-icon-alt">${ICONS.network}</span>
        <h2>AWS Data Lineage &amp; Catalog</h2>
        <p class="m-desc">Explore AWS resources, data flows, dependencies, and impact analysis.</p>
        <div class="reason">Discovery module not yet implemented — no resource or relationship data exists yet.</div>
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
      <div class="site-footer-brand">Athivatech</div>
      <div>Data Reliability Platform</div>
    </div>
  `;
};
