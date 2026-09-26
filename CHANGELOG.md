# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-15

### Added
- MCP server (stdio) with 9 tools:
  - Read: `list_activities`, `get_activity_detail`, `get_daily_summary`, `get_training_status`
  - Write: `create_workout`, `schedule_workout`, `list_workouts`, `get_workout`, `delete_workout`
- Compact workout step format with repeats, time/distance/lap-button end conditions, and pace, speed, heart-rate, power and cadence targets (ranges or zones). It's validated before anything is uploaded.
- `garmin-mcp-login`: a one-time interactive login with MFA support. Only session tokens are stored, never the password. Includes `--check` and `--logout`.
- `./run`: a one-command setup that syncs dependencies, logs in if needed, installs the server to `~/.local/bin` and starts it.
- Privacy guardrails: a Garmin-only network boundary, owner-only token storage, token-free tool output, and errors re-raised with readable messages.
- Test suite (workout builder, MCP tools over an in-memory client, auth, privacy boundary), plus CI on Linux and macOS for Python 3.12–3.14, ruff, pyright and gitleaks.

[0.1.0]: https://github.com/agogte/garmin_mcp/releases/tag/v0.1.0
