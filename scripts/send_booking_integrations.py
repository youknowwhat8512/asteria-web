#!/usr/bin/env python3
"""Deterministic three-leg delivery wrapper for one Asteria booking alert.

The outbox consumer (``scripts/consume_booking_outbox.py``) invokes THIS wrapper
instead of a single sender. The wrapper runs the three idempotent delivery legs
in a fixed order:

  1. Google Calendar  (``scripts/send_google_calendar_booking.py``)
  2. Discord          (``scripts/send_discord_booking_alert.py``)
  3. Kakao (UI)       (SSoT ``22_2_tools/.../scripts/send_booking_alert.py``)

Calendar runs FIRST, Discord runs ONLY if Calendar verified (exit 0), and Kakao
runs ONLY if BOTH Calendar and Discord verified. This ordering means the durable
Calendar event and the Discord thread alert are both committed before the
KakaoTalk UI leg is attempted — a human who sees the Kakao message can trust the
calendar and Discord already hold the matching event. A non-OK leg
short-circuits: later legs are never attempted.

Every leg receives the SAME common booking CLI fields (including
``--idempotency-key``); each leg reads its own hard-pinned target config. The
Kakao leg additionally receives fixed, non-secret extras it needs and the other
legs do not: ``--member-included unknown`` and a fixed Google Calendar URL.

Design guarantees:
  * stdlib only; no third-party dependencies.
  * Each leg is a hard-pinned absolute script path; no other program runs. The
    Kakao leg is pinned to the 22_2_tools SSoT sender and its config to
    ``~/.config/asteria-kakao/booking-alert.json``.
  * Child stdout/stderr is CAPTURED and never re-emitted, so the message body,
    booker name, route, booking code, chat id, tokens, and credentials in a
    child cannot leak through the wrapper. Only stable, opaque status codes
    reach the wrapper's own stdout/stderr.
  * Delivery is reported successful ONLY when ALL THREE legs verify.

Exit codes (consumed by the outbox consumer, matching the single-sender
contract it already understands):
  0  All legs written AND verified. Safe to mark the row delivered.
  2  A leg failed in a provably safe / retryable way (exit 2, or the child
     could not be started at all). Because every leg is deterministic and
     idempotent, retrying the WHOLE wrapper is safe — Calendar re-addresses the
     same event id, Discord re-uses the same enforce_nonce, and Kakao is a no-op
     when its exact message is already present (pre-read idempotency).
  3  A leg was uncertain (exit 3, an unexpected child exit code, or a child
     timeout): a write/click may have happened but could not be confirmed.
     Terminal manual review; never auto-retry.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# --- Fixed, non-secret constants ------------------------------------------

REPO_SCRIPTS = Path("/Users/ja/repos/55_MyLabs/asteria-web/scripts")

# The two delivery legs are hard-pinned absolute paths; the wrapper will never
# execute any other program.
EXPECTED_CALENDAR_SENDER = REPO_SCRIPTS / "send_google_calendar_booking.py"
EXPECTED_DISCORD_SENDER = REPO_SCRIPTS / "send_discord_booking_alert.py"
# The Kakao leg lives in the 22_2_tools SSoT skill, NOT this repo. It is
# hard-pinned to the absolute SSoT path; the wrapper runs no other program.
EXPECTED_KAKAO_SENDER = Path(
    "/Users/ja/repos/22_2_tools/skills/asteria-kakao-booking-alerts/scripts/send_booking_alert.py"
)

DEFAULT_CALENDAR_CONFIG = (
    Path.home() / ".config" / "asteria-google-calendar" / "booking-alert.json"
)
DEFAULT_DISCORD_CONFIG = (
    Path.home() / ".config" / "asteria-discord" / "booking-alert.json"
)
DEFAULT_KAKAO_CONFIG = (
    Path.home() / ".config" / "asteria-kakao" / "booking-alert.json"
)

# Fixed, non-secret extras the Kakao leg requires and the other legs do not.
# Kakao links back to the fixed Veronica booking page; Calendar remains an
# internal integration leg but its Google URL is not exposed in chat messages.
KAKAO_MEMBER_INCLUDED = "unknown"
KAKAO_BOOKING_URL = "https://asteria.club/veronica/"

# Ordered so Calendar (the durable, deterministic-idempotent leg) commits first,
# Discord second, and the KakaoTalk UI leg last.
LEG_ORDER = ("calendar", "discord", "kakao")

# Per-leg cap. Calendar/Discord make bounded HTTP calls (20s each), so a healthy
# leg finishes well under CHILD_TIMEOUT; the ceiling only bounds a hung leg. The
# Kakao leg drives the KakaoTalk UI (window recovery + read-backs), which is
# slower, so it gets a larger dedicated cap.
CHILD_TIMEOUT = 90
KAKAO_CHILD_TIMEOUT = 150

# Stable, non-secret result codes emitted to the wrapper's own logs.
CODE_OK = "ok"
CODE_RETRYABLE = "retryable"
CODE_UNCERTAIN = "uncertain"
CODE_START_FAILED = "start_failed"


def log_status(code: str) -> None:
    print(f"send_booking_integrations: {code}", flush=True)


def log_error(code: str) -> None:
    print(f"send_booking_integrations: {code}", file=sys.stderr, flush=True)


# --- Child leg invocation --------------------------------------------------


def build_child_argv(
    sender: Path, config: Path, args: argparse.Namespace, extra: list | None = None
) -> list:
    """Build one leg's argv from the common booking fields plus that leg's own
    target config and any leg-specific fixed extras. No shell is used."""
    argv = [
        sys.executable,
        str(sender),
        "--event",
        args.event,
        "--booking-id",
        args.booking_id,
        "--date",
        args.date,
        "--start-time",
        args.start_time,
        "--end-time",
        args.end_time,
        "--route",
        args.route,
        "--name",
        args.name,
        "--party-size",
        str(args.party_size),
        # Deterministic dedupe key shared by every leg. Passing the SAME row key
        # to each leg keeps a whole-wrapper retry idempotent.
        "--idempotency-key",
        args.idempotency_key,
        "--config",
        str(config),
    ]
    if extra:
        argv.extend(extra)
    return argv


def run_leg(
    sender: Path,
    config: Path,
    args: argparse.Namespace,
    *,
    extra: list | None = None,
    timeout: int = CHILD_TIMEOUT,
) -> str:
    """Run one delivery leg and reduce it to a stable result code. The child's
    stdout/stderr are captured and discarded so no message content can leak
    through the wrapper.

    Returns one of CODE_OK / CODE_RETRYABLE / CODE_UNCERTAIN / CODE_START_FAILED."""
    argv = build_child_argv(sender, config, args, extra)
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # A leg that timed out may have completed a write we cannot confirm.
        return CODE_UNCERTAIN
    except (OSError, subprocess.SubprocessError):
        # The child could not be started at all — nothing was sent.
        return CODE_START_FAILED

    rc = proc.returncode
    if rc == 0:
        return CODE_OK
    if rc == 2:
        return CODE_RETRYABLE
    if rc == 3:
        return CODE_UNCERTAIN
    # Any other exit code is treated conservatively as uncertain.
    return CODE_UNCERTAIN


# --- Result reduction ------------------------------------------------------

# Map a leg's stable result code to the wrapper's process exit code.
_RESULT_EXIT = {
    CODE_RETRYABLE: 2,
    CODE_START_FAILED: 2,
    CODE_UNCERTAIN: 3,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Calendar and Discord booking-alert legs in order"
    )
    parser.add_argument("--event", required=True)
    parser.add_argument("--booking-id", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--end-time", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--party-size", type=int, required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument(
        "--calendar-config", type=Path, default=DEFAULT_CALENDAR_CONFIG
    )
    parser.add_argument(
        "--discord-config", type=Path, default=DEFAULT_DISCORD_CONFIG
    )
    parser.add_argument(
        "--kakao-config", type=Path, default=DEFAULT_KAKAO_CONFIG
    )
    args = parser.parse_args(argv)

    kakao_extra = [
        "--member-included",
        KAKAO_MEMBER_INCLUDED,
        "--booking-url",
        KAKAO_BOOKING_URL,
    ]

    # Each leg: (pinned sender, target config, fixed extras, per-leg timeout).
    legs = {
        "calendar": (EXPECTED_CALENDAR_SENDER, args.calendar_config, None, CHILD_TIMEOUT),
        "discord": (EXPECTED_DISCORD_SENDER, args.discord_config, None, CHILD_TIMEOUT),
        "kakao": (EXPECTED_KAKAO_SENDER, args.kakao_config, kakao_extra, KAKAO_CHILD_TIMEOUT),
    }

    # Calendar first, Discord only if Calendar verified, Kakao only if both
    # verified. A non-OK leg short-circuits: later legs are never attempted.
    for name in LEG_ORDER:
        sender, config, extra, timeout = legs[name]
        result = run_leg(sender, config, args, extra=extra, timeout=timeout)
        if result != CODE_OK:
            exit_code = _RESULT_EXIT.get(result, 3)
            log_error(f"leg={name} result={result} exit={exit_code}")
            return exit_code
        log_status(f"leg={name} result={result}")

    log_status("all_legs_verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
