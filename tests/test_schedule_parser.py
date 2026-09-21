from datetime import datetime, timedelta, timezone

from src.schedule_parser import compute_next_run, parse_cron_expression, parse_rate_expression

UTC = timezone.utc


def test_parse_rate_singular_and_plural_units():
    assert parse_rate_expression("rate(1 day)") == timedelta(days=1)
    assert parse_rate_expression("rate(5 minutes)") == timedelta(minutes=5)
    assert parse_rate_expression("rate(2 hours)") == timedelta(hours=2)
    assert parse_rate_expression("rate(1 hour)") == timedelta(hours=1)


def test_parse_rate_rejects_zero_and_malformed():
    assert parse_rate_expression("rate(0 minutes)") is None
    assert parse_rate_expression("rate(five minutes)") is None
    assert parse_rate_expression("not a rate at all") is None


def test_compute_next_run_for_rate_expression():
    after = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    next_run, reason = compute_next_run("rate(1 day)", after)
    assert next_run == datetime(2026, 1, 2, 10, 0, 0, tzinfo=UTC)
    assert reason is None


def test_compute_next_run_daily_cron():
    # cron(0 2 * * ? *) - every day at 02:00 UTC.
    after = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)  # already past 02:00 today
    next_run, reason = compute_next_run("cron(0 2 * * ? *)", after)
    assert next_run == datetime(2026, 1, 2, 2, 0, 0, tzinfo=UTC)
    assert reason is None


def test_compute_next_run_daily_cron_before_fire_time_same_day():
    after = datetime(2026, 1, 1, 0, 30, 0, tzinfo=UTC)  # before 02:00 today
    next_run, reason = compute_next_run("cron(0 2 * * ? *)", after)
    assert next_run == datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)


def test_compute_next_run_hourly_cron():
    # cron(15 * * * ? *) - fifteen minutes past every hour.
    after = datetime(2026, 1, 1, 10, 20, 0, tzinfo=UTC)
    next_run, reason = compute_next_run("cron(15 * * * ? *)", after)
    assert next_run == datetime(2026, 1, 1, 11, 15, 0, tzinfo=UTC)


def test_compute_next_run_monthly_cron_specific_day():
    # cron(0 9 1 * ? *) - 1st of every month at 09:00 UTC.
    after = datetime(2026, 1, 15, 0, 0, 0, tzinfo=UTC)
    next_run, reason = compute_next_run("cron(0 9 1 * ? *)", after)
    assert next_run == datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC)


def test_compute_next_run_weekday_named_range_cron():
    # cron(0 12 ? * MON-FRI *) - noon UTC on weekdays only. 2026-01-01 is a Thursday.
    after = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)  # Thursday, after noon
    next_run, reason = compute_next_run("cron(0 12 ? * MON-FRI *)", after)
    assert next_run == datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)  # Friday


def test_compute_next_run_weekday_cron_skips_weekend():
    # Friday after noon -> next weekday fire is Monday, not Saturday/Sunday.
    after = datetime(2026, 1, 2, 13, 0, 0, tzinfo=UTC)  # Friday, after noon
    next_run, reason = compute_next_run("cron(0 12 ? * MON-FRI *)", after)
    assert next_run == datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)  # Monday
    assert next_run.weekday() == 0  # Monday


def test_compute_next_run_month_name_cron():
    # cron(0 0 1 JAN ? *) - once a year, Jan 1st.
    after = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
    next_run, reason = compute_next_run("cron(0 0 1 JAN ? *)", after)
    assert next_run == datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_compute_next_run_step_cron():
    # cron(*/15 * * * ? *) - every 15 minutes.
    after = datetime(2026, 1, 1, 10, 7, 0, tzinfo=UTC)
    next_run, reason = compute_next_run("cron(*/15 * * * ? *)", after)
    assert next_run == datetime(2026, 1, 1, 10, 15, 0, tzinfo=UTC)


def test_compute_next_run_no_schedule_expression():
    next_run, reason = compute_next_run(None, datetime.now(UTC))
    assert next_run is None
    assert "No trigger was identified" in reason


def test_compute_next_run_unrecognized_expression():
    next_run, reason = compute_next_run("not a real schedule", datetime.now(UTC))
    assert next_run is None
    assert "not a recognized" in reason


def test_parse_cron_rejects_unsupported_special_characters():
    # "L" (last day of month) is real AWS cron syntax this parser doesn't
    # model - must be an honest "unsupported," never a guess.
    fields, reason = parse_cron_expression("cron(0 0 L * ? *)")
    assert fields is None
    assert "not supported" in reason


def test_parse_cron_rejects_wrong_field_count():
    fields, reason = parse_cron_expression("cron(0 0 * *)")
    assert fields is None
    assert "6 fields" in reason


def test_compute_next_run_result_is_always_strictly_after_the_reference_time():
    # Reference time itself exactly matches a fire time - result must still
    # be the *next* one, not the same instant.
    after = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
    next_run, _ = compute_next_run("cron(0 2 * * ? *)", after)
    assert next_run > after
    assert next_run == datetime(2026, 1, 2, 2, 0, 0, tzinfo=UTC)
