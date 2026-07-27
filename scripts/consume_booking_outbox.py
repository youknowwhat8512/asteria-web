#!/usr/bin/env python3
"""Bounded, privacy-safe Supabase notification_outbox consumer for Asteria.

The Asteria web backend only ever writes a PRIVACY-SAFE row to
``public.notification_outbox``. This Mac-side consumer, invoked by launchd every
30 seconds with ``--once``, claims eligible rows atomically and hands each off
to the allowlisted integration wrapper ``scripts/send_booking_integrations.py``,
which runs two idempotent delivery legs in order — Google Calendar first, then
the pinned Veronica Discord thread — and reports success only when BOTH legs
verify by read-back.

Design guarantees:
  * stdlib only; no third-party dependencies.
  * The service-role secret is read at runtime from the macOS Keychain via
    ``hermes-keychain`` and is NEVER printed, logged, stored, or placed in
    process arguments. Only stable, opaque error codes and outbox row ids
    (UUIDs) ever appear in logs.
  * Only rows with ``created_at >= activated_at`` are ever selected, so
    historical test events are never delivered.
  * Rows are claimed with a conditional PATCH (compare-and-set on id +
    status + retry_count); processing is idempotent and race-safe.
  * A crash after a send leaves a row in ``processing``; such rows are NEVER
    auto-reclaimed (that could duplicate a delivered message) — they are only
    counted and reported as ``stuck_processing`` for manual review.
  * The row's ``idempotency_key`` is passed to the wrapper and on to both legs,
    which derive a deterministic Discord nonce and a deterministic Calendar
    event id from it so a duplicate delivery is de-duplicated on both sides.
  * The wrapper's exit codes drive the terminal state: 0 -> delivered,
    2/exception -> retry with capped exponential backoff, 3 (a leg wrote but
    could not be verified) -> terminal manual review, never auto-resent.

Run modes (exactly one required):
  --dry-run   Validate config/keychain/sender, count eligible rows, mutate
              nothing and claim nothing. Prints aggregate counts only.
  --once      Perform one bounded pass of real processing.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError
from zoneinfo import ZoneInfo

# --- Fixed, non-secret constants ------------------------------------------

DEFAULT_CONFIG = Path.home() / ".config" / "asteria-discord" / "outbox-consumer.json"
EXPECTED_PROJECT_REF = "wbvbvfqrdhpsjmcwzouv"
# The sender is pinned to this exact absolute path — the consumer will never
# execute any other program as the delivery leg. It is the two-leg integration
# wrapper, which runs Google Calendar first and only then Discord; a row is
# delivered only when BOTH legs verify.
EXPECTED_SENDER = Path(
    "/Users/ja/repos/55_MyLabs/asteria-web/scripts/send_booking_integrations.py"
)
EXPECTED_KEYCHAIN_BIN = "/Users/ja/.local/bin/hermes-keychain"
KEYCHAIN_SERVICE = "asteria.supabase.service-role"
STATE_DIR = Path.home() / ".local" / "state" / "asteria-discord"
LOCK_PATH = STATE_DIR / "consume.lock"

TABLE = "notification_outbox"
KST = ZoneInfo("Asia/Seoul")

# event_type stored on the row/payload -> sender --event value.
EVENT_MAP = {
    "booking_created": "created",
    "booking_updated": "updated",
    "booking_cancelled": "cancelled",
}

# Strict payload allowlist. Only these top-level keys may appear.
ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "event_type",
        "booking_code",
        "vessel",
        "starts_at",
        "ends_at",
        "departure",
        "destination",
        "total_people",
        "booker_name",
    }
)

# Keys that must never appear at any nesting depth (defense in depth; mirrors
# the web-side outbox redaction test).
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "phone",
        "booker_phone",
        "guest_phone",
        "passengers",
        "passenger",
        "cancel_code",
        "cancellation_code",
        "waiver",
        "waiver_text",
        "booker_phone_encrypted",
        "guest_phone_encrypted",
    }
)

BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
FETCH_LIMIT = 100  # bounded superset fetch; eligibility is filtered in Python

HTTP_TIMEOUT = 20
KEYCHAIN_TIMEOUT = 15
# The integration wrapper runs Calendar, Discord, then the Kakao UI leg. The
# Kakao leg may need a bounded deep-recovery read, UI click, and read-back, so
# the outer cap must sit above the wrapper's 150s Kakao cap plus the two 90s
# network-leg caps. A genuine kill is still conservative: Calendar/Discord are
# deterministic-idempotent and Kakao pre-reads the exact message before click.
SENDER_TIMEOUT = 360

# Stable error codes persisted to last_error / emitted to logs. No PII, ever.
CODE_RETRYABLE = "delivery_failed_retryable"
CODE_MANUAL_REVIEW = "sent_but_unverified_manual_review"
CODE_VALIDATION = "payload_validation_failed"


class ConsumerError(Exception):
    """A fatal, non-secret consumer error. ``str`` is a stable code only."""


class RowRejected(Exception):
    """A claimed row failed strict validation; carries a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- Logging: aggregate counts and opaque row ids only ---------------------


