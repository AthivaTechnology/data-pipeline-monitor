Views.catalog = async function (container) {
  container.innerHTML = `
    ${Utils.breadcrumbs([{ label: "Lineage", href: "#/lineage" }, { label: "Catalog" }])}
    ${Utils.pageHeader({
      title: "AWS Resource Catalog",
      subtitle: "Searchable inventory of tracked AWS resources.",
      metaHtml: `<span class="pill">Not yet available</span>`,
    })}

    <div class="controls-row">
      <input type="text" placeholder="Search by name, ARN, or tag…" disabled title="Not available until the discovery backend exists" />
      <select disabled title="Not available yet"><option>Resource type</option></select>
      <select disabled title="Not available yet"><option>Environment</option></select>
      <select disabled title="Not available yet"><option>Region</option></select>
      <span class="spacer"></span>
    </div>

    <div class="state-box card-style">
      <div class="big">Resource catalog data is not yet collected</div>
      <div>This page will list every tracked AWS resource — name, type, ARN, environment, region, owner/tags, upstream dependencies, downstream consumers — once the lineage discovery module is implemented. The controls above are a preview of the intended layout and are disabled because there is no data behind them yet.</div>
    </div>
  `;
};
