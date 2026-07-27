#!/usr/bin/env python3
"""Upsert one Asteria booking into the fixed Veronica Google Calendar.

This is the second idempotent delivery leg for the Veronica booking pipeline,
alongside ``scripts/send_discord_booking_alert.py``. The wrapper
``scripts/send_booking_integrations.py`` runs this leg first and only then the
Discord leg; the outbox consumer invokes the wrapper. This sender takes the
SAME plain, already-KST-formatted CLI fields as the Discord sender (including
``--event`` and ``--idempotency-key``).

Design guarantees:
  * stdlib only; no third-party dependencies (no google client library).
  * OAuth credentials (client id/secret, refresh token, token uri) are read at
    runtime from a hard-pinned 0600 credentials file and used only in memory.
    The refresh token, the minted access token, credential values, the message
    body, and every Google HTTP response body are NEVER printed, logged,
    stored, or placed in process arguments. Only stable, opaque status/error
    codes ever reach stdout/stderr.
  * Delivery targets exactly one hard-pinned Google user + calendar. A config
    whose ``user_email``/``calendar_id``/``credentials_file`` do not match the
    compiled constants is rejected before any network call.
  * The write is idempotent: the Google event id is a deterministic lowercase
    hex sha256 of the booking id (a valid ``[a-v0-9]`` Calendar event id), so
    created/updated/cancelled all address the SAME single event per booking.
    ``sendUpdates=none`` suppresses all guest notifications.
  * After every write the event is re-fetched and its id, calendar-scoped
    fields, start, end, status, and content are verified before success.

Exit codes (consumed by the wrapper):
  0  Written AND verified by read-back (or a cancel confirmed absent). Safe.
  2  Provably safe-before-write / retryable: the failure occurred before Google
     could change anything (bad config/credentials, OAuth refresh failure,
     validation error, or a request Google rejected outright without a change),
     or a 409/404 conflict proved no change was made. Safe to retry.
  3  Ambiguous: a write may have changed calendar state but the read-back could
     not confirm the intended result. Terminal manual review; never auto-retry.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

# --- Fixed, non-secret constants ------------------------------------------

DEFAULT_CONFIG = (
    Path.home() / ".config" / "asteria-google-calendar" / "booking-alert.json"
)

# The one and only Google delivery target. These are hard-pinned so a mistaken
# or tampered config can never redirect booking PII to a different account or
# calendar.
EXPECTED_USER_EMAIL = "asteriayachtclub@gmail.com"
EXPECTED_CALENDAR_ID = (
    "7f48e83938cf52b65ba95144dd9786efcbcc8862063af4846af22341ab8eac81"
    "@group.calendar.google.com"
)
EXPECTED_CREDENTIALS_FILE = (
    "/Users/ja/.google_workspace_mcp/credentials/asteriayachtclub@gmail.com.json"
)

# Summary title base. The booker name is appended per booking.
CALENDAR_TITLE = "베로니카 차터 예약"
# Non-secret marker written to the description and extendedProperties so the
# event's provenance is machine-identifiable without any PII.
SOURCE_MARKER = "asteria-veronica-booking"

API_BASE = "https://www.googleapis.com/calendar/v3"
USER_AGENT = "AsteriaBookingCalendar (https://asteria.club, 1.0)"
KST = ZoneInfo("Asia/Seoul")

EVENT_LABELS = frozenset({"created", "updated", "cancelled"})

BOOKING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Google Calendar event ids must match [a-v0-9] and be 5..1024 chars. A
# lowercase hex sha256 (0-9a-f, 64 chars) always satisfies this.
EVENT_ID_RE = re.compile(r"^[a-v0-9]{5,1024}$")
ROUTE_SEP = " → "

HTTP_TIMEOUT = 20

# HTTP statuses that mean Google rejected the request WITHOUT changing calendar
# state. These are safe-before-write: nothing was written, so a retry cannot
# duplicate or corrupt. 404/409 are handled contextually per operation.
PRE_WRITE_REJECT = frozenset({400, 401, 403, 405, 412, 415, 429})


class SafeBeforeSendError(Exception):
    """Failure guaranteed to leave calendar state unchanged (exit 2).

    ``str`` is a stable, non-secret code only."""


class UncertainError(Exception):
    """A write may have changed state but could not be verified (exit 3).

    ``str`` is a stable, non-secret code only."""


class TransportError(Exception):
    """Network-level failure of a single request; the caller decides whether
    that leaves state provably unchanged or ambiguous."""


# --- Logging: stable codes only; never token, credentials, body, or PII -----


def log_status(code: str) -> None:
    print(f"send_google_calendar_booking: {code}", flush=True)


def log_error(code: str) -> None:
    print(f"send_google_calendar_booking: {code}", file=sys.stderr, flush=True)


# --- Validation & event construction (all failures are safe-before-write) ---


def clean_text(value: str, field: str, limit: int) -> str:
    value = " ".join(value.split()).strip()
    if not value or len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise SafeBeforeSendError(f"invalid_{field}")
    return value


def validate_time(value: str, field: str, *, allow_24: bool = False) -> str:
    if allow_24 and value == "24:00":
        return value
    if not TIME_RE.fullmatch(value):
        raise SafeBeforeSendError(f"invalid_{field}")
    return value


def compute_event_id(booking_id: str) -> str:
    """Deterministic Google Calendar event id derived from the booking id.

    Lowercase hex sha256 is a valid ``[a-v0-9]`` event id, so a retry — or a
    later update/cancel — of the SAME booking always addresses the SAME single
    event. This is what makes every leg idempotent."""
    digest = hashlib.sha256(booking_id.encode("utf-8")).hexdigest()
    if not EVENT_ID_RE.fullmatch(digest):  # pragma: no cover - hex is always valid
        raise SafeBeforeSendError("event_id_invalid")
    return digest


def build_plan(args: argparse.Namespace) -> dict:
    """Validate the CLI fields and build the event id, request body, and the
    expected-values map used for read-back verification. Only allowlisted
    booking fields appear — no phone, cancellation code, passenger details,
    attendees, tokens, or secrets are accepted or emitted."""
    if args.event not in EVENT_LABELS:
        raise SafeBeforeSendError("invalid_event")
    if not BOOKING_ID_RE.fullmatch(args.booking_id):
        raise SafeBeforeSendError("invalid_booking_id")
    try:
        date = dt.date.fromisoformat(args.date)
    except ValueError as exc:
        raise SafeBeforeSendError("invalid_date") from exc
    if not 1 <= args.party_size <= 50:
        raise SafeBeforeSendError("invalid_party_size")

    start_time = validate_time(args.start_time, "start_time")
    end_time = validate_time(args.end_time, "end_time", allow_24=True)
    route = clean_text(args.route, "route", 80)
    name = clean_text(args.name, "name", 40)

    idempotency_key = args.idempotency_key.strip()
    if not idempotency_key or len(idempotency_key) > 256:
        raise SafeBeforeSendError("invalid_idempotency_key")

    # The consumer builds route as "{departure} → {destination}"; the event
    # location is the departure leg only.
    departure = route.split(ROUTE_SEP, 1)[0].strip()
    if not departure:
        raise SafeBeforeSendError("invalid_departure")

    # Local (Asia/Seoul) start/end. A legal 24:00 end maps to next-day midnight.
    start_local = f"{args.date}T{start_time}:00"
    start_dt = dt.datetime.fromisoformat(start_local).replace(tzinfo=KST)
    if end_time == "24:00":
        end_date = date + dt.timedelta(days=1)
        end_local = f"{end_date.isoformat()}T00:00:00"
    else:
        end_local = f"{args.date}T{end_time}:00"
    end_dt = dt.datetime.fromisoformat(end_local).replace(tzinfo=KST)
    if end_dt <= start_dt:
        raise SafeBeforeSendError("invalid_time_range")

    event_id = compute_event_id(args.booking_id)
    summary = f"{CALENDAR_TITLE} · {name}"
    description = "\n".join(
        [
            f"예약번호: {args.booking_id}",
            f"항로: {route}",
            f"예상 인원: {args.party_size}명",
            f"source: {SOURCE_MARKER}",
        ]
    )
    ext_private = {
        "asteria_source": SOURCE_MARKER,
        "asteria_booking_id": args.booking_id,
        "asteria_idempotency_key": idempotency_key,
    }

    body = {
        "id": event_id,
        "summary": summary,
        "location": departure,
        "description": description,
        "start": {"dateTime": start_local, "timeZone": "Asia/Seoul"},
        "end": {"dateTime": end_local, "timeZone": "Asia/Seoul"},
        "status": "confirmed",
        "visibility": "private",
        "transparency": "opaque",
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {"private": ext_private},
    }
    expected = {
        "event_id": event_id,
        "summary": summary,
        "location": departure,
        "description": description,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "ext": ext_private,
    }
    return {"event_id": event_id, "body": body, "expected": expected}


# --- Config & credentials --------------------------------------------------


def load_config(path: Path) -> dict:
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        raise SafeBeforeSendError("config_not_found") from exc
    if info.st_uid != os.getuid():
        raise SafeBeforeSendError("config_not_owned_by_user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SafeBeforeSendError("config_permissions_too_open")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafeBeforeSendError("config_invalid_json") from exc
    if not isinstance(raw, dict):
        raise SafeBeforeSendError("config_not_object")

    user_email = str(raw.get("user_email", "")).strip()
    calendar_id = str(raw.get("calendar_id", "")).strip()
    if user_email != EXPECTED_USER_EMAIL:
        raise SafeBeforeSendError("config_bad_user_email")
    if calendar_id != EXPECTED_CALENDAR_ID:
        raise SafeBeforeSendError("config_bad_calendar_id")

    credentials_file = str(raw.get("credentials_file", "")).strip()
    if not credentials_file or not os.path.isabs(credentials_file):
        raise SafeBeforeSendError("config_credentials_not_absolute")
    if os.path.realpath(credentials_file) != os.path.realpath(
        EXPECTED_CREDENTIALS_FILE
    ):
        raise SafeBeforeSendError("config_credentials_not_allowlisted")

    return {
        "user_email": user_email,
        "calendar_id": calendar_id,
        "credentials_file": credentials_file,
    }


def load_credentials(config: dict) -> dict:
    """Read the OAuth credentials from the hard-pinned 0600 file. Returned for
    immediate in-memory use only; values are never logged, stored, or passed as
    arguments."""
    path = Path(config["credentials_file"])
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        raise SafeBeforeSendError("credentials_not_found") from exc
    if info.st_uid != os.getuid():
        raise SafeBeforeSendError("credentials_not_owned_by_user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SafeBeforeSendError("credentials_permissions_too_open")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafeBeforeSendError("credentials_invalid_json") from exc
    if not isinstance(raw, dict):
        raise SafeBeforeSendError("credentials_not_object")

    creds = {}
    for key in ("client_id", "client_secret", "refresh_token", "token_uri"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SafeBeforeSendError("credentials_missing_field")
        creds[key] = value.strip()
    # token_uri must be HTTPS so a tampered file cannot exfiltrate the refresh
    # token over cleartext.
    if not creds["token_uri"].lower().startswith("https://"):
        raise SafeBeforeSendError("credentials_token_uri_not_https")
    return creds


def refresh_access_token(creds: dict) -> str:
    """Exchange the refresh token for a short-lived access token, entirely in
    memory. Any failure here is before any calendar write (exit 2). The token
    and every response detail are never logged."""
    data = urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = Request(
        creds["token_uri"],
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
    except (HTTPError, URLError, OSError) as exc:
        # No calendar write has occurred; the whole delivery is safe to retry.
        raise SafeBeforeSendError("oauth_refresh_failed") from exc
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SafeBeforeSendError("oauth_bad_response") from exc
    token = obj.get("access_token") if isinstance(obj, dict) else None
    if not isinstance(token, str) or not token:
        raise SafeBeforeSendError("oauth_no_access_token")
    return token


# --- Google Calendar REST --------------------------------------------------


def _events_collection_url(config: dict) -> str:
    return f"{API_BASE}/calendars/{quote(config['calendar_id'], safe='')}/events"


def _event_url(config: dict, event_id: str) -> str:
    return f"{_events_collection_url(config)}/{event_id}"


def _request(method: str, url: str, token: str, body=None):
    """Perform one Calendar request. Returns ``(status_code, obj_or_None)``.

    HTTP error statuses are returned (not raised) so callers can branch on
    them; only genuine transport failures raise ``TransportError``. Response
    bodies are parsed but NEVER logged."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
            code = resp.status
    except HTTPError as exc:
        code = exc.code
        try:
            raw = exc.read()
        except OSError:
            raw = b""
    except (URLError, OSError) as exc:
        raise TransportError("transport_error") from exc

    obj = None
    if raw:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            obj = parsed
    return code, obj


