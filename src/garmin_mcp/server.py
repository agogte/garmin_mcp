"""Garmin Connect MCP server (stdio, local only).

Every tool returns structured JSON. Credentials never pass through this
process: it only resumes the session tokens created by ``garmin-mcp-login``.
"""

import datetime as dt
import logging
import sys
from collections.abc import Callable
from typing import Any, Literal

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
    parse_activity_detail_metrics,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .auth import LOGIN_HINT, NotLoggedIn, get_client, reset_client
from .workouts import Sport, Step, build_workout

# stdout is the MCP channel; keep logs on stderr and quiet enough that nothing
# auth-related is ever printed.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

mcp = MCPServer(
    "garmin",
    instructions=(
        "Tools for the user's Garmin Connect account. Read tools return raw-ish JSON "
        "for your own analysis (distances in meters, durations in seconds, speeds in m/s). "
        "Write tools create/schedule/delete workouts in the user's Garmin library and "
        "calendar; confirm with the user before deleting anything."
    ),
)


def _call[T](fn: Callable[[], T]) -> T:
    """Run a Garmin call, turning expected failures into messages the model can read."""
    try:
        return fn()
    except NotLoggedIn as e:
        raise ToolError(str(e)) from e
    except GarminConnectAuthenticationError as e:
        reset_client()
        raise ToolError(f"Garmin session rejected. {LOGIN_HINT}") from e
    except GarminConnectTooManyRequestsError as e:
        raise ToolError("Garmin rate limit hit (429). Wait a few minutes and retry.") from e
    except GarminConnectNotFoundError as e:
        raise ToolError(f"Not found: {e}") from e
    except (GarminConnectConnectionError, ValueError) as e:
        raise ToolError(str(e)) from e


def _sections(**fetchers: Callable[[], Any]) -> dict[str, Any]:
    """Fetch several independent sections; one failing doesn't sink the rest."""
    out: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, fetch in fetchers.items():
        try:
            out[name] = _call(fetch)
        except ToolError as e:
            if "Garmin session" in str(e) or "No valid Garmin session" in str(e):
                raise
            errors[name] = str(e)
    if errors:
        out["errors"] = errors
    return out


def _today() -> str:
    return dt.date.today().isoformat()


def _pace_s_per_km(speed_mps: float | None) -> float | None:
    return round(1000 / speed_mps, 1) if speed_mps else None


