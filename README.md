# Data Reliability Platform — Pipeline Freshness Monitor

Monitors AWS Step Functions data pipelines for execution health and output
data freshness, and shows the result in a small internal dashboard. Read-only
against AWS: it never starts, stops, or modifies any pipeline or trigger.

## Architecture

```
EventBridge (rate(15 minutes), no Input)
  -> MonitorFunction (Lambda) -- default mode: freshness monitoring
       -> Step Functions API: list_state_machines (discovery - the single
          source of truth for which pipelines exist and get processed)
       -> config/registry.yaml (optional, per-ARN metadata override - see below)
       -> Step Functions API (list_executions / describe_execution)
       -> freshness engine (pure logic, no AWS calls)
       -> S3 API (optional, only when a registry override configures an output)
       -> DynamoDB (PipelineStatusTable)

EventBridge (rate(1 day), Input: {"mode": "lineage"})
  -> MonitorFunction (Lambda) -- same function, lineage mode
       -> Step Functions API: list_state_machines (same discovery call, reused)
       -> Step Functions API: describe_state_machine (the ASL definition)
       -> lineage_scanner (pure logic: walks the definition's real control
          flow - Next/Choices/Parallel/Map - into ordered nodes + edges)
       -> DynamoDB (PipelineStatusTable - same table, LINEAGE#-prefixed keys)

  <- ApiFunction (Lambda, HTTP API) reads DynamoDB, serves the dashboard
  <- dashboard/ (static HTML/JS, hash-routed, opened directly - no server)
```

One Lambda (`MonitorFunction`), two EventBridge schedules, mode-dispatched
at the top of `src/handler.py`'s `lambda_handler` (checked first, returns
immediately for lineage mode - the freshness-monitoring code below it is
unaffected by the second schedule existing). Not two Lambdas: lineage
discovery's AWS calls are a strict subset of what `MonitorFunction` already
has permission for, so a separate function/role/schedule/log-group would
only have duplicated infrastructure, not isolated anything a mode-check at
the top of one function doesn't already isolate just as well.

Every state machine in the account is discovered and monitored automatically,
with no dependency on `config/registry.yaml` — it's empty by default and the
application works fully without it. See the comment at the top of that file
for exactly what it's for (a trusted schedule, an exact output location to
check, alerting, owner/contact, or excluding a non-pipeline state machine).

No VPC, no NAT Gateway, no always-on compute. Cost at current scale (~30
state machines, 96 monitor runs/day + 1 lineage run/day, one Lambda) is
effectively $0 — inside AWS free tier.

## Lineage & Catalog

Maps what each pipeline actually calls - EventBridge trigger → Step Function
→ each Task's resource, in the order the state machine can reach them - and
exposes it as a browsable catalog (`#/lineage`, `#/lineage/catalog`,
`#/lineage/resource/:id`).

**Single table, not a second database.** `pipeline-freshness-status` has only
a partition key (`pipeline_name`), no sort key - DynamoDB can't add one to an
existing table without recreating it, which was off the table (see
`src/lineage_store.py`'s module docstring). Lineage items reuse that same
attribute with a namespaced value instead:

```
pipeline_name = "<real pipeline name>"          -> existing status item (unchanged)
pipeline_name = "LINEAGE#PIPELINE#<name>"       -> that pipeline's full graph (nodes+edges, one item)
pipeline_name = "LINEAGE#SUMMARY"               -> one rolling aggregate (counts, last run - overwritten each run)
```

Safe because AWS Step Functions names can never contain `#` (only letters,
digits, `-`, `_` are legal), so a `LINEAGE#...` value can never collide with
a real pipeline name. `status_store.get_all_statuses()` filters these out
explicitly, so they never leak into the Pipeline Monitor's `/status`
response. No new GSI: the only "list everything" access pattern
(`GET /resources`) uses a filtered Scan, the same access-pattern cost this
table already had for `get_all_statuses()` at this scale (~30 pipelines,
~100 resources) - a GSI would cost extra on every write to optimize a read
that isn't actually slow yet.

**Evidence, not guesses.** Every edge traces to a concrete fact, one of:
- An ASL Task's literal `Resource` field (a real per-resource ARN, or a
  Step Functions service-integration ARN like `arn:aws:states:::lambda:invoke`
  combined with a recovered `Parameters` value such as `FunctionName`).