def _insert(config: dict, token: str, body: dict):
    """POST the event with our supplied id. Returns ('ok', obj) on creation,
    ('conflict', None) on 409 (an event with this id already exists)."""
    url = _events_collection_url(config) + "?sendUpdates=none"
    try:
        code, obj = _request("POST", url, token, body=body)
    except TransportError as exc:
        raise UncertainError("insert_transport") from exc
    if code in (200, 201):
        return "ok", obj
    if code == 409:
        return "conflict", None
    if code == 404 or code in PRE_WRITE_REJECT:
        raise SafeBeforeSendError(f"insert_rejected_{code}")
    raise UncertainError(f"insert_uncertain_{code}")


def _update(config: dict, token: str, event_id: str, body: dict):
    """PUT the full deterministic event. Returns ('ok', obj) or ('not_found',
    None) on 404 (no such event yet)."""
    url = _event_url(config, event_id) + "?sendUpdates=none"
    try:
        code, obj = _request("PUT", url, token, body=body)
    except TransportError as exc:
        raise UncertainError("update_transport") from exc
    if code == 200:
        return "ok", obj
    if code == 404:
        return "not_found", None
    if code in PRE_WRITE_REJECT:
        raise SafeBeforeSendError(f"update_rejected_{code}")
    raise UncertainError(f"update_uncertain_{code}")


