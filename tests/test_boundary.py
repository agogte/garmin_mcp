"""Guards for the privacy model: Garmin-only network targets, no token leakage."""

import re
from pathlib import Path

from .conftest import FAKE_TOKEN

SRC = Path(__file__).resolve().parents[1] / "src"
URL = re.compile(r"https?://([A-Za-z0-9.-]+)")


def test_source_only_references_garmin_hosts():
    hosts = {host for path in SRC.rglob("*.py") for host in URL.findall(path.read_text())}
    offenders = {h for h in hosts if not (h == "garmin.com" or h.endswith(".garmin.com"))}
    assert not offenders, f"non-Garmin hosts in src/: {offenders}"


def test_server_never_handles_passwords():
    server_src = (SRC / "garmin_mcp" / "server.py").read_text().lower()
    assert "password" not in server_src
    assert "getpass" not in server_src


async def test_no_tool_output_contains_tokens(mcp_client, fake_garmin):
    calls = {
        "list_activities": {},
        "get_activity_detail": {"activity_id": 101},
        "get_daily_summary": {"date": "2026-10-01", "include_raw": True},
        "get_training_status": {"date": "2026-10-01"},
        "list_workouts": {},
        "get_workout": {"workout_id": 9},
        "list_pacepro_plans": {},
        "get_pacepro_plan": {"plan_id": 42, "include_points": True},
    }
    for name, args in calls.items():
        result = await mcp_client.call_tool(name, args)
        assert FAKE_TOKEN not in result.model_dump_json(), f"{name} leaked a token"
