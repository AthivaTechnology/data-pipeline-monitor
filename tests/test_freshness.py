from datetime import datetime, timedelta, timezone

from src.freshness import FreshnessStatus, evaluate_freshness
from src.models import (
    ExecutionStatus,
    ExecutionSummary,
    PipelineConfig,
    PipelineExecutionState,
    ScheduleConfig,
)

ARN = "arn:aws:states:us-east-1:382625484581:stateMachine:test_pipeline"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _pipeline(schedule_type="hourly", grace_period_minutes=15, interval_minutes=None) -> PipelineConfig:
    return PipelineConfig(
        name="test_pipeline",
        state_machine_arn=ARN,
        region="us-east-1",
        environment="prod",
        monitoring_enabled=True,
        alerting_enabled=False,
        owner=None,
        schedule=ScheduleConfig(type=schedule_type, cron_utc=None, interval_minutes=interval_minutes),
        grace_period_minutes=grace_period_minutes,
        output=None,
    )


def _execution(status: ExecutionStatus, minutes_ago: int, error=None, cause=None) -> ExecutionSummary:
    start = NOW - timedelta(minutes=minutes_ago)
    stop = start + timedelta(seconds=30) if status != ExecutionStatus.RUNNING else None
    return ExecutionSummary(
        execution_arn=ARN + ":exec",
        name="exec",
        status=status,
        start_date=start,
        stop_date=stop,
        error=error,
        cause=cause,
    )


def _state(pipeline, latest_execution=None, latest_success=None, collector_error=None):
    return PipelineExecutionState(
        pipeline=pipeline,
        latest_execution=latest_execution,
        latest_successful_execution=latest_success,
        collector_error=collector_error,
    )


def test_collector_error_yields_unknown():
    state = _state(_pipeline(), collector_error="ThrottlingException")
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.UNKNOWN
    assert "monitor failed" in result.reason


def test_never_run():
    state = _state(_pipeline(), latest_execution=None, latest_success=None)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.NEVER_RUN


def test_running_takes_priority():
    running = _execution(ExecutionStatus.RUNNING, minutes_ago=2)
    state = _state(_pipeline(), latest_execution=running, latest_success=None)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.RUNNING


