# Data Reliability Platform — Pipeline Freshness Monitor

Monitors AWS Step Functions data pipelines for execution health and output
data freshness, and shows the result in a small internal dashboard. Read-only
against AWS: it never starts, stops, or modifies any pipeline or trigger.

## Architecture

```
EventBridge (rate(15 minutes))
  -> MonitorFunction (Lambda)
       -> Step Functions API (list_executions / describe_execution)
       -> freshness engine (pure logic, no AWS calls)
       -> S3 API (optional, per-pipeline output check)
       -> DynamoDB (PipelineStatusTable)
  <- ApiFunction (Lambda, HTTP API) reads DynamoDB, serves the dashboard
  <- dashboard/ (static HTML/JS, hash-routed, opened directly - no server)
```

No VPC, no NAT Gateway, no always-on compute. Cost at current scale (10
pipelines, 96 monitor runs/day) is effectively $0 — inside AWS free tier.

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
needed. `tests/test_registry.py` validates the real `config/registry.yaml`
composition (pipeline count, alerting flags, no invented owners) as a
regression guard.

## Running the collector locally (read-only against real AWS)

```
$env:AWS_PROFILE = "haripriya.p"   # PowerShell
py scripts/run_local.py
```

Prints execution + data freshness for every pipeline in the registry,
without writing anything to AWS or DynamoDB.

## Deploying

```
sam build
sam deploy
```

Uses `samconfig.toml` (stack `pipeline-freshness-monitor`, `us-east-1`).
IAM is scoped per-pipeline: adding a state machine to the registry means
adding its ARN to `template.yaml`'s `ReadRegisteredStepFunctions` /
`ReadRegisteredStepFunctionExecutions` policy statements too — it is
intentionally never wildcarded across all Step Functions in the account.

## Opening the dashboard

Open `dashboard/index.html` directly in a browser (double-click, or via a
file:// URL). No build step, no server. Hash-based routing
(`#/pipeline-monitor/<name>`) is deliberate: it works identically whether the
file is opened locally or later hosted as a static site, with no server-side
rewrite rules needed.

## Registering a new pipeline

Only register a state machine after confirming it's a real data pipeline —
see the discovery process below. Do not guess.

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
   - `review_status: confirmed` once you're registering it. Pipelines still
     under investigation are kept **out** of this file entirely rather than
     half-registered — see the discovery report in project history for the
     current PENDING_REVIEW list.
3. Add its ARN to `template.yaml`'s IAM policy (both `ListExecutions` and
   `DescribeExecution` statements, and `s3:GetObject` on the exact key only
   if you configured an `output`).
4. `sam build && sam deploy`, then verify live (`aws lambda invoke`, check
   the DynamoDB item, hit the API) before considering it done.

## Current scope / known limitations

- **Lineage & Catalog module**: frozen. Frontend shell exists
  (`#/lineage`, `#/lineage/catalog`, `#/lineage/resource/:id`) with no fake
  data — clearly labeled "coming in next phase" until a real backend exists.
- **Alerting**: not implemented yet. `alerting_enabled` is tracked per
  pipeline in the registry, ready for when it is.
- **API authentication**: the HTTP API is currently unauthenticated (read-only,
  no mutation risk, but does expose account ID/ARNs/bucket names to anyone
  with the URL). Deferred, not forgotten.
- **Data freshness** only supports a single S3 object key per pipeline —
  partitioned/prefixed outputs (Hive-style `year=/month=/day=`) aren't walked.
