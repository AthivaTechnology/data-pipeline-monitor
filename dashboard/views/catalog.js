(function () {
  let allResources = [];

  Views.catalog = async function (container) {
    container.innerHTML = `
      ${Utils.breadcrumbs([{ label: "Lineage", href: "#/lineage" }, { label: "Catalog" }])}
      ${Utils.pageHeader({
        title: "AWS Resource Catalog",
        subtitle: "Searchable inventory of tracked AWS resources.",
      })}
      <div id="cat-body">${Utils.loadingState("Loading resource catalog…")}</div>
    `;

    let data;
    try {
      data = await Api.fetchResources();
    } catch (e) {
      document.getElementById("cat-body").innerHTML = Utils.errorState(e.message);
      return;
    }

    allResources = data.resources;
    renderBody();
  };

  function renderBody() {
    const el = document.getElementById("cat-body");
    if (allResources.length === 0) {
      el.innerHTML = `
        <div class="state-box card-style empty-illustration">
          <span class="empty-icon">${ICONS.network}</span>
          <div class="big">No resources catalogued yet</div>
          <div>Lineage discovery runs once a day and will populate this catalog automatically once it finds resources referenced by your pipelines' Step Function definitions.</div>
        </div>`;
      return;
    }

    const types = Array.from(new Set(allResources.map((r) => r.resource_type))).sort();
    const regions = Array.from(new Set(allResources.map((r) => r.region).filter(Boolean))).sort();

    el.innerHTML = `
      <div class="controls-row">
        <input type="text" id="cat-search" placeholder="Search by name, type, or pipeline…" />
        <select id="cat-type-filter"><option value="">All resource types</option>${types.map((t) => `<option value="${t}">${Utils.escapeHtml(Views._lineageTypeLabel(t))}</option>`).join("")}</select>
        <select id="cat-region-filter"><option value="">All regions</option>${regions.map((r) => `<option value="${r}">${r}</option>`).join("")}</select>
        <span class="spacer"></span>
      </div>
      <div id="cat-table"></div>
    `;
    document.getElementById("cat-search").addEventListener("input", renderTable);
    document.getElementById("cat-type-filter").addEventListener("change", renderTable);
    document.getElementById("cat-region-filter").addEventListener("change", renderTable);
    renderTable();
  }

  function renderTable() {
    const search = (document.getElementById("cat-search")?.value || "").trim().toLowerCase();
    const type = document.getElementById("cat-type-filter")?.value || "";
    const region = document.getElementById("cat-region-filter")?.value || "";

    const filtered = allResources.filter((r) => {
      if (type && r.resource_type !== type) return false;
      if (region && r.region !== region) return false;
      if (search) {
        const haystack = `${r.display_name} ${r.resource_type} ${(r.pipelines || []).join(" ")}`.toLowerCase();
        if (!haystack.includes(search)) return false;
      }
      return true;
    });

    const wrap = document.getElementById("cat-table");
    if (filtered.length === 0) {
      wrap.innerHTML = Utils.emptyState("No resources match your filters", "Try clearing the search box or filters above.");
      return;
    }

    const rows = filtered.map((r) => `
      <tr class="clickable" onclick="location.hash='#/lineage/resource/${encodeURIComponent(r.resource_id)}'">
        <td><div class="pname">${Utils.escapeHtml(r.display_name)}</div></td>
        <td class="nowrap"><span class="lineage-chip lineage-chip-${r.resource_type}">${Utils.escapeHtml(Views._lineageTypeLabel(r.resource_type))}</span></td>
        <td class="nowrap">${Utils.dash(r.region)}</td>
        <td>${(r.pipelines || []).map((p) => Utils.escapeHtml(p)).join(", ") || Utils.dash(null)}</td>
        <td class="nowrap"><button class="view-btn" onclick="event.stopPropagation(); location.hash='#/lineage/resource/${encodeURIComponent(r.resource_id)}'">View</button></td>
      </tr>`).join("");

    wrap.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Resource</th><th>Type</th><th>Region</th><th>Used by pipelines</th><th>Actions</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }
})();