- `trigger_scanner`'s already-existing EventBridge Rule/Scheduler detection.

Both are reported with `"confidence": "direct"`. Nothing is currently marked
`"inferred"` in this MVP (e.g. no Lambda-environment-variable scanning was
implemented) - see Limitations.

**Honest about what it can't resolve.** A service-integration Task whose
`Parameters` doesn't name a concrete target (e.g. `TableName` set to a
runtime `$.foo` reference) still gets a node - typed correctly, but labeled
`"<type> (target not resolved, via '<state name>')"` rather than a fabricated
name, and scoped to that exact pipeline+state so it never falsely merges with
an unrelated unresolved call elsewhere.

### Required IAM

None beyond what `MonitorFunction` already had before lineage existed - no
new permissions, no new role, nothing to add. Its existing grants happen to
be a superset of what lineage discovery needs:

```
states:ListStateMachines, states:DescribeStateMachine   (already had - freshness discovery reuses it too)
events:ListRuleNamesByTarget, events:DescribeRule        (already had - trigger detection reuses it too)
scheduler:ListSchedules, scheduler:GetSchedule           (already had)
dynamodb:PutItem  (already had, scoped to PipelineStatusTable only)
```

No `lambda:GetFunction`, `firehose:*`, `glue:*`, or `athena:*` permissions
are needed - every resource this scanner detects is classified directly from
the state machine's own ASL definition, already readable via
`DescribeStateMachine`, not from a separate describe call per resource.

### Limitations

- No SQL parsing: an Athena query is recorded as an Athena node, but the
  specific Glue table(s) its SQL text references are not resolved.
- No Lambda environment-variable or event-source-mapping scanning - a
  Lambda's *own* downstream calls (e.g. it internally publishes to SNS) are
  invisible unless that Lambda is itself another discovered Step Function.
- No CloudFormation-stack-based grouping (`states:ListTagsForResource` isn't
  called) - resources aren't linked by "deployed together," only by ASL
  call evidence.
- This repository contains no Terraform, so no Terraform-based relationship
  source was implemented.
- Discovery runs once a day - a pipeline's structure changes rarely, so this
  trades a day of latency on picking up a *new* pipeline's lineage for far
  fewer AWS calls than running it every 15 minutes alongside freshness.

Two independently-computed axes per pipeline, never conflated:
- **Execution health** (`execution_status`): did the Step Function itself run
  and succeed on schedule? `fresh | delayed | failed | stale | running | never_run | unknown`
- **Data freshness** (`data_status`): is the pipeline's *output* actually
  recent? `fresh | delayed | stale | unknown | not_configured`

## Running tests

```
py -m pytest -q
```

All AWS calls in tests are mocked (`botocore.stub.Stubber`) — no credentials
needed. `tests/test_registry.py` confirms the real `config/registry.yaml` is
empty by default and that the loader works identically whether the file is
empty, missing, or has override entries. `tests/test_handler.py` covers the
unified discovery loop, including that a state machine is never processed
twice and that a registry override is picked up correctly when present.

## Running the collector locally (read-only against real AWS)

```
$env:AWS_PROFILE = "haripriya.p"   # PowerShell
py scripts/run_local.py
```

A narrow spot-check tool, not the full monitor: it only prints execution +
data freshness for pipelines that have a `config/registry.yaml` override
entry (so it's only useful once you've added one), without writing anything
to AWS or DynamoDB. To see what the full monitor (every discovered state
machine, registry or not) would do, invoke `MonitorFunction` itself, e.g.
via `aws lambda invoke`.

## Deploying

```
sam build
sam deploy
```

Uses `samconfig.toml` (stack `pipeline-freshness-monitor`, `us-east-1`).
IAM for `states:ListStateMachines` / `DescribeStateMachine` / `ListExecutions`
/ `DescribeExecution` is account-wide (`Resource: "*"`) by necessity — every
state machine is discovered and processed the same way, so it can't be
scoped to a fixed ARN list the way a registry-driven policy could be. Every
action granted is a List/Describe/Get read; nothing can create, modify,
start, stop, or delete anything. `s3:GetObject` is the one exception that
stays least-privilege: no S3 permission is granted at all while
`config/registry.yaml` is empty, since nothing can produce a checkable
output location without a registry override (see below) — add a new
`s3:GetObject` statement scoped to the exact object's ARN in
`template.yaml`'s `Policies` block only when you configure one.