def log(message: str) -> None:
    """Emit an operational log line. Callers must pass only aggregate counts,
    stable codes, or opaque UUID row ids — never message bodies, names,
    routes, phone numbers, secrets, or idempotency keys."""
    print(f"consume_booking_outbox: {message}", flush=True)


# --- Time helpers ----------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 / ISO-8601 timestamp into an aware datetime."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConsumerError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        raise ConsumerError("timestamp_missing_offset")
    return parsed


def compute_backoff_seconds(retry_count: int) -> int:
    """Capped exponential backoff for the NEXT attempt after ``retry_count``
    prior failures."""
    if retry_count < 0:
        retry_count = 0
    delay = BACKOFF_BASE_SECONDS * (2 ** retry_count)
    return min(delay, BACKOFF_CAP_SECONDS)


# --- Config ----------------------------------------------------------------


class Config:
    def __init__(
        self,
        project_ref: str,
        activated_at: datetime,
        sender_script: Path,
        keychain_bin: str,
        batch_size: int,
    ) -> None:
        self.project_ref = project_ref
        self.activated_at = activated_at
        self.sender_script = sender_script
        self.keychain_bin = keychain_bin
        self.batch_size = batch_size

    @property
    def rest_base(self) -> str:
        return f"https://{self.project_ref}.supabase.co/rest/v1"


def load_config(path: Path) -> Config:
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        raise ConsumerError("config_not_found") from exc
    if info.st_uid != os.getuid():
        raise ConsumerError("config_not_owned_by_user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ConsumerError("config_permissions_too_open")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConsumerError("config_invalid_json") from exc
    if not isinstance(raw, dict):
        raise ConsumerError("config_not_object")

    project_ref = str(raw.get("project_ref", "")).strip()
    if project_ref != EXPECTED_PROJECT_REF:
        raise ConsumerError("config_bad_project_ref")

    activated_at = parse_rfc3339(str(raw.get("activated_at", "")))

    sender_raw = str(raw.get("sender_script", "")).strip()
    if not sender_raw or not os.path.isabs(sender_raw):
        raise ConsumerError("config_sender_not_absolute")
    sender_script = Path(sender_raw)
    if os.path.realpath(sender_script) != os.path.realpath(EXPECTED_SENDER):
        raise ConsumerError("config_sender_not_allowlisted")
    if not sender_script.is_file():
        raise ConsumerError("config_sender_missing")

    keychain_bin = str(raw.get("keychain_bin", "")).strip()
    if keychain_bin != EXPECTED_KEYCHAIN_BIN:
        raise ConsumerError("config_bad_keychain_bin")
    if not (os.path.isfile(keychain_bin) and os.access(keychain_bin, os.X_OK)):
        raise ConsumerError("config_keychain_bin_not_executable")

    batch_size = raw.get("batch_size", 5)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool):
        raise ConsumerError("config_bad_batch_size")
    if not 1 <= batch_size <= 10:
        raise ConsumerError("config_batch_size_out_of_range")

    return Config(project_ref, activated_at, sender_script, keychain_bin, batch_size)


# --- Keychain --------------------------------------------------------------


def load_service_role(config: Config) -> str:
    """Read the service-role secret from the Keychain. The value is returned
    for immediate in-memory use and must never be logged or stored."""
    argv = [config.keychain_bin, "get", KEYCHAIN_SERVICE, config.project_ref]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConsumerError("keychain_unavailable") from exc
    if proc.returncode != 0:
        # Never surface stderr — it could echo the account/service context.
        raise ConsumerError("keychain_lookup_failed")
    secret = proc.stdout.strip()
    if not secret:
        raise ConsumerError("keychain_empty")
    return secret


# --- HTTP (PostgREST) ------------------------------------------------------