def test_latest_failed_reports_failed_even_with_older_success():
    failed = _execution(ExecutionStatus.FAILED, minutes_ago=5, error="States.TaskFailed", cause="boom")
    old_success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=65)
    state = _state(_pipeline(), latest_execution=failed, latest_success=old_success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.FAILED
    assert "States.TaskFailed" in result.reason


def test_timed_out_and_aborted_also_map_to_failed():
    for st in (ExecutionStatus.TIMED_OUT, ExecutionStatus.ABORTED):
        execution = _execution(st, minutes_ago=5)
        state = _state(_pipeline(), latest_execution=execution, latest_success=None)
        assert evaluate_freshness(state, NOW).status == FreshnessStatus.FAILED


def test_hourly_fresh_within_interval():
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=30)  # < 60 min interval
    state = _state(_pipeline(schedule_type="hourly", grace_period_minutes=15), latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.FRESH


def test_hourly_delayed_within_grace():
    # 70 min since success: past the 60 min interval, but within 60+15=75 min grace window
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=70)
    state = _state(_pipeline(schedule_type="hourly", grace_period_minutes=15), latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.DELAYED


def test_hourly_stale_past_grace():
    # 90 min since success: past 60+15=75 min grace window
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=90)
    state = _state(_pipeline(schedule_type="hourly", grace_period_minutes=15), latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.STALE


def test_daily_fresh_within_interval():
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=60 * 10)  # 10h < 24h
    state = _state(_pipeline(schedule_type="daily", grace_period_minutes=60), latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.FRESH


def test_daily_delayed_within_grace():
    # 24h30m since success: past 24h interval, within 24h+60min grace
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=60 * 24 + 30)
    state = _state(_pipeline(schedule_type="daily", grace_period_minutes=60), latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.DELAYED


def test_daily_stale_past_grace():
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=60 * 26)  # 26h, past 24h+60min grace
    state = _state(_pipeline(schedule_type="daily", grace_period_minutes=60), latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.STALE


def test_custom_interval_used_when_configured():
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=10)
    pipeline = _pipeline(schedule_type="custom", grace_period_minutes=5, interval_minutes=15)
    state = _state(pipeline, latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.FRESH


def test_custom_without_interval_is_unknown():
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=10)
    pipeline = _pipeline(schedule_type="custom", grace_period_minutes=5, interval_minutes=None)
    state = _state(pipeline, latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.UNKNOWN
    assert "schedule configuration" in result.reason


def test_unset_grace_period_is_unknown_not_stale():
    """A pipeline with a verified schedule but no grace-period rule applied
    yet (e.g. a monthly/bounded-hours/irregular cron) must report UNKNOWN,
    never STALE or FAILED - grace_period_minutes=None means "not configured",
    not "zero tolerance"."""
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=10)
    pipeline = _pipeline(schedule_type="daily", grace_period_minutes=None)
    state = _state(pipeline, latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.status == FreshnessStatus.UNKNOWN
    assert "grace period" in result.reason


def test_full_lifecycle_fresh_failed_recovered():
    """Simulates three consecutive monitor runs against the same pipeline:
    healthy -> fails -> a new execution succeeds. The engine is stateless
    (pure function of state + now), so "recovery" isn't a special code path -
    this proves that plugging in a fresh SUCCEEDED execution after a FAILED
    one cleanly produces FRESH again, with nothing left over from the
    failure. This is the scenario Slack recovery notifications will depend on.
    """
    pipeline = _pipeline(schedule_type="hourly", grace_period_minutes=15)

    # Tick 1: healthy
    success_1 = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=30)
    tick1 = evaluate_freshness(_state(pipeline, latest_execution=success_1, latest_success=success_1), NOW)
    assert tick1.status == FreshnessStatus.FRESH

    # Tick 2: the next scheduled run fails
    failed = _execution(ExecutionStatus.FAILED, minutes_ago=5, error="States.TaskFailed")
    tick2 = evaluate_freshness(
        _state(pipeline, latest_execution=failed, latest_success=success_1), NOW
    )
    assert tick2.status == FreshnessStatus.FAILED

    # Tick 3: a later run succeeds again - recovery, not stuck on FAILED
    success_2 = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=1)
    tick3 = evaluate_freshness(
        _state(pipeline, latest_execution=success_2, latest_success=success_2), NOW
    )
    assert tick3.status == FreshnessStatus.FRESH


def test_full_lifecycle_fresh_to_stale_when_runs_stop():
    """A pipeline that goes quiet (no new executions past its grace period)
    must transition from FRESH to STALE purely from elapsed time, without
    any new execution needed to trigger the change."""
    pipeline = _pipeline(schedule_type="hourly", grace_period_minutes=15)
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=30)
    state = _state(pipeline, latest_execution=success, latest_success=success)

    fresh_tick = evaluate_freshness(state, NOW)
    assert fresh_tick.status == FreshnessStatus.FRESH

    # Same execution data, but evaluated much later - nothing about the
    # pipeline changed, only elapsed time did.
    later = NOW + timedelta(hours=2)
    stale_tick = evaluate_freshness(state, later)
    assert stale_tick.status == FreshnessStatus.STALE


def test_evaluation_is_idempotent():
    """Running the same evaluation twice with identical inputs must produce
    an identical result - the engine must have no hidden state or
    side-effects that could cause a monitor run to silently overwrite a
    correct status with an incorrect one."""
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=30)
    state = _state(_pipeline(schedule_type="hourly", grace_period_minutes=15), latest_execution=success, latest_success=success)

    first = evaluate_freshness(state, NOW)
    second = evaluate_freshness(state, NOW)
    assert first.status == second.status == FreshnessStatus.FRESH
    assert first.reason == second.reason
    assert first.expected_next_run == second.expected_next_run


def test_expected_next_run_and_stale_deadline_are_computed():
    success = _execution(ExecutionStatus.SUCCEEDED, minutes_ago=30)
    pipeline = _pipeline(schedule_type="hourly", grace_period_minutes=15)
    state = _state(pipeline, latest_execution=success, latest_success=success)
    result = evaluate_freshness(state, NOW)
    assert result.expected_next_run == success.stop_date + timedelta(minutes=60)
    assert result.stale_deadline == success.stop_date + timedelta(minutes=75)
