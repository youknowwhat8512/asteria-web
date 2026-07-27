#!/usr/bin/env python3
"""Deterministic two-leg delivery wrapper for one Asteria booking alert.

The outbox consumer (``scripts/consume_booking_outbox.py``) invokes THIS wrapper
instead of a single sender. The wrapper runs the two idempotent delivery legs in
a fixed order:

  1. Google Calendar  (``scripts/send_google_calendar_booking.py``)
  2. Discord          (``scripts/send_discord_booking_alert.py``)

Calendar runs FIRST and Discord runs ONLY if Calendar verified (exit 0). This
ordering means a human who sees the Discord alert can trust the calendar already
holds the matching event. Both legs receive the SAME common booking CLI fields
(including ``--idempotency-key``); each leg reads its own hard-pinned target
config.

Design guarantees:
  * stdlib only; no third-party dependencies.
  * Each leg is a hard-pinned absolute script path; no other program runs.
  * Child stdout/stderr is CAPTURED and never re-emitted, so the message body,
    booker name, route, booking code, tokens, and credentials in a child cannot
    leak through the wrapper. Only stable, opaque status codes reach the
    wrapper's own stdout/stderr.
  * Delivery is reported successful ONLY when BOTH legs verify.

Exit codes (consumed by the outbox consumer, matching the single-sender
contract it already understands):
  0  Both legs written AND verified. Safe to mark the row delivered.
  2  A leg failed in a provably safe / retryable way (exit 2, or the child
     could not be started at all). Because every leg is deterministic and
     idempotent, retrying the WHOLE wrapper is safe — Calendar re-addresses the
     same event id and Discord re-uses the same enforce_nonce.
  3  A leg was uncertain (exit 3, an unexpected child exit code, or a child
     timeout): a write may have happened but could not be confirmed. Terminal
     manual review; never auto-retry.
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

DEFAULT_CALENDAR_CONFIG = (
    Path.home() / ".config" / "asteria-google-calendar" / "booking-alert.json"
)
DEFAULT_DISCORD_CONFIG = (
    Path.home() / ".config" / "asteria-discord" / "booking-alert.json"
)

# Ordered so Calendar (the durable, deterministic-idempotent leg) commits first.
LEG_ORDER = ("calendar", "discord")

# Per-leg cap. Each leg's own HTTP calls are bounded (20s each), so a healthy
# leg finishes well under this; the ceiling only bounds a hung leg. The
# consumer's outer SENDER_TIMEOUT sits above two of these so a slow-but-
# progressing wrapper is not killed mid-flight.
CHILD_TIMEOUT = 90

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


def build_child_argv(sender: Path, config: Path, args: argparse.Namespace) -> list:
    """Build one leg's argv from the common booking fields plus that leg's own
    target config. No shell is used."""
    return [
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
        "--idempotency-key",
        args.idempotency_key,
        "--config",
        str(config),
    ]


def run_leg(sender: Path, config: Path, args: argparse.Namespace) -> str:
    """Run one delivery leg and reduce it to a stable result code. The child's
    stdout/stderr are captured and discarded so no message content can leak
    through the wrapper.

    Returns one of CODE_OK / CODE_RETRYABLE / CODE_UNCERTAIN / CODE_START_FAILED."""
    argv = build_child_argv(sender, config, args)
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CHILD_TIMEOUT,
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
    args = parser.parse_args(argv)

    legs = {
        "calendar": (EXPECTED_CALENDAR_SENDER, args.calendar_config),
        "discord": (EXPECTED_DISCORD_SENDER, args.discord_config),
    }

    # Calendar first, Discord only if Calendar verified. A non-OK leg
    # short-circuits: the later leg is never attempted.
    for name in LEG_ORDER:
        sender, config = legs[name]
        result = run_leg(sender, config, args)
        if result != CODE_OK:
            exit_code = _RESULT_EXIT.get(result, 3)
            log_error(f"leg={name} result={result} exit={exit_code}")
            return exit_code
        log_status(f"leg={name} result={result}")

    log_status("all_legs_verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
