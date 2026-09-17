Views.resourceDetail = async function (container, params) {
  container.innerHTML = `
    ${Utils.breadcrumbs([
      { label: "Lineage", href: "#/lineage" },
      { label: "Catalog", href: "#/lineage/catalog" },
      { label: params.id },
    ])}
    ${Utils.pageHeader({ title: "Resource Details", subtitle: `Requested resource: ${Utils.escapeHtml(params.id)}` })}
    <div id="rd-body">${Utils.loadingState("Loading resource detail…")}</div>
  `;

  let r;
  try {
    r = await Api.fetchResource(params.id);
  } catch (e) {
    document.getElementById("rd-body").innerHTML = Utils.errorState(e.message);
    return;
  }

  const el = document.getElementById("rd-body");
  if (r === null) {
    el.innerHTML = `
      <div class="state-box card-style">
        <div class="big">This resource isn't in the catalog</div>
        <div>No lineage data exists for "${Utils.escapeHtml(params.id)}" — either it hasn't been discovered yet, or the id in the URL doesn't match a known resource.</div>
        <p style="margin-top:16px"><a class="btn" href="#/lineage/catalog">&larr; Back to Catalog</a></p>
      </div>`;
    return;
  }

  const relRow = (rel) => `
    <div class="activity-item" onclick="location.hash='#/lineage/resource/${encodeURIComponent(rel.resource_id)}'">
      <span class="lineage-chip lineage-chip-${rel.resource_type || ""}">${Utils.escapeHtml(Views._lineageTypeLabel(rel.resource_type || ""))}</span>
      <div class="activity-body">
        <div class="activity-name">${Utils.escapeHtml(rel.display_name)}</div>
        <div class="activity-meta">${Utils.escapeHtml(rel.relationship_source)} &middot;
          <span class="badge ${rel.confidence === "direct" ? "badge-fresh" : "badge-delayed"}">${rel.confidence === "direct" ? "Direct" : "Inferred"}</span>
        </div>
      </div>
    </div>`;

  el.innerHTML = `
    <div class="detail-header">
      <h1 style="margin:0 0 2px;font-size:19px">${Utils.escapeHtml(r.display_name)}</h1>
      <div class="arn">${Utils.escapeHtml(r.resource_id)}</div>
      <div class="badges">
        <span class="badge badge-lg lineage-chip-${r.resource_type}">${Utils.escapeHtml(Views._lineageTypeLabel(r.resource_type))}</span>
      </div>
    </div>

    <div class="section">
      <h3>Details</h3>
      <div class="kv-grid">
        <div><div class="k">Region</div><div class="v">${Utils.dash(r.region)}</div></div>
        <div><div class="k">Used by pipelines</div><div class="v">${(r.pipelines || []).map((p) => `<a href="#/pipeline-monitor/${encodeURIComponent(p)}">${Utils.escapeHtml(p)}</a>`).join(", ") || "None currently"}</div></div>
      </div>
    </div>

    <div class="section">
      <h3>Upstream (feeds this resource)</h3>
      ${r.upstream.length ? r.upstream.map(relRow).join("") : `<div class="reason-box">Nothing was found feeding into this resource.</div>`}
    </div>

    <div class="section">
      <h3>Downstream (consumes from this resource)</h3>
      ${r.downstream.length ? r.downstream.map(relRow).join("") : `<div class="reason-box">Nothing was found downstream of this resource.</div>`}
    </div>
  `;
};
