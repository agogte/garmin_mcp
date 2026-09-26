from typing import Any

from garmin_mcp import server
from garmin_mcp.auth import NotLoggedIn

EXPECTED_TOOLS = {
    "list_activities",
    "get_activity_detail",
    "get_daily_summary",
    "get_training_status",
    "create_workout",
    "schedule_workout",
    "list_workouts",
    "get_workout",
    "delete_workout",
}


def payload(result: Any) -> Any:
    assert not result.is_error, result.content
    data = result.structured_content
    return data["result"] if isinstance(data, dict) and set(data) == {"result"} else data


async def test_lists_all_tools(mcp_client):
    tools = (await mcp_client.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_not_logged_in_returns_readable_error(mcp_client, monkeypatch):
    def not_logged_in():
        raise NotLoggedIn("No valid Garmin session. Run ./run")

    monkeypatch.setattr(server, "get_client", not_logged_in)
    result = await mcp_client.call_tool("list_activities", {"limit": 1})
    assert result.is_error
    assert "No valid Garmin session" in result.content[0].text


async def test_list_activities_is_trimmed(mcp_client, fake_garmin):
    activities = payload(await mcp_client.call_tool("list_activities", {"limit": 5}))
    assert len(activities) == 2
    run = activities[0]
    assert run["activity_id"] == 101
    assert run["type"] == "running"
    assert run["avg_pace_s_per_km"] == 300.0
    assert run["training_load"] == 82.5


async def test_activity_detail_isolates_failing_sections(mcp_client, fake_garmin):
    detail = payload(await mcp_client.call_tool("get_activity_detail", {"activity_id": 101}))
    assert detail["splits"]["lapDTOs"][0]["distance"] == 1000.0
    assert "power_time_in_zones" not in detail
    assert "no power data" in detail["errors"]["power_time_in_zones"]


async def test_daily_summary(mcp_client, fake_garmin):
    day = payload(await mcp_client.call_tool("get_daily_summary", {"date": "2026-10-01"}))
    assert day["steps"] == 12345
    assert day["resting_hr"] == 48
    assert day["sleep"]["score"] == 84
    assert day["body_battery"]["most_recent"] == 71


async def test_training_status(mcp_client, fake_garmin):
    status = payload(await mcp_client.call_tool("get_training_status", {"date": "2026-10-01"}))
    assert status["max_metrics"]["generic"]["vo2MaxValue"] == 55


async def test_create_and_schedule_workout(mcp_client, fake_garmin):
    created = payload(
        await mcp_client.call_tool(
            "create_workout",
            {
                "sport": "running",
                "name": "Tempo",
                "steps": [
                    {"type": "warmup", "duration_s": 600},
                    {
                        "type": "interval",
                        "duration_s": 1200,
                        "target": {"kind": "heart_rate", "low": 160, "high": 170},
                    },
                    {"type": "cooldown", "duration_s": 600},
                ],
            },
        )
    )
    assert created["workout_id"] == 555
    assert created["estimated_duration_s"] == 2400
    (uploaded,) = fake_garmin.uploaded
    assert uploaded["workoutName"] == "Tempo"
    assert len(uploaded["workoutSegments"][0]["workoutSteps"]) == 3

    scheduled = payload(
        await mcp_client.call_tool("schedule_workout", {"workout_id": 555, "date": "2026-10-20"})
    )
    assert scheduled["date"] == "2026-10-20"
    assert fake_garmin.scheduled == [(555, "2026-10-20")]


async def test_invalid_workout_is_rejected_before_upload(mcp_client, fake_garmin):
    result = await mcp_client.call_tool(
        "create_workout", {"sport": "running", "name": "bad", "steps": [{"type": "repeat"}]}
    )
    assert result.is_error
    assert fake_garmin.uploaded == []


async def test_workout_library(mcp_client, fake_garmin):
    workouts = payload(await mcp_client.call_tool("list_workouts", {}))
    assert workouts[0]["workout_id"] == 9
    assert workouts[0]["sport"] == "running"
    assert payload(await mcp_client.call_tool("get_workout", {"workout_id": 9}))["workoutId"] == 9
    assert payload(await mcp_client.call_tool("delete_workout", {"workout_id": 9}))["deleted"] is True
    assert fake_garmin.deleted == [9]
