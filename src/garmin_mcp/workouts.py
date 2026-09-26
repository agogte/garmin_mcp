"""Translate a compact step spec into Garmin Connect workout JSON."""

from __future__ import annotations

from typing import Any, Literal

from garminconnect.workout import (
    ConditionType,
    CyclingWorkout,
    ExecutableStep,
    HikingWorkout,
    RepeatGroup,
    RunningWorkout,
    StepType,
    SwimmingWorkout,
    TargetType,
    WalkingWorkout,
    WorkoutSegment,
)
from pydantic import BaseModel, Field, model_validator

METERS_PER_MILE = 1609.344

SPORTS = {
    "running": RunningWorkout,
    "cycling": CyclingWorkout,
    "swimming": SwimmingWorkout,
    "walking": WalkingWorkout,
    "hiking": HikingWorkout,
}
Sport = Literal["running", "cycling", "swimming", "walking", "hiking"]

_STEP_TYPES = {
    "warmup": (StepType.WARMUP, 1),
    "cooldown": (StepType.COOLDOWN, 2),
    "interval": (StepType.INTERVAL, 3),
    "recovery": (StepType.RECOVERY, 4),
    "rest": (StepType.REST, 5),
    "repeat": (StepType.REPEAT, 6),
}

_TARGET_TYPES = {
    "none": (TargetType.NO_TARGET, "no.target", 1),
    "power": (TargetType.POWER_ZONE, "power.zone", 2),
    "cadence": (TargetType.CADENCE, "cadence", 3),
    "heart_rate": (TargetType.HEART_RATE_ZONE, "heart.rate.zone", 4),
    "speed": (TargetType.SPEED_ZONE, "speed.zone", 5),
    "pace": (TargetType.PACE_ZONE, "pace.zone", 6),
}


class Target(BaseModel):
    """Intensity target for a step."""

    kind: Literal["pace", "speed", "heart_rate", "hr_zone", "power", "power_zone", "cadence"] = Field(
        description=(
            "pace: low/high as 'm:ss' per unit (km or mile). "
            "speed: low/high in km/h (or mph with unit='mile'). "
            "heart_rate: low/high bpm. hr_zone: zone 1-5. "
            "power: low/high watts. power_zone: zone 1-7. "
            "cadence: low/high steps or revs per minute."
        )
    )
    low: float | str | None = None
    high: float | str | None = None
    zone: int | None = Field(default=None, ge=1, le=10)
    unit: Literal["km", "mile"] = "km"

    @model_validator(mode="after")
    def _check(self) -> Target:
        if self.kind in ("hr_zone", "power_zone"):
            if self.zone is None:
                raise ValueError(f"{self.kind} target requires 'zone'")
        elif self.low is None or self.high is None:
            raise ValueError(f"{self.kind} target requires 'low' and 'high'")
        return self


class Step(BaseModel):
    """One workout step. Ends after duration_s, distance_m, or (if neither) the lap button."""

    type: Literal["warmup", "interval", "recovery", "rest", "cooldown", "repeat"]
    duration_s: float | None = Field(default=None, gt=0)
    distance_m: float | None = Field(default=None, gt=0)
    target: Target | None = None
    description: str | None = None
    iterations: int | None = Field(default=None, ge=1, description="repeat only")
    steps: list[Step] | None = Field(default=None, description="repeat only: steps to repeat")

    @model_validator(mode="after")
    def _check(self) -> Step:
        if self.type == "repeat":
            if not self.iterations or not self.steps:
                raise ValueError("repeat step requires 'iterations' and non-empty 'steps'")
            if self.duration_s or self.distance_m or self.target:
                raise ValueError("repeat step takes only 'iterations' and 'steps'")
        else:
            if self.steps or self.iterations:
                raise ValueError(f"{self.type} step cannot have 'steps'/'iterations'")
            if self.duration_s and self.distance_m:
                raise ValueError("use either duration_s or distance_m, not both")
        return self


def _parse_pace(value: float | str) -> float:
    """'m:ss' (or seconds as a number) -> seconds per unit distance."""
    if isinstance(value, str):
        minutes, _, seconds = value.strip().partition(":")
        return int(minutes) * 60 + float(seconds or 0)
    return float(value)


def _pace_to_mps(pace: float | str, unit: str) -> float:
    meters = METERS_PER_MILE if unit == "mile" else 1000.0
    return meters / _parse_pace(pace)


