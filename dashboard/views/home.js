Views.home = async function (container) {
  let status = null;
  let error = null;
  try {
    status = await Api.fetchStatus();
  } catch (e) {
    error = e.message;
  }

  const region = status && status.pipelines[0] ? status.pipelines[0].region : null;
  const lastUpdated = status
    ? `Last updated: ${new Date().toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" })} (your local time)`
    : "";

  const s = status ? status.summary : {};
  const total = status ? status.total_pipelines : null;

  const monitorMetrics = status
    ? `<div class="m-metrics">
        <div class="mm"><div class="n">${total}</div><div class="l">Total</div></div>
        <div class="mm"><div class="n">${s.fresh || 0}</div><div class="l">Fresh</div></div>
        <div class="mm"><div class="n">${s.failed || 0}</div><div class="l">Failed</div></div>
        <div class="mm"><div class="n">${s.delayed || 0}</div><div class="l">Delayed</div></div>
        <div class="mm"><div class="n">${s.stale || 0}</div><div class="l">Stale</div></div>
      </div>`
    : `<div class="reason">${error ? `Could not load live metrics: ${Utils.escapeHtml(error)}` : "No live data available."}</div>`;

  container.innerHTML = `
    ${Utils.pageHeader({
      title: "Data Reliability Platform",
      subtitle: "Operational health and data trustworthiness for AWS data pipelines and resources.",
      metaHtml: `
        <div class="pill-row"><span class="pill">region: ${Utils.dash(region)}</span></div>
        <div class="last-updated">${lastUpdated}</div>`,
    })}

    <div class="module-cards">
      <div class="module-card">
        <span class="m-badge">Operational</span>
        <h2>Pipeline Freshness Monitor</h2>
        <p class="m-desc">Monitor pipeline execution health and data freshness across registered AWS Step Functions.</p>
        ${monitorMetrics}
        <div class="m-footer"><a class="btn btn-primary" href="#/pipeline-monitor">Open Monitor</a></div>
      </div>

      <div class="module-card">
        <span class="m-badge">Coming in next phase</span>
        <h2>AWS Data Lineage &amp; Catalog</h2>
        <p class="m-desc">Explore AWS resources, data flows, dependencies, and impact analysis.</p>
        <div class="reason">Discovery module not yet implemented — no resource or relationship data exists yet.</div>
        <div class="m-footer" style="margin-top:16px"><a class="btn" href="#/lineage">Explore Lineage</a></div>
      </div>
    </div>
  `;
};
