(function () {
  Views.pipelineDetail = async function (container, params) {
    const name = params.name;
    let p;
    try {
      p = await Api.fetchPipeline(name);
    } catch (e) {
      container.innerHTML = `
        <a class="back-link" href="#/pipeline-monitor">&larr; Back to Pipeline Monitor</a>
        ${Utils.errorState(e.message)}`;
      return;
    }

    if (p === null) {
      container.innerHTML = `
        <a class="back-link" href="#/pipeline-monitor">&larr; Back to Pipeline Monitor</a>
        ${Utils.emptyState("Pipeline not found", `No pipeline named "${Utils.escapeHtml(name)}" is registered.`)}`;
      return;
    }

    const isNeverRun = p.execution_status === "never_run";

    container.innerHTML = `
      ${Utils.breadcrumbs([{ label: "Pipeline Monitor", href: "#/pipeline-monitor" }, { label: p.pipeline_name }])}

      <div class="detail-header">
        <h1 style="margin:0 0 2px;font-size:19px">${Utils.escapeHtml(p.pipeline_name)}</h1>
        <div class="arn">${Utils.escapeHtml(p.state_machine_arn)}</div>
        <p style="margin:4px 0 0"><a href="${consoleUrl(p.state_machine_arn, p.region)}" target="_blank" rel="noopener">Open in AWS Step Functions console &#8599;</a></p>
        <div class="badges">
          ${Utils.badge(p.execution_status, EXEC_META, true)}
          ${Utils.badge(p.data_status, DATA_META, true)}
          ${p.source === "discovered" ? '<span class="badge badge-not_configured badge-lg">Auto-discovered</span>' : ""}
        </div>
      </div>

      ${p.source === "discovered" ? `
        <div class="section">
          <p class="footer-note" style="margin:0">${Utils.escapeHtml(p.discovery_reason || "Auto-discovered; not present in config/registry.yaml.")}</p>
        </div>` : ""}

      <div class="section">
        <h3>Configuration</h3>
        <div class="kv-grid">
          <div><div class="k">Environment</div><div class="v">${p.environment
            ? Utils.escapeHtml(p.environment)
            : `Not automatically detected<div class="footer-note" style="margin-top:4px">${Utils.escapeHtml(p.environment_reason || "No environment could be automatically determined for this pipeline.")}</div>`}</div></div>
          <div><div class="k">Region</div><div class="v">${Utils.dash(p.region)}</div></div>
          <div><div class="k">Owner</div><div class="v">${Utils.notAssigned(p.owner)}</div></div>
          <div><div class="k">Contact</div><div class="v">${Utils.notAssigned(p.contact)}</div></div>
          <div><div class="k">Monitoring</div><div class="v">Enabled</div></div>
          <div><div class="k">Alerting</div><div class="v">${p.alerting_enabled ? "Enabled" : "Disabled"}</div></div>
          <div><div class="k">Review Status</div><div class="v">${Utils.reviewStatusLabel(p)}</div></div>
          <div><div class="k">Configured Schedule</div><div class="v">${Utils.scheduleLabel(p)}</div></div>
          <div><div class="k">Grace Period</div><div class="v">${Utils.gracePeriodLabel(p)}</div></div>
          ${p.source === "discovered" ? `<div><div class="k">Detected Trigger</div><div class="v">${Utils.dash(p.detected_trigger)}</div></div>` : ""}
          ${p.state_machine_status ? `<div><div class="k">State Machine Status</div><div class="v">${Utils.escapeHtml(p.state_machine_status)}</div></div>` : ""}
          ${p.created_at ? `<div><div class="k">Created</div><div class="v">${Utils.fmtTime(p.created_at)}</div></div>` : ""}
        </div>
      </div>

      ${p.source === "discovered" ? `
        <div class="section">
          <h3>Detected Resources</h3>
          ${(p.detected_resources && p.detected_resources.length > 0)
            ? `<ul class="roadmap" style="list-style:none;padding:0;margin:0">${p.detected_resources.map((r) => `<li class="item" style="padding:8px 0"><div class="t">${Utils.escapeHtml(r)}</div></li>`).join("")}</ul>`
            : `<div class="reason-box">No S3, Lambda, DynamoDB, Athena, Glue, Redshift, or RDS references were found in this state machine's definition.</div>`}
        </div>` : ""}

      <div class="section">
        <h3>Execution Health — ${Utils.badge(p.execution_status, EXEC_META)}</h3>
        <div class="reason-box">${Utils.escapeHtml(p.execution_reason)}</div>
        ${isNeverRun ? `<p class="footer-note">This pipeline has no execution history yet. That is expected for a pipeline that has never been triggered — it is not a monitoring error.</p>` : ""}
        <div class="kv-grid" style="margin-top:12px">
          <div><div class="k">Last Successful Execution</div><div class="v">${Utils.fmtTime(p.last_successful_execution_at, "No successful run yet")}</div></div>
          <div><div class="k">Last Execution</div><div class="v">${(p.last_execution_status || p.last_execution_at) ? `${Utils.dash(p.last_execution_status)} — ${Utils.fmtTime(p.last_execution_at)}` : "No execution yet"}</div></div>
          <div><div class="k">Last Execution Duration</div><div class="v">${Utils.fmtDuration(p.last_execution_duration_seconds, "Not available")}</div></div>
          <div><div class="k">Expected Next Run</div><div class="v">${Utils.fmtTime(p.expected_next_run, "Not scheduled")}</div></div>
        </div>
      </div>

      <div class="section">
        <h3>Recent Execution History</h3>
        ${renderHistoryTable(p.recent_executions)}
      </div>

      <div class="section">
        <h3>Data Freshness — ${Utils.badge(p.data_status, DATA_META)}</h3>
        <div class="reason-box">${Utils.escapeHtml(p.data_reason)}</div>
        ${p.data_checked_location
          ? `<div class="kv-grid" style="margin-top:12px">
              <div><div class="k">Output Type</div><div class="v">${outputType(p.data_checked_location)}</div></div>
              <div><div class="k">Output Location</div><div class="v">${Utils.dash(p.data_checked_location)}</div></div>
              <div><div class="k">Data Last Modified</div><div class="v">${Utils.fmtTime(p.data_last_modified)}</div></div>
            </div>`
          : ""}
      </div>

      <div class="section">
        <div class="kv-grid">
          <div><div class="k">Last Monitor Check</div><div class="v">${Utils.fmtTime(p.last_checked_at)}</div></div>
        </div>
      </div>
    `;
  };

  function consoleUrl(arn, region) {
    return `https://${region}.console.aws.amazon.com/states/home?region=${encodeURIComponent(region)}#/statemachines/view/${encodeURIComponent(arn)}`;
  }

  function outputType(location) {
    if (!location) return "—";
    if (location.startsWith("s3://")) return "S3";
    return "—";
  }

  function renderHistoryTable(executions) {
    if (!executions || executions.length === 0) {
      return `<div class="reason-box">No execution history available.</div>`;
    }
    const rows = executions.map((e) => `
      <tr>
        <td>${Utils.dash(e.status)}</td>
        <td>${Utils.fmtTime(e.start_date)}</td>
        <td>${Utils.fmtTime(e.stop_date)}</td>
        <td>${Utils.fmtDuration(e.duration_seconds)}</td>
        <td class="reason">${Utils.escapeHtml(e.error) || (e.cause ? Utils.escapeHtml(e.cause) : "—")}</td>
      </tr>`).join("");
    return `
      <table class="history-table">
        <thead><tr><th>Status</th><th>Started</th><th>Completed</th><th>Duration</th><th>Error / Cause</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }
})();
