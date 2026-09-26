"""Garmin Connect session handling.

Privacy model:
- The password is never stored. ``garmin-mcp-login`` asks for it interactively
  (hidden input) in the user's own terminal, sends it only to Garmin's SSO
  (https://sso.garmin.com), and then drops it.
- Only the resulting session tokens are persisted, in a local file with 0600
  permissions inside a 0700 directory (enforced by ``garminconnect``).
- The MCP server itself never receives credentials: it can only resume from
  that token file, and tokens refreshed during use are written back to it.
"""

import argparse
import getpass
import os
import sys
import threading
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

TOKEN_DIR = Path(os.environ.get("GARMIN_TOKEN_DIR", "~/.garmin_mcp/tokens")).expanduser()

LOGIN_HINT = (
    "No valid Garmin session. Ask the user to run `./run` (or `uv run garmin-mcp-login`) "
    "in the garmin-mcp project directory in their own terminal. Never ask for the "
    "password in chat."
)


class NotLoggedIn(Exception):
    """No usable cached session; the user must run the interactive login."""


_client: Garmin | None = None
_lock = threading.Lock()


def _ensure_private_dir() -> None:
    TOKEN_DIR.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    TOKEN_DIR.parent.chmod(0o700)


def get_client() -> Garmin:
    """Return a logged-in client, resuming from the local token cache only."""
    global _client
    with _lock:
        if _client is not None:
            return _client
        # No email/password: login() can only resume from the token file.
        garmin = Garmin()
        try:
            garmin.login(tokenstore=str(TOKEN_DIR))
        except GarminConnectAuthenticationError as e:
            raise NotLoggedIn(LOGIN_HINT) from e
        except GarminConnectConnectionError as e:
            # A missing/corrupt token file surfaces as a connection error.
            if not (TOKEN_DIR / "garmin_tokens.json").exists():
                raise NotLoggedIn(LOGIN_HINT) from e
            raise
        _client = garmin
        return _client


def reset_client() -> None:
    """Drop the cached client so the next call re-reads the token file."""
    global _client
    with _lock:
        _client = None


def login_cli() -> None:
    parser = argparse.ArgumentParser(
        prog="garmin-mcp-login",
        description="One-time interactive Garmin Connect login. Stores session "
        f"tokens (never your password) in {TOKEN_DIR}.",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Delete the local token file. To also revoke the session at Garmin, "
        "sign out of other sessions in your Garmin account settings.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the saved session works (exit 1 if not). Never prompts.",
    )
    args = parser.parse_args()

    if args.check:
        try:
            garmin = get_client()
        except NotLoggedIn:
            sys.exit(1)
        except Exception as e:
            sys.exit(f"Garmin session check failed: {e}")
        print(f"Garmin session OK ({garmin.get_full_name() or 'unknown user'}).")
        return

    if args.logout:
        Garmin().logout(tokenstore=str(TOKEN_DIR))
        print(f"Deleted local Garmin tokens in {TOKEN_DIR}.")
        return

    if not sys.stdin.isatty():
        sys.exit("garmin-mcp-login must be run interactively in a terminal.")

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (hidden): ")
    garmin = Garmin(
        email,
        password,
        prompt_mfa=lambda: input("MFA code (check email/authenticator app): ").strip(),
    )
    del password

    try:
        # No tokenstore here so a stale cache can't short-circuit a fresh login.
        garmin.login()
    except GarminConnectTooManyRequestsError as e:
        sys.exit(f"Garmin rate-limited the login: {e}")
    except GarminConnectAuthenticationError as e:
        sys.exit(f"Login failed: {e}")
    except GarminConnectConnectionError as e:
        sys.exit(f"Could not reach Garmin / login failed: {e}")

    _ensure_private_dir()
    garmin.client.dump(str(TOKEN_DIR))
    print(f"Logged in as {garmin.get_full_name() or email}.")
    print(f"Session tokens saved to {TOKEN_DIR} (owner-only permissions). Password was not stored.")
