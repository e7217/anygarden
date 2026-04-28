"""Pure-function unit tests for ``doorae.goals.policy`` (#302)."""

from datetime import datetime, timezone

import pytest

from doorae.goals.policy import (
    GOAL_FAILURE_PAUSE_THRESHOLD,
    InvalidTriggerConfig,
    MaterializeDecision,
    apply_completion_to_failure_counter,
    compute_next_run_at,
    materialize_decision,
    validate_trigger_config,
)


class TestValidateTriggerConfig:
    def test_cron_daily_9am_accepted(self):
        validate_trigger_config("cron", {"cron": "0 9 * * *"})

    def test_cron_every_minute_accepted_at_floor(self):
        # Standard cron's minimum granularity IS 1 minute, so "* * * * *"
        # sits exactly at our 60s floor (allowed). Sub-minute cron is
        # only possible via 6-field expressions some implementations
        # support — those would be caught by the gap check.
        validate_trigger_config("cron", {"cron": "* * * * *"})

    def test_cron_every_two_minutes_accepted(self):
        validate_trigger_config("cron", {"cron": "*/2 * * * *"})

    def test_cron_invalid_expression_rejected(self):
        with pytest.raises(InvalidTriggerConfig, match="invalid cron"):
            validate_trigger_config("cron", {"cron": "not a cron"})

    def test_cron_missing_key_rejected(self):
        with pytest.raises(InvalidTriggerConfig):
            validate_trigger_config("cron", {})

    def test_interval_60s_accepted(self):
        validate_trigger_config("interval", {"interval_seconds": 60})

    def test_interval_below_minimum_rejected(self):
        with pytest.raises(InvalidTriggerConfig, match=">= 60"):
            validate_trigger_config("interval", {"interval_seconds": 30})

    def test_interval_non_integer_rejected(self):
        with pytest.raises(InvalidTriggerConfig):
            validate_trigger_config("interval", {"interval_seconds": "60"})

    def test_manual_no_config_required(self):
        validate_trigger_config("manual", {})

    def test_unknown_trigger_type_rejected(self):
        with pytest.raises(InvalidTriggerConfig, match="unknown"):
            validate_trigger_config("webhook", {})


class TestComputeNextRunAt:
    def test_cron_returns_next_fire(self):
        # 09:00 UTC daily — from 08:00 UTC the next fire is 09:00 today.
        now = datetime(2026, 4, 28, 8, 0, tzinfo=timezone.utc)
        nxt = compute_next_run_at("cron", {"cron": "0 9 * * *"}, after=now)
        assert nxt == datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc)

    def test_interval_adds_seconds(self):
        now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
        nxt = compute_next_run_at("interval", {"interval_seconds": 600}, after=now)
        assert nxt == datetime(2026, 4, 28, 12, 10, tzinfo=timezone.utc)

    def test_manual_returns_none(self):
        now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
        assert compute_next_run_at("manual", {}, after=now) is None


class TestMaterializeDecision:
    def test_full_keeps_silent_success(self):
        assert (
            materialize_decision(
                materialize="full",
                final_status="done",
                is_interesting=False,
            )
            is MaterializeDecision.KEEP
        )

    def test_full_keeps_failure(self):
        assert (
            materialize_decision(
                materialize="full",
                final_status="failed",
                is_interesting=False,
            )
            is MaterializeDecision.KEEP
        )

    def test_interesting_only_keeps_failure(self):
        assert (
            materialize_decision(
                materialize="interesting_only",
                final_status="failed",
                is_interesting=False,
            )
            is MaterializeDecision.KEEP
        )

    def test_interesting_only_keeps_interesting_success(self):
        assert (
            materialize_decision(
                materialize="interesting_only",
                final_status="done",
                is_interesting=True,
            )
            is MaterializeDecision.KEEP
        )

    def test_interesting_only_drops_silent_success(self):
        assert (
            materialize_decision(
                materialize="interesting_only",
                final_status="done",
                is_interesting=False,
            )
            is MaterializeDecision.DELETE
        )


class TestFailureCounter:
    def test_success_resets_counter(self):
        u = apply_completion_to_failure_counter(current=2, final_status="done")
        assert u.new_count == 0
        assert u.pause is False

    def test_failure_increments_counter(self):
        u = apply_completion_to_failure_counter(current=0, final_status="failed")
        assert u.new_count == 1
        assert u.pause is False

    def test_threshold_crossing_flags_pause(self):
        threshold = GOAL_FAILURE_PAUSE_THRESHOLD
        u = apply_completion_to_failure_counter(
            current=threshold - 1, final_status="failed"
        )
        assert u.new_count == threshold
        assert u.pause is True
