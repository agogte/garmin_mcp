# Security & privacy model

garmin-mcp handles credentials for an account that holds health, sleep, heart-rate and GPS data. It's designed so that **your Garmin password is never stored, and no credential ever leaves your machine except to Garmin itself.**

## Threat model

| Asset | Where it lives | Protection |
|---|---|---|
| Garmin password | Nowhere | Typed once into a hidden `getpass` prompt by `garmin-mcp-login`, sent over HTTPS only to `sso.garmin.com`, then deleted from memory. Never written to disk, env vars, config files or logs. |
| Session tokens (DI OAuth access + refresh) | `~/.garmin_mcp/tokens/garmin_tokens.json` | File mode `0600` inside a `0700` directory. Written atomically through a random temp file opened with `O_EXCL` (and `O_NOFOLLOW` where available), and symlinked paths are refused (enforced by `garminconnect`). Refreshed tokens are re-saved in place. |
| Activity & health data | Garmin's servers; returned to the MCP client on request | Only fetched when a tool is called. Not cached by this server. |

The MCP server process (`garmin-mcp`) **never receives your password**. It can only resume an existing session from the token file. If there isn't a valid session, tools return an error asking you to run `./run` in your own terminal.

## Network boundary

The only hosts contacted are Garmin's own:

- `sso.garmin.com`: login (only from `garmin-mcp-login`, never from the server)
- `diauth.garmin.com`: token exchange and refresh
- `connectapi.garmin.com`: data and workout API calls
- `connect.garmin.com`: session establishment

`garminconnect`'s auth client rejects any domain other than `garmin.com` / `garmin.cn`. There's no backend, relay, analytics or telemetry. The MCP SDK includes only the OpenTelemetry *API*, with no SDK or exporter installed, so spans are no-ops.

Dependency audit notes (garminconnect 0.3.16, mcp 2.2.0):
- `curl_cffi` is used for TLS fingerprinting during login. Its optional fingerprint-update endpoint is only called through its manual `curl-cffi pro update` CLI, never automatically. DNS-over-HTTPS is off unless a `doh_url` is passed, and garminconnect never passes one.
- `ua_generator` generates user-agent strings locally and has no network code.

CI enforces part of this: `tests/test_boundary.py` fails if any non-Garmin host appears in `src/`, if the server module handles passwords, or if a token shows up in any tool output. Every push also runs `gitleaks` against the full git history.

## Working with AI agents

- **Never paste your Garmin password into a chat.** Only type it into the `./run` / `garmin-mcp-login` prompt in your own terminal.
- **Deny your agent read access to `~/.garmin_mcp/`.** Coding agents send whatever files they read to their model provider. Most agents support a deny-list in their permission settings. Add the token directory, and any `.env` files, to it.
- **Never register the server with secrets in its environment** (e.g. `-e GARMIN_PASSWORD=...`). Client configs are usually plaintext JSON. The server needs no env vars.
- **Tool output does reach your assistant.** Activity, wellness and workout data you ask for goes into the conversation, and therefore to whichever model provider your client uses. Credentials and tokens never do.

## macOS note

macOS privacy protection (TCC) stops desktop apps from executing files under `~/Documents`, `~/Desktop` and `~/Downloads` unless you grant them folder access. Instead of asking for that broad grant, `./run` installs the server to `~/.local/bin/garmin-mcp` with `uv tool install`. MCP clients launch that copy, so they never need access to your source folder.

## Revoking access

1. `uv run garmin-mcp-login --logout` deletes the local token file.
2. To also invalidate the session on Garmin's side, change your Garmin password, or sign out of all sessions in your Garmin account security settings.

## Reporting a vulnerability

Please report security issues **privately** through [GitHub Security Advisories](https://github.com/agogte/garmin_mcp/security/advisories/new) rather than in a public issue. Never include real credentials or token files in a report.
