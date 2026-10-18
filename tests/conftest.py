"""Shared fixtures: an in-memory MCP client wired to a fake Garmin account."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import Any

import pytest
from garminconnect import GarminConnectNotFoundError
from mcp import Client

from garmin_mcp import server

FAKE_TOKEN = "di-token-that-must-never-leak"


class FakeAuthClient:
    """Stands in for garminconnect's auth client, holding a secret token."""

    di_token = FAKE_TOKEN
    di_refresh_token = FAKE_TOKEN + "-refresh"

    def __init__(self) -> None:
        self.posted: list[tuple[str, Any]] = []
        self.deleted: list[str] = []

    def post(self, domain: str, path: str, json: Any = None, api: bool = False) -> Any:
        self.posted.append((path, json))
        return {"paceBandPk": 42}

    def delete(self, domain: str, path: str, api: bool = False) -> Any:
        self.deleted.append(path)
        return {}


class FakeGarmin:
    """Records write calls and returns canned Garmin-shaped payloads."""

    def __init__(self) -> None:
        self.client = FakeAuthClient()
        self.uploaded: list[dict[str, Any]] = []
        self.scheduled: list[tuple[int, str]] = []
        self.deleted: list[int] = []

    # --- reads
    def get_activities(self, start: int, limit: int, activitytype: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "activityId": 101,
                "activityName": "Morning Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": "2026-10-01 07:00:00",
                "duration": 1800.0,
                "distance": 6000.0,
                "averageHR": 148,
                "averageSpeed": 3.3333,
                "activityTrainingLoad": 82.5,
            },
            {"activityId": 102, "activityName": "Ride", "activityType": {"typeKey": "cycling"}},
        ][:limit]

    def get_activities_by_date(
        self, startdate: str, enddate: str | None = None, activitytype: str | None = None
    ) -> list[dict[str, Any]]:
        return self.get_activities(0, 100, activitytype)

    def get_activity(self, activity_id: str) -> dict[str, Any]:
        return {"activityId": int(activity_id), "summaryDTO": {"distance": 6000.0}}

    def get_activity_splits(self, activity_id: str) -> dict[str, Any]:
        return {"lapDTOs": [{"distance": 1000.0, "duration": 300.0}]}

    def get_activity_hr_in_timezones(self, activity_id: str) -> list[dict[str, Any]]:
        return [{"zoneNumber": 2, "secsInZone": 1200}]

    def get_activity_power_in_timezones(self, activity_id: str) -> Any:
        raise GarminConnectNotFoundError("no power data")

    def get_user_summary(self, cdate: str) -> dict[str, Any]:
        return {"totalSteps": 12345, "restingHeartRate": 48, "bodyBatteryMostRecentValue": 71}

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        return {"dailySleepDTO": {"sleepScores": {"overall": {"value": 84}}, "sleepTimeSeconds": 27000}}

    def get_training_status(self, cdate: str) -> dict[str, Any]:
        return {"mostRecentTrainingStatus": {"trainingStatus": "PRODUCTIVE"}}

    def get_max_metrics(self, cdate: str) -> dict[str, Any]:
        return {"generic": {"vo2MaxValue": 55}}

    def get_training_readiness(self, cdate: str) -> list[dict[str, Any]]:
        return [{"score": 77}]

    def get_workouts(self, start: int, limit: int) -> list[dict[str, Any]]:
        return [{"workoutId": 9, "workoutName": "5x1k", "sportType": {"sportTypeKey": "running"}}]

    def get_workout_by_id(self, workout_id: int) -> dict[str, Any]:
        return {"workoutId": workout_id, "workoutName": "5x1k"}

    def connectapi(self, path: str, **kwargs: Any) -> Any:
        summary = {
            "paceBandPk": 42,
            "name": "sub 25 5k",
            "courseId": 0,
            "totalDistance": 5000.0,
            "goalTime": 1495,
            "goalSpeed": 5000.0 / 1495,
            "splitType": "DISTANCE_MILE",
            "distanceUnit": "MILE",
            "pacingStrategy": 0.4,
            "uphillEffort": 0.0,
        }
        if path == "/course-service/pacebands/summaries":
            return [summary]
        if path == "/course-service/pacebands/42":
            split = {
                "splitDistance": 1609.344,
                "cumulativeDistanceFromStart": 1609.344,
                "cumulativeTimeFromStart": 489.6,
                "splitAvgSpeed": 1609.344 / 489.6,
                "startPoint": {"latitude": 1.0, "longitude": 2.0},
                "endPoint": {"latitude": 1.1, "longitude": 2.1},
            }
            return {"paceBandPk": 42, "paceBandSummary": summary, "paceBandSplits": [split]}
        raise GarminConnectNotFoundError(f"unexpected path {path}")

    # --- writes
    def upload_workout(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.uploaded.append(payload)
        return {
            "workoutId": 555,
            "workoutName": payload["workoutName"],
            "estimatedDurationInSecs": payload["estimatedDurationInSecs"],
        }

    def schedule_workout(self, workout_id: int, date_str: str) -> dict[str, Any]:
        self.scheduled.append((workout_id, date_str))
        return {"workoutScheduleId": 777, "calendarDate": date_str}

    def delete_workout(self, workout_id: int) -> None:
        self.deleted.append(workout_id)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run every async test on anyio's asyncio backend."""
    for item in items:
        if inspect.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.anyio)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_garmin(monkeypatch: pytest.MonkeyPatch) -> FakeGarmin:
    fake = FakeGarmin()
    monkeypatch.setattr(server, "get_client", lambda: fake)
    return fake


@pytest.fixture
async def mcp_client() -> AsyncIterator[Client]:
    async with Client(server.mcp) as client:
        yield client