def _delete(config: dict, token: str, event_id: str) -> str:
    """DELETE the deterministic event. Returns 'deleted' on 200/204 or 'gone'
    on 404/410 (already absent — an idempotent success)."""
    url = _event_url(config, event_id) + "?sendUpdates=none"
    try:
        code, _obj = _request("DELETE", url, token)
    except TransportError as exc:
        raise UncertainError("delete_transport") from exc
    if code in (200, 204):
        return "deleted"
    if code in (404, 410):
        return "gone"
    if code in PRE_WRITE_REJECT:
        raise SafeBeforeSendError(f"delete_rejected_{code}")
    raise UncertainError(f"delete_uncertain_{code}")


def _get(config: dict, token: str, event_id: str):
    """GET the deterministic event. Returns ``(status_code, obj_or_None)``;
    raises ``TransportError`` on a network failure."""
    return _request("GET", _event_url(config, event_id), token)


# --- Verification ----------------------------------------------------------


def _verify_time(node, expected_dt: dt.datetime, label: str) -> None:
    if not isinstance(node, dict):
        raise UncertainError(f"verify_{label}_shape")
    if node.get("timeZone") != "Asia/Seoul":
        raise UncertainError(f"verify_{label}_tz")
    raw = node.get("dateTime")
    if not isinstance(raw, str):
        raise UncertainError(f"verify_{label}_missing")
    try:
        got = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise UncertainError(f"verify_{label}_parse") from exc
    if got.tzinfo is None:
        got = got.replace(tzinfo=KST)
    if got != expected_dt:
        raise UncertainError(f"verify_{label}_value")


