Views.settingsMonitoring = async function (container) {
  let data;
  try {
    data = await Api.fetchStatus();
  } catch (e) {
    container.innerHTML = Utils.pageHeader({ title: "Monitoring Configuration" }) + Utils.errorState(e.message);
    return;
  }

  const pipelines = data.pipelines;
  const rows = pipelines.length === 0
    ? `<tr><td colspan="9" class="muted">No pipelines registered.</td></tr>`
    : pipelines.map((p) => `
        <tr>
          <td class="pname">${Utils.escapeHtml(p.pipeline_name)}</td>
          <td>${Utils.dash(p.environment)}</td>
          <td>Enabled</td>
          <td>${p.alerting_enabled ? "Enabled" : "Disabled"}</td>
          <td>${Utils.reviewStatusLabel(p)}</td>
          <td>${Utils.scheduleLabel(p)} &middot; grace ${Utils.gracePeriodLabel(p)}</td>
          <td>${p.data_status === "not_configured" ? "Not configured" : Utils.dash(p.data_checked_location)}</td>
          <td>${Utils.notAssigned(p.owner)}</td>
          <td>${Utils.notAssigned(p.contact)}</td>
        </tr>`).join("");

  container.innerHTML = `
    ${Utils.pageHeader({
      title: "Monitoring Configuration",
      subtitle: "Read-only view of the pipeline registry backing the freshness monitor.",
    })}

    <div class="section" style="margin-bottom:16px">
      <p class="footer-note" style="margin:0">
        This page is read-only for this phase — editing requires updating <code>config/registry.yaml</code> and redeploying.
        "Monitoring" always shows Enabled here because the API currently only returns pipelines it actively monitors;
        a pipeline registered with monitoring disabled would not appear in this list at all rather than showing as disabled.
      </p>
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Pipeline</th><th>Environment</th><th>Monitoring</th><th>Alerting</th><th>Review Status</th>
            <th>Schedule</th><th>Output Freshness Source</th><th>Owner</th><th>Contact</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
};
