Views.resourceDetail = async function (container, params) {
  container.innerHTML = `
    ${Utils.breadcrumbs([
      { label: "Lineage", href: "#/lineage" },
      { label: "Catalog", href: "#/lineage/catalog" },
      { label: params.id },
    ])}
    ${Utils.pageHeader({ title: "Resource Details", subtitle: `Requested resource: ${Utils.escapeHtml(params.id)}` })}

    <div class="state-box card-style">
      <div class="big">This resource catalog is not yet available</div>
      <div>Individual AWS resource metadata, upstream/downstream relationships, related pipelines, relationship evidence, and impact analysis will appear here once the lineage discovery module is implemented. No data exists for "${Utils.escapeHtml(params.id)}" because no resources have been catalogued yet.</div>
      <p style="margin-top:16px"><a class="btn" href="#/lineage/catalog">&larr; Back to Catalog</a></p>
    </div>
  `;
};