class Http:
    """Thin PostgREST client. Holds the service-role secret in memory only;
    it lives solely in request headers and is never logged."""

    def __init__(self, config: Config, service_role: str) -> None:
        self._base = config.rest_base
        self._auth = {
            "apikey": service_role,
            "Authorization": f"Bearer {service_role}",
        }

    def _request(self, method: str, query: str, body=None, prefer: str = None):
        url = f"{self._base}/{TABLE}?{query}"
        headers = dict(self._auth)
        headers["Accept"] = "application/json"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if prefer is not None:
            headers["Prefer"] = prefer
        req = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                payload = resp.read()
        except URLError as exc:
            raise ConsumerError("http_error") from exc
        except OSError as exc:
            raise ConsumerError("http_error") from exc
        if not payload:
            return []
        try:
            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConsumerError("http_bad_response") from exc

    def select(self, params) -> list:
        return self._request("GET", urlencode(params, quote_via=quote))

    def patch(self, filters, body) -> list:
        query = urlencode(filters, quote_via=quote)
        result = self._request("PATCH", query, body=body, prefer="return=representation")
        return result if isinstance(result, list) else []


# --- Eligibility & selection ----------------------------------------------


def eligible_rows(http: Http, config: Config) -> list:
    """Fetch a bounded superset of created_at >= activated_at rows in
    (pending, failed) and return, in FIFO order, those actually eligible to
    process now, capped at batch_size."""
    activated_iso = config.activated_at.isoformat()
    params = [
        ("select", "*"),
        ("created_at", f"gte.{activated_iso}"),
        ("status", "in.(pending,failed)"),
        ("order", "created_at.asc,id.asc"),
        ("limit", str(FETCH_LIMIT)),
    ]
    rows = http.select(params)
    now = now_utc()
    ready = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = row.get("status")
        if status == "pending":
            ready.append(row)
        elif status == "failed":
            retry_count = row.get("retry_count", 0)
            max_retries = row.get("max_retries", 0)
            nxt = row.get("next_attempt_at")
            if not isinstance(retry_count, int) or not isinstance(max_retries, int):
                continue
            if retry_count >= max_retries or not nxt:
                continue
            try:
                due = parse_rfc3339(str(nxt))
            except ConsumerError:
                continue
            if due <= now:
                ready.append(row)
        if len(ready) >= config.batch_size:
            break
    return ready


def count_stuck_processing(http: Http, config: Config) -> int:
    """Count (never reclaim) rows left in 'processing' after activation. A
    crash between send and finalize can strand a row here; auto-reclaiming
    could duplicate a delivered message, so we only report the count."""
    activated_iso = config.activated_at.isoformat()
    params = [
        ("select", "id"),
        ("created_at", f"gte.{activated_iso}"),
        ("status", "eq.processing"),
        ("limit", str(FETCH_LIMIT)),
    ]
    rows = http.select(params)
    return sum(1 for r in rows if isinstance(r, dict))


# --- Strict validation -----------------------------------------------------


def assert_no_forbidden_keys(value) -> None:
    stack = [value]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key, val in cur.items():
                if key in FORBIDDEN_PAYLOAD_KEYS:
                    raise RowRejected(CODE_VALIDATION)
                stack.append(val)
        elif isinstance(cur, list):
            stack.extend(cur)