def verify_event(got: dict, expected: dict) -> None:
    """Confirm the fetched event exactly matches the intended state. Any
    mismatch is UncertainError: a write already happened, so a divergent
    read-back is manual review, never an automatic retry."""
    if not isinstance(got, dict):
        raise UncertainError("verify_shape")
    if str(got.get("id", "")) != expected["event_id"]:
        raise UncertainError("verify_id")
    if got.get("summary") != expected["summary"]:
        raise UncertainError("verify_summary")
    if got.get("location") != expected["location"]:
        raise UncertainError("verify_location")
    if got.get("description") != expected["description"]:
        raise UncertainError("verify_description")
    if got.get("visibility") != "private":
        raise UncertainError("verify_visibility")
    if got.get("status") != "confirmed":
        raise UncertainError("verify_status")
    _verify_time(got.get("start"), expected["start_dt"], "start")
    _verify_time(got.get("end"), expected["end_dt"], "end")
    private = got.get("extendedProperties")
    private = private.get("private") if isinstance(private, dict) else None
    if not isinstance(private, dict):
        raise UncertainError("verify_ext_missing")
    for key, value in expected["ext"].items():
        if private.get(key) != value:
            raise UncertainError(f"verify_ext_{key}")


def _readback_verify(config: dict, token: str, expected: dict) -> None:
    try:
        code, got = _get(config, token, expected["event_id"])
    except TransportError as exc:
        raise UncertainError("readback_transport") from exc
    if code != 200 or got is None:
        raise UncertainError(f"readback_status_{code}")
    verify_event(got, expected)


