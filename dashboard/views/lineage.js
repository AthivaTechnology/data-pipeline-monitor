(function () {
  let pipelineNames = [];
  let currentGraph = null;

  Views.lineage = async function (container) {
    container.innerHTML = Utils.pageHeader({
      title: "AWS Data Lineage & Catalog",
      subtitle: "Explore AWS resources, data flows, dependencies, and impact analysis.",
      metaHtml: `<span class="pill" id="lin-status-pill">Loading…</span>`,
    }) + `<div id="lin-body">${Utils.loadingState("Loading lineage discovery results…")}</div>`;

    let summary, statusData;
    try {
      [summary, statusData] = await Promise.all([Api.fetchLineageSummary(), Api.fetchStatus()]);
    } catch (e) {
      document.getElementById("lin-body").innerHTML = Utils.errorState(e.message);
      return;
    }

    pipelineNames = statusData.pipelines.map((p) => p.pipeline_name).sort();

    const pill = document.getElementById("lin-status-pill");
    if (!summary.discovery_has_run) {
      pill.textContent = "Not yet run";
      renderEmptyNeverRun();
      return;
    }
    pill.textContent = "Active";
    pill.classList.add("pill-active");

    renderBody(summary);
  };

  function renderEmptyNeverRun() {
    document.getElementById("lin-body").innerHTML = `
      <div class="state-box card-style empty-illustration">
        <span class="empty-icon">${ICONS.network}</span>
        <div class="big">Lineage discovery hasn't run yet</div>
        <div>The discovery job runs automatically once a day. Once it completes, every pipeline's detected
        resources and relationships will appear here — nothing below is fake data, it just hasn't been
        collected for the first time yet.</div>
      </div>`;
  }

  function renderBody(summary) {
    const el = document.getElementById("lin-body");
    el.innerHTML = `
      <div class="stat-strip">
        <div class="stat-tile"><div class="n">${summary.total_pipelines_scanned}</div><div class="l">Pipelines scanned</div></div>
        <div class="stat-tile"><div class="n">${summary.total_resources}</div><div class="l">Resources discovered</div></div>
        <div class="stat-tile"><div class="n">${summary.total_relationships}</div><div class="l">Relationships mapped</div></div>
        <div class="stat-tile"><div class="n" style="font-size:14px">${Utils.fmtTime(summary.last_discovery_at)}</div><div class="l">Last discovery run</div></div>
      </div>

      <div class="controls-row">
        <select id="lin-pipeline-select">
          <option value="">Select a pipeline…</option>
          ${pipelineNames.map((n) => `<option value="${Utils.escapeHtml(n)}">${Utils.escapeHtml(n)}</option>`).join("")}
        </select>
        <a class="btn" href="#/lineage/catalog">Browse full resource catalog →</a>
        <a class="btn" href="#/applications/data-exporter">Application Dependency pilot: data-exporter →</a>
        <span class="spacer"></span>
      </div>

      <div id="lin-graph"><div class="state-box card-style"><div class="big">Pick a pipeline above</div><div>Its discovered resources and relationships will appear here.</div></div></div>
    `;
    document.getElementById("lin-pipeline-select").addEventListener("change", (e) => {
      if (e.target.value) loadGraph(e.target.value);
    });
  }

  async function loadGraph(name) {
    const el = document.getElementById("lin-graph");
    el.innerHTML = Utils.loadingState("Loading lineage graph…");
    try {
      currentGraph = await Api.fetchLineageForPipeline(name);
    } catch (e) {
      el.innerHTML = Utils.errorState(e.message);
      return;
    }
    if (currentGraph === null) {
      el.innerHTML = Utils.emptyState(
        "No lineage data for this pipeline yet",
        "It will appear after the next daily discovery run."
      );
      return;
    }
    el.innerHTML = renderGraph(currentGraph);
  }

  function renderGraph(graph) {
    const nodesById = {};
    graph.nodes.forEach((n) => { nodesById[n.resource_id] = n; });

    const chips = graph.nodes.map((n) => `
      <span class="lineage-chip lineage-chip-${n.resource_type}">
        ${Utils.escapeHtml(typeLabel(n.resource_type))}: ${Utils.escapeHtml(n.display_name)}
      </span>`).join(`<span class="arrow">&rarr;</span>`);

    const edgeRows = graph.edges.map((e) => {
      const src = nodesById[e.source_id];
      const tgt = nodesById[e.target_id];
      return `
        <tr>
          <td>${src ? Utils.escapeHtml(src.display_name) : Utils.escapeHtml(e.source_id)} <span class="psub">${src ? typeLabel(src.resource_type) : ""}</span></td>
          <td class="nowrap">&rarr;</td>
          <td>${tgt ? Utils.escapeHtml(tgt.display_name) : Utils.escapeHtml(e.target_id)} <span class="psub">${tgt ? typeLabel(tgt.resource_type) : ""}</span></td>
          <td>${Utils.escapeHtml(e.relationship_source)}</td>
          <td class="nowrap"><span class="badge ${e.confidence === "direct" ? "badge-fresh" : "badge-delayed"}">${e.confidence === "direct" ? "Direct" : "Inferred"}</span></td>
        </tr>`;
    }).join("");

    return `
      <div class="section">
        <h3>${Utils.escapeHtml(graph.pipeline_name)} <span class="footer-note">Discovered ${Utils.fmtTime(graph.discovered_at)}</span></h3>
        ${graph.nodes.length === 0
          ? `<div class="reason-box">No AWS resources could be identified in this pipeline's definition.</div>`
          : `<div class="sample-flow">${chips}</div>`}
      </div>
      ${graph.edges.length > 0 ? `
        <div class="section">
          <h3>Relationships</h3>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Source</th><th></th><th>Target</th><th>Evidence</th><th>Confidence</th></tr></thead>
              <tbody>${edgeRows}</tbody>
            </table>
          </div>
        </div>` : ""}
    `;
  }

  function typeLabel(t) {
    const labels = {
      step_function: "Step Function", eventbridge: "EventBridge", lambda: "Lambda",
      s3: "S3", dynamodb: "DynamoDB", sns: "SNS", sqs: "SQS", kinesis: "Kinesis",
      firehose: "Firehose", glue: "Glue", athena: "Athena", redshift: "Redshift", rds: "RDS",
      iam_role: "IAM Role", cloudwatch_alarm: "CloudWatch Alarm",
    };
    return labels[t] || t;
  }

  Views._lineageTypeLabel = typeLabel;
})();
