"""Validates the real config/registry.yaml against the rules established in
the 2026-09-16 discovery report - a regression guard against a future edit
silently breaking the alerting/monitoring composition this repo depends on.
"""
from src.registry import load_monitored_registry, load_registry

EXPECTED_PIPELINE_NAMES = {
    "athena_data_export",
    "syco_unload_and_hourly_refresh",
    "pulse_data_load",
    "audit_load_batch",
    "v2-lhg-dynamodb-export-s3",
    "connect_lake_load_batch",
    "ansible_data_load",
    "twilio_data_extract",
    "ringcentral_transcribe_job",
    "syco_incremental_export",
}


def test_registry_has_exactly_the_expected_ten_pipelines():
    pipelines = load_registry()
    assert {p.name for p in pipelines} == EXPECTED_PIPELINE_NAMES
    assert len(pipelines) == 10


def test_all_ten_are_monitored():
    assert len(load_monitored_registry()) == 10


def test_only_athena_data_export_has_alerting_enabled():
    pipelines = load_registry()
    alerting_on = {p.name for p in pipelines if p.alerting_enabled}
    assert alerting_on == {"athena_data_export"}


def test_all_pipelines_are_marked_confirmed():
    # No PENDING_REVIEW pipeline should ever be silently registered - the
    # discovery process keeps those out of registry.yaml entirely.
    for p in load_registry():
        assert p.review_status == "confirmed", p.name


def test_every_pipeline_has_a_real_state_machine_arn():
    for p in load_registry():
        assert p.state_machine_arn.startswith("arn:aws:states:"), p.name
        assert p.state_machine_arn.endswith(f":{p.name}"), p.name


def test_no_owner_or_contact_is_invented():
    # As of this registration pass, ownership metadata is genuinely
    # unavailable for every pipeline (verified via exhaustive tag
    # inspection) - the registry must reflect that honestly, not guess.
    for p in load_registry():
        assert p.owner is None, f"{p.name} has an owner but none was verified"


def test_grace_period_is_either_a_verified_rule_value_or_explicitly_unset():
    # Every grace period must be one we can justify: 60 (daily rule),
    # 15 (hourly rule), or None ("requires configuration"). Anything else
    # would mean a number got invented somewhere.
    allowed = {60, 15, 30, None}  # 30 is syco's pre-existing placeholder, documented as such
    for p in load_registry():
        assert p.grace_period_minutes in allowed, f"{p.name} has an unexplained grace period"


def test_new_pipelines_have_no_output_configured():
    # None of the 8 newly-registered pipelines have a verified single-object
    # S3 key - they must not have output configured, per "do not invent
    # output destinations".
    newly_added = EXPECTED_PIPELINE_NAMES - {"athena_data_export", "syco_unload_and_hourly_refresh"}
    for p in load_registry():
        if p.name in newly_added:
            assert p.output is None, f"{p.name} should not have an invented output"
