from typing import Any

import pytest
from pydantic import ValidationError

from garmin_mcp.workouts import METERS_PER_MILE, SPORTS, Step, build_workout


def steps(*specs: dict[str, Any]) -> list[Step]:
    return [Step.model_validate(s) for s in specs]


def segment_steps(workout: dict[str, Any]) -> list[dict[str, Any]]:
    return workout["workoutSegments"][0]["workoutSteps"]


INTERVALS = steps(
    {"type": "warmup", "duration_s": 600, "target": {"kind": "hr_zone", "zone": 2}},
    {
        "type": "repeat",
        "iterations": 5,
        "steps": [
            {
                "type": "interval",
                "distance_m": 1000,
                "target": {"kind": "pace", "low": "4:30", "high": "4:45"},
            },
            {"type": "recovery", "duration_s": 120},
        ],
    },
    {"type": "interval", "duration_s": 300, "target": {"kind": "power", "low": 280, "high": 250}},
    {"type": "cooldown"},
)


def test_step_order_is_sequential_including_nested_repeats():
    seg = segment_steps(build_workout("running", "5x1k", INTERVALS))
    repeat = seg[1]
    ordered = [seg[0], repeat, *repeat["workoutSteps"], seg[2], seg[3]]
    assert [s["stepOrder"] for s in ordered] == [1, 2, 3, 4, 5, 6]


def test_repeat_group_shape():
    repeat = segment_steps(build_workout("running", "5x1k", INTERVALS))[1]
    assert repeat["type"] == "RepeatGroupDTO"
    assert repeat["numberOfIterations"] == 5
    assert repeat["endCondition"]["conditionTypeKey"] == "iterations"
    assert repeat["endConditionValue"] == 5.0


def test_pace_target_converts_to_mps_slow_first():
    interval = segment_steps(build_workout("running", "5x1k", INTERVALS))[1]["workoutSteps"][0]
    assert interval["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert interval["targetValueOne"] == pytest.approx(1000 / 285)  # 4:45/km
    assert interval["targetValueTwo"] == pytest.approx(1000 / 270)  # 4:30/km


def test_pace_per_mile():
    (step,) = steps(
        {
            "type": "interval",
            "duration_s": 60,
            "target": {"kind": "pace", "low": "8:00", "high": "7:30", "unit": "mile"},
        }
    )
    s = segment_steps(build_workout("running", "mile pace", [step]))[0]
    assert s["targetValueOne"] == pytest.approx(METERS_PER_MILE / 480)
    assert s["targetValueTwo"] == pytest.approx(METERS_PER_MILE / 450)


def test_speed_target_kmh_to_mps():
    (step,) = steps(
        {"type": "interval", "duration_s": 60, "target": {"kind": "speed", "low": 36, "high": 30}}
    )
    s = segment_steps(build_workout("cycling", "speed", [step]))[0]
    assert s["targetType"]["workoutTargetTypeKey"] == "speed.zone"
    assert s["targetValueOne"] == pytest.approx(30 / 3.6)
    assert s["targetValueTwo"] == pytest.approx(10.0)


def test_zone_and_range_targets():
    seg = segment_steps(build_workout("running", "5x1k", INTERVALS))
    assert seg[0]["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert seg[0]["zoneNumber"] == 2
    assert "targetValueOne" not in seg[0]
    assert seg[2]["targetType"]["workoutTargetTypeKey"] == "power.zone"
    assert (seg[2]["targetValueOne"], seg[2]["targetValueTwo"]) == (250, 280)


def test_end_conditions():
    seg = segment_steps(build_workout("running", "5x1k", INTERVALS))
    assert seg[0]["endCondition"]["conditionTypeKey"] == "time"
    assert seg[1]["workoutSteps"][0]["endCondition"]["conditionTypeKey"] == "distance"
    assert seg[3]["endCondition"]["conditionTypeKey"] == "lap.button"
    assert "endConditionValue" not in seg[3]
    assert seg[3]["targetType"]["workoutTargetTypeKey"] == "no.target"


def test_estimated_duration_uses_pace_for_distance_steps():
    workout = build_workout("running", "5x1k", INTERVALS)
    per_rep = 1000 / ((1000 / 285 + 1000 / 270) / 2) + 120
    assert workout["estimatedDurationInSecs"] == int(600 + 5 * per_rep + 300)


@pytest.mark.parametrize("sport", sorted(SPORTS))
def test_every_sport_builds(sport):
    workout = build_workout(sport, f"{sport} test", steps({"type": "interval", "duration_s": 60}))
    assert workout["sportType"]["sportTypeKey"] == sport
    assert workout["workoutSegments"][0]["sportType"] == workout["sportType"]


def test_descriptions_are_passed_through():
    workout = build_workout(
        "running",
        "x",
        steps({"type": "interval", "duration_s": 60, "description": "relax"}),
        description="notes",
    )
    assert workout["description"] == "notes"
    assert segment_steps(workout)[0]["description"] == "relax"


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "repeat", "steps": []},
        {"type": "repeat", "iterations": 3},
        {"type": "repeat", "iterations": 2, "duration_s": 5, "steps": [{"type": "interval"}]},
        {"type": "interval", "duration_s": 5, "distance_m": 5},
        {"type": "interval", "steps": [{"type": "interval"}]},
        {"type": "interval", "target": {"kind": "pace", "low": "4:00"}},
        {"type": "interval", "target": {"kind": "hr_zone"}},
        {"type": "interval", "duration_s": -1},
        {"type": "sprint"},
    ],
)
def test_invalid_steps_are_rejected(spec):
    with pytest.raises(ValidationError):
        Step.model_validate(spec)


def test_empty_workout_is_rejected():
    with pytest.raises(ValueError):
        build_workout("running", "empty", [])


def test_pacepro_splits_fold_short_remainder_and_hit_goal():
    from garmin_mcp.server import METERS_PER_MILE, build_pacepro_splits

    splits = build_pacepro_splits(5000, 1490, METERS_PER_MILE, 4)
    assert [round(s["splitDistance"]) for s in splits] == [1609, 1609, 1781]
    assert splits[-1]["cumulativeTimeFromStart"] == pytest.approx(1490)
    assert splits[-1]["cumulativeDistanceFromStart"] == pytest.approx(5000)
    speeds = [s["splitAvgSpeed"] for s in splits]
    assert speeds == sorted(speeds)  # positive change = negative split


def test_pacepro_splits_reject_bad_input():
    from garmin_mcp.server import build_pacepro_splits

    with pytest.raises(ValueError):
        build_pacepro_splits(0, 1490, 1000, 0)
    with pytest.raises(ValueError):
        build_pacepro_splits(5000, 1490, 1000, 400)