# --- Operations ------------------------------------------------------------


def do_upsert_created(config: dict, token: str, plan: dict) -> None:
    outcome, _obj = _insert(config, token, plan["body"])
    if outcome == "conflict":
        # An event with this deterministic id already exists — the insert made
        # no change. Confirm it matches what we intended.
        try:
            code, got = _get(config, token, plan["event_id"])
        except TransportError as exc:
            # Insert changed nothing (409); the whole delivery is safe to retry.
            raise SafeBeforeSendError("create_conflict_get_transport") from exc
        if code == 200 and got is not None:
            verify_event(got, plan["expected"])
            return
        if code in (404, 410):
            # Vanished between the 409 and the GET; a retry would re-create it.
            raise SafeBeforeSendError("create_conflict_get_absent")
        raise UncertainError(f"create_conflict_get_{code}")
    _readback_verify(config, token, plan["expected"])


def do_upsert_updated(config: dict, token: str, plan: dict) -> None:
    outcome, _obj = _update(config, token, plan["event_id"], plan["body"])
    if outcome == "not_found":
        # No event yet — create it with the same deterministic id.
        ins, _iobj = _insert(config, token, plan["body"])
        if ins == "conflict":
            # Raced with a concurrent create between our 404 and this insert.
            try:
                code, got = _get(config, token, plan["event_id"])
            except TransportError as exc:
                raise UncertainError("update_insert_conflict_get_transport") from exc
            if code == 200 and got is not None:
                verify_event(got, plan["expected"])
                return
            raise UncertainError(f"update_insert_conflict_get_{code}")
    _readback_verify(config, token, plan["expected"])


def do_cancel(config: dict, token: str, plan: dict) -> None:
    # 'deleted' and 'gone' (404/410) are both idempotent successes; a pre-write
    # rejection raises SafeBeforeSendError inside _delete.
    _delete(config, token, plan["event_id"])
    # Confirm the event is truly absent.
    try:
        code, _got = _get(config, token, plan["event_id"])
    except TransportError as exc:
        raise UncertainError("cancel_verify_transport") from exc
    if code in (404, 410):
        return
    if code == 200 and isinstance(_got, dict):
        # Google Calendar may retain a deleted event as a cancellation
        # tombstone instead of returning 404/410. This is the documented
        # successful terminal state as long as it is the same deterministic
        # event and is explicitly marked cancelled.
        if (
            str(_got.get("id", "")) == plan["event_id"]
            and _got.get("status") == "cancelled"
        ):
            return
        raise UncertainError("cancel_verify_still_present")
    raise UncertainError(f"cancel_verify_{code}")


DISPATCH = {
    "created": do_upsert_created,
    "updated": do_upsert_updated,
    "cancelled": do_cancel,
}


# --- Entrypoint ------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Upsert one Asteria booking into the fixed Veronica calendar"
    )
    parser.add_argument("--event", choices=sorted(EVENT_LABELS), required=True)
    parser.add_argument("--booking-id", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--end-time", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--party-size", type=int, required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Print the event body and exit; no config, credentials, or network.",
    )
    args = parser.parse_args(argv)

    # Phase 1: everything that must succeed BEFORE any calendar write. Any
    # failure here leaves calendar state unchanged (exit 2).
    try:
        plan = build_plan(args)
        if args.render_only:
            # Deliberate local preview only; the launchd path never uses this.
            print(json.dumps(plan["body"], ensure_ascii=False, indent=2))
            return 0
        config = load_config(args.config.expanduser())
        creds = load_credentials(config)
        token = refresh_access_token(creds)
    except SafeBeforeSendError as exc:
        log_error(f"safe_before_write code={exc}")
        return 2

    # Phase 2: the write + read-back verification. A pre-write rejection or a
    # proven-no-change conflict is safe (exit 2); any post-write ambiguity is
    # uncertain (exit 3).
    try:
        DISPATCH[args.event](config, token, plan)
    except SafeBeforeSendError as exc:
        log_error(f"safe_before_write code={exc}")
        return 2
    except UncertainError as exc:
        log_error(f"wrote_but_unverified code={exc}")
        return 3

    log_status("wrote_and_verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
