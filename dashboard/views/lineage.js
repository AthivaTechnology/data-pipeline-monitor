Views.lineage = async function (container) {
  container.innerHTML = `
    ${Utils.pageHeader({
      title: "AWS Data Lineage & Catalog",
      subtitle: "Explore AWS resources, data flows, dependencies, and impact analysis.",
      metaHtml: `<span class="pill pill-soon">Coming Soon</span>`,
    })}

    <div class="state-box card-style empty-illustration">
      <span class="empty-icon">${ICONS.network}</span>
      <div class="big">Discovery module not yet implemented</div>
      <div>No AWS resource inventory, relationships, or graph data exist yet. Nothing below is real data — it previews what this page is designed to show once the discovery backend is built.</div>
      <p style="margin-top:16px"><a class="btn btn-primary" href="#/lineage/catalog">Browse Catalog (preview)</a></p>
    </div>

    <div class="section" style="margin-top:16px">
      <h3>What this module will provide</h3>
      <div class="roadmap">
        <div class="item"><div class="t">AWS resource search</div><div class="d">Find any tracked resource by name, ARN, type, or tag.</div></div>
        <div class="item"><div class="t">Resource type filters</div><div class="d">Step Functions, Lambda, S3, DynamoDB, Athena, QuickSight, and more.</div></div>
        <div class="item"><div class="t">Environment &amp; region filters</div><div class="d">Scope the catalog to a specific environment or AWS region.</div></div>
        <div class="item"><div class="t">Graph view</div><div class="d">Visualize upstream/downstream data flow between resources.</div></div>
        <div class="item"><div class="t">Upstream / downstream relationships</div><div class="d">See what feeds a resource and what consumes it.</div></div>
        <div class="item"><div class="t">Resource details</div><div class="d">Metadata, tags, owner, and configuration for one resource.</div></div>
        <div class="item"><div class="t">Impact analysis</div><div class="d">Understand what breaks downstream if a resource changes.</div></div>
      </div>
    </div>

    <div class="section">
      <h3>Example Future Graph <span class="pill">Illustrative example — not real AWS data</span></h3>
      <div class="sample-flow">
        <span class="node">Step Function</span><span class="arrow">&rarr;</span>
        <span class="node">Lambda</span><span class="arrow">&rarr;</span>
        <span class="node">DynamoDB / S3</span><span class="arrow">&rarr;</span>
        <span class="node">Athena</span><span class="arrow">&rarr;</span>
        <span class="node">QuickSight</span>
      </div>
      <p class="footer-note">This is a sample of the kind of flow the graph view will render once real dependency data is collected — it is not derived from any actual AWS account data.</p>
    </div>
  `;
};
