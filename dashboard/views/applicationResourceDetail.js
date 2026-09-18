Views.applicationResourceDetail = async function (container, params) {
  const { id: appId, resourceId } = params;

  container.innerHTML = `
    ${Utils.breadcrumbs([
      { label: "Lineage", href: "#/lineage" },
      { label: appId, href: `#/applications/${encodeURIComponent(appId)}` },
      { label: resourceId },
    ])}
    <div id="appres-body">${Utils.loadingState("Loading resource detail…")}</div>
  `;

  let r;
  try {
    r = await Api.fetchApplicationResource(appId, resourceId);
  } catch (e) {
    document.getElementById("appres-body").innerHTML = Utils.errorState(e.message);
    return;
  }

  const el = document.getElementById("appres-body");
  if (r === null) {
    el.innerHTML = `
      <div class="state-box card-style">
        <div class="big">This resource isn't in the application catalog</div>
        <div>No discovery data exists for "${Utils.escapeHtml(resourceId)}" in application "${Utils.escapeHtml(appId)}".</div>
        <p style="margin-top:16px"><a class="btn" href="#/applications/${encodeURIComponent(appId)}">&larr; Back to ${Utils.escapeHtml(appId)}</a></p>
      </div>`;
    return;
  }

  const typeLabel = (t) => (typeof Views._lineageTypeLabel === "function" ? Views._lineageTypeLabel(t) : t);
  const verb = (t) => (typeof Views._relationshipVerb === "function" ? Views._relationshipVerb(t) : (t || "depends on").replace(/_/g, " "));

  const relRow = (rel) => `
    <div class="activity-item" onclick="location.hash='#/applications/${encodeURIComponent(appId)}/resource/${encodeURIComponent(rel.resource_id)}'">
      <span class="lineage-chip lineage-chip-${rel.resource_type || ""}">${Utils.escapeHtml(typeLabel(rel.resource_type || ""))}</span>
      <div class="activity-body">
        <div class="activity-name">${Utils.escapeHtml(rel.display_name || rel.resource_id)}</div>
        <div class="activity-meta">${Utils.escapeHtml(verb(rel.relationship_type))}${rel.relationship_source ? ` &middot; ${Utils.escapeHtml(rel.relationship_source)}` : ""}</div>
      </div>
    </div>`;

  const impactRow = (imp) => `
    <div class="activity-item" onclick="location.hash='#/applications/${encodeURIComponent(appId)}/resource/${encodeURIComponent(imp.resource_id)}'">
      <span class="lineage-chip lineage-chip-${imp.resource_type || ""}">${Utils.escapeHtml(typeLabel(imp.resource_type || ""))}</span>
      <div class="activity-body">
        <div class="activity-name">${Utils.escapeHtml(imp.display_name || imp.resource_id)}</div>
        <div class="activity-meta">${imp.hops} hop${imp.hops === 1 ? "" : "s"} downstream via "${Utils.escapeHtml(verb(imp.relationship_type))}"</div>
      </div>
    </div>`;

  const upstream = r.upstream || [];
  const downstream = r.downstream || [];
  const impact = r.impact || [];

  el.innerHTML = `
    <div class="detail-header">
      <h1 style="margin:0 0 2px;font-size:19px">${Utils.escapeHtml(r.display_name)}</h1>
      <div class="arn">${Utils.escapeHtml(r.resource_id)}</div>
      <div class="badges">
        <span class="badge badge-lg lineage-chip-${r.resource_type}">${Utils.escapeHtml(typeLabel(r.resource_type))}</span>
      </div>
    </div>

    <div class="section">
      <h3>Details</h3>
      <div class="kv-grid">
        <div><div class="k">Region</div><div class="v">${Utils.dash(r.region)}</div></div>
        <div><div class="k">Application</div><div class="v"><a href="#/applications/${encodeURIComponent(appId)}">${Utils.escapeHtml(appId)}</a></div></div>
      </div>
    </div>

    <div class="section">
      <h3>Upstream (feeds this resource)</h3>
      ${upstream.length ? upstream.map(relRow).join("") : `<div class="reason-box">Nothing was found feeding into this resource.</div>`}
    </div>

    <div class="section">
      <h3>Downstream (this resource feeds into)</h3>
      ${downstream.length ? downstream.map(relRow).join("") : `<div class="reason-box">Nothing was found downstream of this resource.</div>`}
    </div>

    <div class="section">
      <h3>Potentially affected (full downstream impact)</h3>
      <p class="desc" style="margin:-4px 0 12px">Everything transitively reachable downstream of this resource — not just its direct neighbors — so you can see the full blast radius before changing or disabling it.</p>
      ${impact.length ? impact.map(impactRow).join("") : `<div class="reason-box">Changing or disabling this resource has no confirmed downstream impact within this application.</div>`}
    </div>
  `;
};