def validate_and_build_argv(config: Config, row: dict) -> list:
    """Validate a claimed row + payload against a strict allowlist and build
    the sender argv (no shell). Raises RowRejected on any violation."""
    event_type = row.get("event_type")
    if event_type not in EVENT_MAP:
        raise RowRejected(CODE_VALIDATION)

    idempotency_key = row.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise RowRejected(CODE_VALIDATION)

    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise RowRejected(CODE_VALIDATION)

    assert_no_forbidden_keys(payload)

    extra = set(payload.keys()) - ALLOWED_PAYLOAD_KEYS
    if extra:
        raise RowRejected(CODE_VALIDATION)

    # payload.event_type must agree with the authoritative row.event_type.
    if payload.get("event_type") != event_type:
        raise RowRejected(CODE_VALIDATION)

    booking_code = payload.get("booking_code")
    booker_name = payload.get("booker_name")
    departure = payload.get("departure")
    destination = payload.get("destination")
    total_people = payload.get("total_people")
    starts_at = payload.get("starts_at")
    ends_at = payload.get("ends_at")

    for field in (booking_code, booker_name, departure, destination, starts_at, ends_at):
        if not isinstance(field, str) or not field.strip():
            raise RowRejected(CODE_VALIDATION)
    if not isinstance(total_people, int) or isinstance(total_people, bool):
        raise RowRejected(CODE_VALIDATION)
    if not 1 <= total_people <= 50:
        raise RowRejected(CODE_VALIDATION)

    try:
        start_dt = parse_rfc3339(starts_at).astimezone(KST)
        end_dt = parse_rfc3339(ends_at).astimezone(KST)
    except ConsumerError as exc:
        raise RowRejected(CODE_VALIDATION) from exc

    if end_dt.date() == start_dt.date():
        end_hhmm = end_dt.strftime("%H:%M")
    elif (
        end_dt.date() == start_dt.date() + timedelta(days=1)
        and end_dt.hour == 0
        and end_dt.minute == 0
        and end_dt.second == 0
        and end_dt.microsecond == 0
    ):
        # The booking contract allows an end at midnight, represented in the
        # database as next-day 00:00 but shown operationally as same-day 24:00.
        end_hhmm = "24:00"
    else:
        raise RowRejected(CODE_VALIDATION)

    date_str = start_dt.strftime("%Y-%m-%d")
    start_hhmm = start_dt.strftime("%H:%M")
    route = f"{departure.strip()} → {destination.strip()}"

    argv = [
        sys.executable,
        str(config.sender_script),
        "--event",
        EVENT_MAP[event_type],
        "--booking-id",
        booking_code.strip(),
        "--date",
        date_str,
        "--start-time",
        start_hhmm,
        "--end-time",
        end_hhmm,
        "--route",
        route,
        "--name",
        booker_name.strip(),
        "--party-size",
        str(total_people),
        # Deterministic dedupe key; each leg turns it into a Discord nonce and
        # a Calendar event id so a duplicate delivery is de-duplicated.
        "--idempotency-key",
        idempotency_key.strip(),
    ]
    return argv


# --- Claim & finalize (compare-and-set) ------------------------------------


def claim_row(http: Http, row: dict):
    """Atomically move a row pending/failed -> processing via a conditional
    PATCH on (id, status, retry_count). Returns the claimed representation, or
    None if another actor won the race."""
    filters = [
        ("id", f"eq.{row['id']}"),
        ("status", f"eq.{row['status']}"),
        ("retry_count", f"eq.{row['retry_count']}"),
    ]
    result = http.patch(filters, {"status": "processing"})
    if len(result) == 1:
        return result[0]
    return None


def _finalize(http: Http, row: dict, body: dict) -> bool:
    """Conditional PATCH from processing (compare-and-set on id + status +
    retry_count). Returns True iff exactly one row was updated."""
    filters = [
        ("id", f"eq.{row['id']}"),
        ("status", "eq.processing"),
        ("retry_count", f"eq.{row['retry_count']}"),
    ]
    return len(http.patch(filters, body)) == 1


def finalize_delivered(http: Http, row: dict) -> bool:
    return _finalize(
        http,
        row,
        {
            "status": "delivered",
            "delivered_at": now_utc().isoformat(),
            "last_error": None,
            "next_attempt_at": None,
        },
    )


def finalize_retry(http: Http, row: dict) -> bool:
    retry_count = row["retry_count"]
    delay = compute_backoff_seconds(retry_count)
    next_attempt = now_utc() + timedelta(seconds=delay)
    return _finalize(
        http,
        row,
        {
            "status": "failed",
            "retry_count": retry_count + 1,
            "last_error": CODE_RETRYABLE,
            "next_attempt_at": next_attempt.isoformat(),
        },
    )


def finalize_terminal(http: Http, row: dict, code: str) -> bool:
    """Mark a row terminally failed for manual review: retry_count is pinned
    to max_retries so it can never be selected again, and next_attempt_at is
    cleared so it is never auto-resent."""
    return _finalize(
        http,
        row,
        {
            "status": "failed",
            "retry_count": row["max_retries"],
            "last_error": code,
            "next_attempt_at": None,
        },
    )


# --- Sender ----------------------------------------------------------------


def run_sender(argv: list) -> int:
    """Invoke the allowlisted sender without a shell. Returns its exit code. A
    process-level failure (timeout / OS error) is treated as an exit-2-style
    retryable outcome by the caller; sender stdout/stderr is never captured
    into logs or the DB to avoid leaking message content."""
    proc = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=SENDER_TIMEOUT,
        check=False,
    )
    return proc.returncode