def _activity_summary(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "activity_id": a.get("activityId"),
        "name": a.get("activityName"),
        "type": (a.get("activityType") or {}).get("typeKey"),
        "start_local": a.get("startTimeLocal"),
        "duration_s": a.get("duration"),
        "moving_duration_s": a.get("movingDuration"),
        "distance_m": a.get("distance"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "avg_speed_mps": a.get("averageSpeed"),
        "avg_pace_s_per_km": _pace_s_per_km(a.get("averageSpeed")),
        "avg_power_w": a.get("avgPower"),
        "normalized_power_w": a.get("normPower"),
        "avg_cadence": a.get("averageRunningCadenceInStepsPerMinute")
        or a.get("averageBikingCadenceInRevPerMinute"),
        "elevation_gain_m": a.get("elevationGain"),
        "calories": a.get("calories"),
        "training_load": a.get("activityTrainingLoad"),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "vo2max": a.get("vO2MaxValue"),
    }


# ---------------------------------------------------------------- read tools


@mcp.tool()
def list_activities(
    limit: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    activity_type: str | None = None,
) -> list[dict[str, Any]]:
    """List recent activities (newest first) with key summary metrics.

    Dates are YYYY-MM-DD. activity_type examples: running, cycling, swimming,
    walking, hiking, strength_training, multi_sport.
    """
    client = _call(get_client)
    if start_date:
        activities = _call(lambda: client.get_activities_by_date(start_date, end_date, activity_type))
    else:
        activities = _call(lambda: client.get_activities(0, limit, activity_type))
    if not isinstance(activities, list):
        return []
    return [_activity_summary(a) for a in activities[:limit]]


@mcp.tool()
def get_activity_detail(
    activity_id: int, include_samples: bool = False, max_samples: int = 500
) -> dict[str, Any]:
    """Full metrics for one activity: summary, laps/splits, HR and power time-in-zone.

    include_samples adds a time series (HR, pace/speed, power, cadence, elevation, ...)
    downsampled to at most max_samples points.
    """
    client = _call(get_client)
    aid = str(activity_id)
    fetchers: dict[str, Callable[[], Any]] = {
        "summary": lambda: client.get_activity(aid),
        "splits": lambda: client.get_activity_splits(aid),
        "hr_time_in_zones": lambda: client.get_activity_hr_in_timezones(aid),
        "power_time_in_zones": lambda: client.get_activity_power_in_timezones(aid),
    }
    if include_samples:
        fetchers["samples"] = lambda: parse_activity_detail_metrics(
            client.get_activity_details(aid, maxchart=max_samples, maxpoly=0)
        )
    return _sections(**fetchers)


@mcp.tool()
def get_daily_summary(date: str | None = None, include_raw: bool = False) -> dict[str, Any]:
    """Daily wellness snapshot for a date (YYYY-MM-DD, default today):
    steps, resting HR, stress, body battery, intensity minutes, sleep."""
    client = _call(get_client)
    day = date or _today()
    data = _sections(
        summary=lambda: client.get_user_summary(day),
        sleep=lambda: client.get_sleep_data(day),
    )
    s = data.get("summary") or {}
    sleep = (data.get("sleep") or {}).get("dailySleepDTO") or {}
    result: dict[str, Any] = {
        "date": day,
        "steps": s.get("totalSteps"),
        "resting_hr": s.get("restingHeartRate"),
        "min_hr": s.get("minHeartRate"),
        "max_hr": s.get("maxHeartRate"),
        "avg_stress": s.get("averageStressLevel"),
        "max_stress": s.get("maxStressLevel"),
        "body_battery": {
            "most_recent": s.get("bodyBatteryMostRecentValue"),
            "high": s.get("bodyBatteryHighestValue"),
            "low": s.get("bodyBatteryLowestValue"),
            "charged": s.get("bodyBatteryChargedValue"),
            "drained": s.get("bodyBatteryDrainedValue"),
        },
        "intensity_minutes": {
            "moderate": s.get("moderateIntensityMinutes"),
            "vigorous": s.get("vigorousIntensityMinutes"),
        },
        "calories": {"total": s.get("totalKilocalories"), "active": s.get("activeKilocalories")},
        "sleep": {
            "score": ((sleep.get("sleepScores") or {}).get("overall") or {}).get("value"),
            "total_s": sleep.get("sleepTimeSeconds"),
            "deep_s": sleep.get("deepSleepSeconds"),
            "light_s": sleep.get("lightSleepSeconds"),
            "rem_s": sleep.get("remSleepSeconds"),
            "awake_s": sleep.get("awakeSleepSeconds"),
        },
    }
    if "errors" in data:
        result["errors"] = data["errors"]
    if include_raw:
        result["raw"] = {"summary": data.get("summary"), "sleep": data.get("sleep")}
    return result


@mcp.tool()
def get_training_status(date: str | None = None) -> dict[str, Any]:
    """Training status/load, VO2max and training readiness for a date (default today).

    Availability depends on the user's device; missing sections are reported in 'errors'.
    """
    client = _call(get_client)
    day = date or _today()
    return {
        "date": day,
        **_sections(
            training_status=lambda: client.get_training_status(day),
            max_metrics=lambda: client.get_max_metrics(day),
            training_readiness=lambda: client.get_training_readiness(day),
        ),
    }


METERS_PER_MILE = 1609.344


def _pacepro_summary(p: dict[str, Any]) -> dict[str, Any]:
    goal_speed = p.get("goalSpeed")
    return {
        "plan_id": p.get("paceBandPk"),
        "name": p.get("name"),
        "course_id": p.get("courseId") or None,
        "distance_m": p.get("totalDistance"),
        "goal_time_s": p.get("goalTime"),
        "goal_pace_s_per_km": _pace_s_per_km(goal_speed),
        "goal_pace_s_per_mile": round(METERS_PER_MILE / goal_speed, 1) if goal_speed else None,
        "split_type": p.get("splitType"),
        "distance_unit": p.get("distanceUnit"),
        "pacing_strategy": p.get("pacingStrategy"),
        "uphill_effort": p.get("uphillEffort"),
        "manual_pace_edit": p.get("manualPaceEdit"),
        "event_date": p.get("eventDate"),
        "created": p.get("createdDate"),
        "updated": p.get("lastUpdatedDate"),
    }


@mcp.tool()
def list_pacepro_plans() -> list[dict[str, Any]]:
    """List the user's PacePro pacing plans (goal time, distance, pacing strategy).

    pacing_strategy and uphill_effort are Garmin's raw slider values.
    """
    client = _call(get_client)
    plans = _call(lambda: client.connectapi("/course-service/pacebands/summaries"))
    if not isinstance(plans, list):
        return []
    return [_pacepro_summary(p) for p in plans]


@mcp.tool()
def get_pacepro_plan(plan_id: int, include_points: bool = False) -> dict[str, Any]:
    """One PacePro plan with its per-split targets (distance, cumulative time, target pace).

    include_points adds each split's start/end GPS coordinates.
    """
    client = _call(get_client)
    data = _call(lambda: client.connectapi(f"/course-service/pacebands/{int(plan_id)}")) or {}
    splits = []
    for i, sp in enumerate(data.get("paceBandSplits") or [], start=1):
        speed = sp.get("splitAvgSpeed")
        split = {
            "split": i,
            "distance_m": sp.get("splitDistance"),
            "cumulative_distance_m": sp.get("cumulativeDistanceFromStart"),
            "cumulative_time_s": sp.get("cumulativeTimeFromStart"),
            "target_speed_mps": speed,
            "target_pace_s_per_km": _pace_s_per_km(speed),
            "target_pace_s_per_mile": round(METERS_PER_MILE / speed, 1) if speed else None,
        }
        if include_points:
            split["start_point"] = sp.get("startPoint")
            split["end_point"] = sp.get("endPoint")
        splits.append(split)
    return {**_pacepro_summary(data.get("paceBandSummary") or {}), "splits": splits}


# --------------------------------------------------------------- write tools


@mcp.tool()
def create_workout(
    sport: Sport, name: str, steps: list[Step], description: str | None = None
) -> dict[str, Any]:
    """Create a structured workout in the user's Garmin Connect workout library.

    Each step ends after duration_s or distance_m (or the lap button if neither).
    Use type 'repeat' with iterations + nested steps for intervals. Example:
    [{"type":"warmup","duration_s":600},
     {"type":"repeat","iterations":5,"steps":[
        {"type":"interval","distance_m":1000,"target":{"kind":"pace","low":"4:30","high":"4:45"}},
        {"type":"recovery","duration_s":120}]},
     {"type":"cooldown","duration_s":600}]
    Returns the new workout_id (use with schedule_workout).
    """
    payload = _call(lambda: build_workout(sport, name, steps, description))
    client = _call(get_client)
    created = _call(lambda: client.upload_workout(payload))
    return {
        "workout_id": created.get("workoutId"),
        "name": created.get("workoutName"),
        "sport": sport,
        "estimated_duration_s": created.get("estimatedDurationInSecs"),
    }


@mcp.tool()
def schedule_workout(workout_id: int, date: str) -> dict[str, Any]:
    """Put an existing workout on the Garmin Connect calendar (YYYY-MM-DD); it syncs to the watch."""
    client = _call(get_client)
    result = _call(lambda: client.schedule_workout(workout_id, date))
    return {"workout_id": workout_id, "date": date, "garmin_response": result}


@mcp.tool()
def list_workouts(start: int = 0, limit: int = 50) -> list[dict[str, Any]]:
    """List workouts in the user's Garmin Connect library."""
    client = _call(get_client)
    workouts = _call(lambda: client.get_workouts(start, limit)) or []
    return [
        {
            "workout_id": w.get("workoutId"),
            "name": w.get("workoutName"),
            "sport": (w.get("sportType") or {}).get("sportTypeKey"),
            "estimated_duration_s": w.get("estimatedDurationInSecs"),
            "created": w.get("createdDate"),
            "updated": w.get("updatedDate"),
        }
        for w in workouts
    ]


@mcp.tool()
def get_workout(workout_id: int) -> dict[str, Any]:
    """Full Garmin JSON for one workout, including all steps and targets."""
    client = _call(get_client)
    return _call(lambda: client.get_workout_by_id(workout_id))


@mcp.tool()
def delete_workout(workout_id: int) -> dict[str, Any]:
    """Permanently delete a workout from the Garmin library. Confirm with the user first."""
    client = _call(get_client)
    _call(lambda: client.delete_workout(workout_id))
    return {"deleted": True, "workout_id": workout_id}


def main(transport: Literal["stdio"] = "stdio") -> None:
    mcp.run(transport)


if __name__ == "__main__":
    main()
