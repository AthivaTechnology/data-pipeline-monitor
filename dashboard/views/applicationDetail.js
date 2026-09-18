(function () {
  let appId = "";
  let allResources = [];
  let allEdges = [];

  Views.applicationDetail = async function (container, params) {
    appId = params.id;
    container.innerHTML = `
      ${Utils.breadcrumbs([{ label: "Lineage", href: "#/lineage" }, { label: appId }])}
      <div id="app-body">${Utils.loadingState("Loading application dependency data…")}</div>
    `;

    let app;
    try {
      app = await Api.fetchApplication(appId);
    } catch (e) {
      document.getElementById("app-body").innerHTML = Utils.errorState(e.message);
      return;
    }

    if (app === null) {
      document.getElementById("app-body").innerHTML = `
        <div class="state-box card-style">
          <div class="big">Application not found</div>
          <div>No discovery data exists yet for "${Utils.escapeHtml(appId)}" — either the daily discovery run
          hasn't completed yet, or this id isn't in config/applications.yaml.</div>
          <p style="margin-top:16px"><a class="btn" href="#/lineage">&larr; Back to Lineage</a></p>
        </div>`;
      return;
    }

    allResources = app.resources || [];
    allEdges = app.edges || [];
    renderBody(app);
  };

  function renderBody(app) {
    const el = document.getElementById("app-body");
    el.innerHTML = `
      ${Utils.pageHeader({
        title: app.display_name || appId,
        subtitle: `Full deployment footprint for the "${Utils.escapeHtml(app.stack_name || appId)}" CloudFormation stack — resource inventory and confirmed dependencies, not limited to what a single pipeline's own definition reveals.`,
      })}
      <div class="stat-strip">
        <div class="stat-tile"><div class="n">${app.total_resources ?? allResources.length}</div><div class="l">Resources</div></div>
        <div class="stat-tile"><div class="n">${app.total_relationships ?? allEdges.length}</div><div class="l">Relationships mapped</div></div>
        <div class="stat-tile"><div class="n" style="font-size:14px">${Utils.fmtTime(app.last_discovered_at)}</div><div class="l">Last discovery run</div></div>
      </div>

      <div class="section">
        <h3>Resource Inventory</h3>
        ${allResources.length === 0
          ? `<div class="reason-box">No resources were found in this stack.</div>`
          : `
            <div class="controls-row">
              <input type="text" id="app-search" placeholder="Search by name or type…" />
              <select id="app-type-filter"><option value="">All resource types</option>${resourceTypes().map((t) => `<option value="${t}">${Utils.escapeHtml(typeLabel(t))}</option>`).join("")}</select>
              <span class="spacer"></span>
            </div>
            <div id="app-inventory-table"></div>
          `}
      </div>

      <div class="section">
        <h3>Confirmed Dependencies</h3>
        ${allEdges.length === 0
          ? `<div class="reason-box">No direct relationships were confirmed between this application's resources.</div>`
          : renderEdgesTable()}
      </div>
    `;

    if (allResources.length > 0) {
      document.getElementById("app-search").addEventListener("input", renderInventoryTable);
      document.getElementById("app-type-filter").addEventListener("change", renderInventoryTable);
      renderInventoryTable();
    }
  }

  function resourceTypes() {
    return Array.from(new Set(allResources.map((r) => r.resource_type))).sort();
  }

  function typeLabel(t) {
    if (typeof Views._lineageTypeLabel === "function") return Views._lineageTypeLabel(t);
    return t;
  }

  function renderInventoryTable() {
    const search = (document.getElementById("app-search")?.value || "").trim().toLowerCase();
    const type = document.getElementById("app-type-filter")?.value || "";

    const filtered = allResources.filter((r) => {
      if (type && r.resource_type !== type) return false;
      if (search && !`${r.display_name} ${r.resource_type}`.toLowerCase().includes(search)) return false;
      return true;
    });

    const wrap = document.getElementById("app-inventory-table");
    if (filtered.length === 0) {
      wrap.innerHTML = Utils.emptyState("No resources match your filters", "Try clearing the search box or filter above.");
      return;
    }

    const rows = filtered.map((r) => `
      <tr class="clickable" onclick="location.hash='#/applications/${encodeURIComponent(appId)}/resource/${encodeURIComponent(r.resource_id)}'">
        <td><div class="pname">${Utils.escapeHtml(r.display_name)}</div></td>
        <td class="nowrap"><span class="lineage-chip lineage-chip-${r.resource_type}">${Utils.escapeHtml(typeLabel(r.resource_type))}</span></td>
        <td class="nowrap">${Utils.dash(r.region)}</td>
        <td class="nowrap"><button class="view-btn" onclick="event.stopPropagation(); location.hash='#/applications/${encodeURIComponent(appId)}/resource/${encodeURIComponent(r.resource_id)}'">View</button></td>
      </tr>`).join("");

    wrap.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Resource</th><th>Type</th><th>Region</th><th>Actions</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  function renderEdgesTable() {
    const byId = {};
    allResources.forEach((r) => { byId[r.resource_id] = r; });

    const rows = allEdges.map((e) => {
      const src = byId[e.source_id];
      const tgt = byId[e.target_id];
      return `
        <tr>
          <td>${src ? Utils.escapeHtml(src.display_name) : Utils.escapeHtml(e.source_id)} <span class="psub">${src ? typeLabel(src.resource_type) : ""}</span></td>
          <td class="nowrap">${Utils.escapeHtml(relationshipVerb(e.relationship_type))}</td>
          <td>${tgt ? Utils.escapeHtml(tgt.display_name) : Utils.escapeHtml(e.target_id)} <span class="psub">${tgt ? typeLabel(tgt.resource_type) : ""}</span></td>
          <td>${Utils.escapeHtml(e.relationship_source)}</td>
        </tr>`;
    }).join("");

    return `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Source</th><th>Relationship</th><th>Target</th><th>Evidence</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  function relationshipVerb(t) {
    const labels = {
      starts: "starts", invokes: "invokes", triggered_by: "triggered by",
      reads_from: "reads from", writes_to: "writes to", publishes_to: "publishes to",
      consumes_from: "consumes from", routes_to: "routes to", depends_on: "depends on",
    };
    return labels[t] || (t || "depends on").replace(/_/g, " ");
  }

  Views._relationshipVerb = relationshipVerb;
})();