# --- Per-row processing ----------------------------------------------------


def process_row(http: Http, config: Config, row: dict) -> str:
    row_id = row.get("id")
    claimed = claim_row(http, row)
    if claimed is None:
        log(f"row={row_id} skip=lost_race")
        return "lost_race"

    try:
        argv = validate_and_build_argv(config, claimed)
    except RowRejected as exc:
        finalize_terminal(http, claimed, exc.code)
        log(f"row={row_id} manual_review code={exc.code}")
        return "manual_review"

    try:
        rc = run_sender(argv)
    except (OSError, subprocess.SubprocessError):
        # Process/OS exception (incl. timeout) -> exit-2-style retry.
        finalize_retry(http, claimed)
        log(f"row={row_id} failed code={CODE_RETRYABLE} reason=sender_exception")
        return "failed"

    if rc == 0:
        finalize_delivered(http, claimed)
        log(f"row={row_id} delivered")
        return "delivered"
    if rc == 3:
        # Ambiguous: send command succeeded but read-back did not verify.
        # Terminal manual review — never auto-resend.
        finalize_terminal(http, claimed, CODE_MANUAL_REVIEW)
        log(f"row={row_id} manual_review code={CODE_MANUAL_REVIEW}")
        return "manual_review"
    # rc == 2 (safe-before-send/retryable) or any other non-zero code:
    # retryable with backoff.
    finalize_retry(http, claimed)
    log(f"row={row_id} failed code={CODE_RETRYABLE}")
    return "failed"


# --- Run modes -------------------------------------------------------------


def run_dry_run(config: Config) -> int:
    # Keychain availability. The secret is used in-memory only and never logged.
    keychain_ok = True
    service_role = None
    try:
        service_role = load_service_role(config)
    except ConsumerError as exc:
        keychain_ok = False
        log(f"dry-run keychain_error code={exc}")

    sender_ok = config.sender_script.is_file()

    eligible = pending = failed_due = stuck = None
    query_ok = True
    if keychain_ok:
        try:
            http = Http(config, service_role)
            rows = eligible_rows(http, config)
            eligible = len(rows)
            pending = sum(1 for r in rows if r.get("status") == "pending")
            failed_due = sum(1 for r in rows if r.get("status") == "failed")
            stuck = count_stuck_processing(http, config)
        except ConsumerError as exc:
            query_ok = False
            log(f"dry-run query_error code={exc}")

    log(
        "dry-run summary "
        f"keychain={'ok' if keychain_ok else 'unavailable'} "
        f"sender={'ok' if sender_ok else 'missing'} "
        f"query={'ok' if query_ok else 'error'} "
        f"eligible={eligible} pending={pending} "
        f"failed_due={failed_due} stuck_processing={stuck}"
    )
    return 0 if (keychain_ok and sender_ok and query_ok) else 1


def run_once(config: Config) -> int:
    service_role = load_service_role(config)
    http = Http(config, service_role)

    rows = eligible_rows(http, config)
    tally = {"delivered": 0, "failed": 0, "manual_review": 0, "lost_race": 0}
    for row in rows:
        outcome = process_row(http, config, row)
        tally[outcome] = tally.get(outcome, 0) + 1

    stuck = count_stuck_processing(http, config)
    log(
        "once summary "
        f"selected={len(rows)} delivered={tally['delivered']} "
        f"failed={tally['failed']} manual_review={tally['manual_review']} "
        f"lost_race={tally['lost_race']} stuck_processing={stuck}"
    )
    return 0


# --- Locking & entrypoint --------------------------------------------------


def acquire_lock():
    """Take a non-blocking exclusive lock so overlapping launchd invocations
    do not run concurrently. Returns the open fd on success, or None if
    another instance holds the lock."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            return None
        raise
    return fd


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Asteria booking outbox consumer")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config.expanduser())
    except ConsumerError as exc:
        log(f"fatal config code={exc}")
        return 1

    if args.dry_run:
        # Dry-run never mutates and never claims; the lock is unnecessary.
        try:
            return run_dry_run(config)
        except ConsumerError as exc:
            log(f"fatal dry-run code={exc}")
            return 1

    lock_fd = acquire_lock()
    if lock_fd is None:
        log("skip=lock_held")
        return 0
    try:
        return run_once(config)
    except ConsumerError as exc:
        log(f"fatal once code={exc}")
        return 1
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
