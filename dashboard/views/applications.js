(function () {
  Views.applications = async function (container) {
    container.innerHTML = `
      ${Utils.breadcrumbs([{ label: "Lineage", href: "#/lineage" }, { label: "Applications" }])}
      ${Utils.pageHeader({
        title: "Application Dependency",
        subtitle: "Full deployment footprint and confirmed dependencies for each of our team's known applications.",
      })}
      <div id="apps-body">${Utils.loadingState("Loading applications…")}</div>
    `;

    let data;
    try {
      data = await Api.fetchApplications();
    } catch (e) {
      document.getElementById("apps-body").innerHTML = Utils.errorState(e.message);
      return;
    }

    renderBody(data.applications || []);
  };

  function renderBody(applications) {
    const el = document.getElementById("apps-body");
    if (applications.length === 0) {
      el.innerHTML = `
        <div class="state-box card-style empty-illustration">
          <span class="empty-icon">${ICONS.network}</span>
          <div class="big">No applications discovered yet</div>
          <div>Application Dependency discovery runs once a day for every entry in config/applications.yaml.
          Once it completes, each application's resource inventory and dependencies will appear here.</div>
        </div>`;
      return;
    }

    const cards = applications.map((a) => `
      <div class="module-card module-card-clickable" onclick="location.hash='#/applications/${encodeURIComponent(a.application_id)}'">
        <span class="m-badge m-badge-live">${Utils.dash(a.total_resources)} resources</span>
        <span class="m-icon m-icon-alt">${ICONS.network}</span>
        <h2>${Utils.escapeHtml(a.display_name || a.application_id)}</h2>
        <p class="m-desc">Stack: ${Utils.escapeHtml(a.stack_name || a.application_id)}</p>
        <div class="reason">
          ${a.total_resources ?? 0} resource${a.total_resources === 1 ? "" : "s"} and
          ${a.total_relationships ?? 0} relationship${a.total_relationships === 1 ? "" : "s"} mapped.
          Last discovery: ${Utils.fmtTime(a.last_discovered_at)}.
        </div>
        <div class="m-footer" style="margin-top:16px"><span class="btn">View Dependencies &rarr;</span></div>
      </div>`).join("");

    el.innerHTML = `<div class="module-cards">${cards}</div>`;
  }
})();