## Opening the dashboard

Open `dashboard/index.html` directly in a browser (double-click, or via a
file:// URL). No build step, no server. Hash-based routing
(`#/pipeline-monitor/<name>`) is deliberate: it works identically whether the
file is opened locally or later hosted as a static site, with no server-side
rewrite rules needed.

## Adding an optional registry override

Every state machine in the account already appears on the dashboard with no
setup — this section is only for *unlocking specific capabilities* for one
pipeline that automatic detection can't provide on its own: real
schedule-based Fresh/Delayed/Stale classification (instead of "Unknown"),
real S3 data-freshness checking (instead of "output source not identified"),
alerting, or owner/contact. Skip this entirely if a pipeline's execution
health and best-effort detected trigger/resources are enough.

Only add an override after confirming it's worth the trust you're putting in
it — a wrong schedule or output here can produce a false "stale"/"failed"
alert, which a purely automatic status can't.

1. Verify with read-only AWS calls: `describe-state-machine` (definition,
   IAM role), `list-tags-for-resource` (CloudFormation stack membership is
   strong evidence of an intentional deployment vs. a console experiment),
   `list-executions` (has it ever run, current status), and
   `events:list-rule-names-by-target` / `scheduler:list-schedules` for its
   real trigger.
2. Add an entry to `config/registry.yaml`:
   - `monitoring_enabled: true` once purpose is confirmed.
   - `alerting_enabled: false` for every newly-added pipeline, regardless of
     how broken or healthy it looks — only flip to `true` once ownership,
     schedule, and output are all verified and explicitly approved.
   - `schedule.type`: `hourly` / `daily` only for a *verified, simple, single*
     cron matching that cadence exactly. Anything irregular (bounded hours,
     monthly, annual, alternating gaps) is `custom`, with `interval_minutes`
     set only when you can honestly justify a single number — otherwise
     leave it `null` and record the raw verified cron in `cron_utc` instead
     of approximating.
   - `grace_period_minutes`: `60` for a verified daily schedule, `15` for a
     verified hourly schedule (existing precedents). Anything else is `null`
     ("requires configuration") — the freshness engine reports `unknown`
     for this, never `failed`/`stale`, so an unconfigured grace period can
     never look like a false alarm.
   - `output`: only set when you have a *verified single S3 object key* from
     a real execution's input/output — a bucket prefix alone isn't enough
     for the MVP's single-`HeadObject` checker. Leave `null` otherwise.
   - `owner` / `contact`: `null` (renders as "Not assigned") unless you have
     an actual name/Slack channel — never a guess.
   - `review_status: confirmed` once you're adding the override. A pipeline
     with no override entry already appears with `review_status: needs_review`
     from discovery — that's expected, not an error, and does not need to be
     "fixed" by adding one unless you actually want the capabilities above.
3. `states:ListExecutions`/`DescribeExecution` are already granted
   account-wide (see "Deploying" above) — nothing to add there. Only add to
   `template.yaml`'s IAM policy if you configured an `output`: a new
   `s3:GetObject` statement scoped to that exact object's ARN.
4. `sam build && sam deploy`, then verify live (`aws lambda invoke`, check
   the DynamoDB item, hit the API) before considering it done.

## Current scope / known limitations

- **Lineage & Catalog module**: implemented - see the "Lineage & Catalog"
  section above for its scope and limitations (no SQL parsing, no Lambda
  internals, no CFN-stack grouping).
- **Alerting**: not implemented yet. `alerting_enabled` is tracked per
  pipeline in the registry, ready for when it is.
- **API authentication**: the HTTP API is currently unauthenticated (read-only,
  no mutation risk, but does expose account ID/ARNs/bucket names to anyone
  with the URL). Deferred, not forgotten.
- **Data freshness** only supports a single S3 object key per pipeline —
  partitioned/prefixed outputs (Hive-style `year=/month=/day=`) aren't walked.
