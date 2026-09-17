"""Validates that config/registry.yaml is genuinely optional: empty by
default, and the loader works the same whether the file is empty, absent,
or has entries. AWS Step Functions discovery - not this file - is the
single source of truth for which pipelines exist; see test_handler.py for
the unified discovery loop this file's entries (if any) get looked up into.
"""
from pathlib import Path

import pytest

from src.registry import load_excluded_names, load_monitored_registry, load_registry


def test_real_registry_is_empty_by_default():
    assert load_registry() == []
    assert load_monitored_registry() == []
    assert load_excluded_names() == set()


def test_missing_file_behaves_exactly_like_an_empty_one(tmp_path):
    missing_path = tmp_path / "does-not-exist.yaml"
    assert load_registry(missing_path) == []
    assert load_monitored_registry(missing_path) == []
    assert load_excluded_names(missing_path) == set()


def test_an_entry_added_as_an_override_still_loads_correctly(tmp_path):
    # Confirms the "optional per-ARN override" path still works for anyone
    # who does want to unlock a trusted schedule/output/alerting for one
    # pipeline - this file being empty by default doesn't mean the
    # mechanism was removed, only that nothing depends on it.
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
pipelines:
  - name: example_pipeline
    state_machine_arn: "arn:aws:states:us-east-1:123456789012:stateMachine:example_pipeline"
    region: us-east-1
    environment: prod
    alerting_enabled: true
    schedule:
      type: daily
      cron_utc: "0 1 * * ? *"
    grace_period_minutes: 60
    output:
      type: s3
      bucket: my-bucket
      key: my/key.csv
excluded:
  - some-internal-test-stub
""",
        encoding="utf-8",
    )

    pipelines = load_registry(path)
    assert len(pipelines) == 1
    assert pipelines[0].name == "example_pipeline"
    assert pipelines[0].alerting_enabled is True
    assert load_excluded_names(path) == {"some-internal-test-stub"}


def test_malformed_yaml_degrades_to_empty_instead_of_raising(tmp_path):
    # A syntax error here must never crash the whole monitor run (every
    # pipeline, not just a mis-configured row) - both handler.py's and
    # lineage_handler.py's lambda_handler call these before their
    # per-pipeline try/except blocks even start.
    path = tmp_path / "registry.yaml"
    path.write_text("pipelines: [this is not valid: yaml: at all: {{{", encoding="utf-8")

    assert load_registry(path) == []
    assert load_monitored_registry(path) == []
    assert load_excluded_names(path) == set()


def test_malformed_yaml_logs_a_warning(tmp_path, caplog):
    # Distinct from the missing-file case (silent, expected) - a syntax
    # error in a file someone deliberately edited is worth surfacing in
    # CloudWatch, even though the monitor keeps running.
    path = tmp_path / "registry.yaml"
    path.write_text("pipelines: [this is not valid: yaml: at all: {{{", encoding="utf-8")

    with caplog.at_level("WARNING"):
        load_registry(path)

    assert any("not valid YAML" in r.message for r in caplog.records)