def _bounds(target: Target) -> tuple[float | str, float | str]:
    """low/high, which Target's validator guarantees for range targets."""
    if target.low is None or target.high is None:
        raise ValueError(f"{target.kind} target requires 'low' and 'high'")
    return target.low, target.high


def _repeat_parts(step: Step) -> tuple[int, list[Step]]:
    """iterations/steps, which Step's validator guarantees for repeat steps."""
    if not step.iterations or not step.steps:
        raise ValueError("repeat step requires 'iterations' and non-empty 'steps'")
    return step.iterations, step.steps


def _target_fields(target: Target | None) -> dict[str, Any]:
    kind = "none" if target is None else target.kind
    kind = {"hr_zone": "heart_rate", "power_zone": "power"}.get(kind, kind)
    type_id, key, order = _TARGET_TYPES[kind]
    fields: dict[str, Any] = {
        "targetType": {"workoutTargetTypeId": type_id, "workoutTargetTypeKey": key, "displayOrder": order}
    }
    if target is None:
        return fields
    if target.zone is not None and target.kind in ("hr_zone", "power_zone"):
        fields["zoneNumber"] = target.zone
        return fields
    low, high = _bounds(target)
    if target.kind == "pace":
        values = [_pace_to_mps(low, target.unit), _pace_to_mps(high, target.unit)]
    elif target.kind == "speed":
        factor = (METERS_PER_MILE if target.unit == "mile" else 1000.0) / 3600.0
        values = [float(low) * factor, float(high) * factor]
    else:
        values = [float(low), float(high)]
    # Garmin expects the lower bound first (for pace: the slower speed in m/s).
    fields["targetValueOne"], fields["targetValueTwo"] = sorted(values)
    return fields


def _end_condition(step: Step) -> tuple[dict[str, Any], float | None]:
    if step.duration_s:
        return {
            "conditionTypeId": ConditionType.TIME,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        }, step.duration_s
    if step.distance_m:
        return {
            "conditionTypeId": ConditionType.DISTANCE,
            "conditionTypeKey": "distance",
            "displayOrder": 3,
            "displayable": True,
        }, step.distance_m
    return {
        "conditionTypeId": ConditionType.LAP_BUTTON,
        "conditionTypeKey": "lap.button",
        "displayOrder": 1,
        "displayable": True,
    }, None


def _estimate_seconds(step: Step) -> float:
    if step.type == "repeat":
        iterations, children = _repeat_parts(step)
        return iterations * sum(_estimate_seconds(s) for s in children)
    if step.duration_s:
        return step.duration_s
    if step.distance_m and step.target and step.target.kind == "pace":
        low, high = _bounds(step.target)
        mid_mps = (_pace_to_mps(low, step.target.unit) + _pace_to_mps(high, step.target.unit)) / 2
        return step.distance_m / mid_mps
    return 0.0


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _build_step(step: Step, order: _Counter) -> ExecutableStep | RepeatGroup:
    type_id, display = _STEP_TYPES[step.type]
    step_type = {"stepTypeId": type_id, "stepTypeKey": step.type, "displayOrder": display}
    step_order = order.next()
    if step.type == "repeat":
        iterations, children = _repeat_parts(step)
        return RepeatGroup(
            stepOrder=step_order,
            stepType=step_type,
            numberOfIterations=iterations,
            workoutSteps=[_build_step(s, order) for s in children],
            endCondition={
                "conditionTypeId": ConditionType.ITERATIONS,
                "conditionTypeKey": "iterations",
                "displayOrder": 7,
                "displayable": False,
            },
            endConditionValue=float(iterations),
        )
    end_condition, end_value = _end_condition(step)
    extra = _target_fields(step.target)
    if step.description:
        extra["description"] = step.description
    return ExecutableStep(
        stepOrder=step_order,
        stepType=step_type,
        endCondition=end_condition,
        endConditionValue=end_value,
        **extra,
    )


def build_workout(
    sport: Sport, name: str, steps: list[Step], description: str | None = None
) -> dict[str, Any]:
    """Return the JSON payload for Garmin's workout-service upload endpoint."""
    if not steps:
        raise ValueError("a workout needs at least one step")
    workout_cls = SPORTS[sport]
    sport_type = workout_cls.model_fields["sportType"].default_factory()
    order = _Counter()
    workout = workout_cls(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=int(sum(_estimate_seconds(s) for s in steps)),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType=sport_type,
                workoutSteps=[_build_step(s, order) for s in steps],
            )
        ],
    )
    return workout.to_dict()
