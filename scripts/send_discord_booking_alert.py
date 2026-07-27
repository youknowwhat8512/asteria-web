#!/usr/bin/env python3
"""Send one allowlisted Asteria booking alert to a fixed Discord thread.

This is the SSoT delivery leg for the Veronica booking notification pipeline.
The generalized outbox consumer (``scripts/consume_booking_outbox.py``) claims a
privacy-safe ``notification_outbox`` row, validates it, and invokes this sender
with plain, already-KST-formatted fields plus the row's ``--idempotency-key``.

Design guarantees:
  * stdlib only; no third-party dependencies.
  * The Discord bot token is read at runtime from the macOS Keychain and is
    NEVER printed, logged, stored, or placed in process arguments. Only stable,
    opaque status/error codes ever reach stdout/stderr.
  * The message body itself (which contains the booker name, route, and booking
    number) is NEVER printed to logs. It is only sent to Discord and compared.
  * Delivery targets exactly one hard-pinned guild + channel. A config whose
    ``guild_id``/``channel_id`` do not match the compiled constants is rejected
    before any network call.
  * The write is idempotent: a deterministic ``nonce`` derived from the outbox
    idempotency key is sent with ``enforce_nonce=true`` so a duplicate POST for
    the same row is de-duplicated by Discord within the nonce window.
  * ``allowed_mentions`` is ``{"parse": []}`` so the message can never ping
    ``@everyone``, roles, or users regardless of content.
  * After the POST, the created message is re-fetched by id and its channel and
    content are verified before success is reported.

Exit codes (consumed by the outbox consumer):
  0  Delivered AND verified by read-back. Safe to mark the row delivered.
  2  Safe-before-send / retryable: the failure occurred before Discord could
     have created any message (bad config, keychain failure, validation error,
     or a request Discord rejected outright). The row may be retried.
  3  Sent-but-unverified: the POST may have created a message but read-back
     could not confirm it. The consumer must treat this as terminal manual
     review and NEVER auto-resend.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# --- Fixed, non-secret constants ------------------------------------------

DEFAULT_CONFIG = Path.home() / ".config" / "asteria-discord" / "booking-alert.json"

# The one and only delivery target. These are hard-pinned so that a mistaken or
# tampered config can never redirect booking PII to a different server/channel.
EXPECTED_GUILD_ID = "1513551188082294834"
EXPECTED_CHANNEL_ID = "1530999174110121985"

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "AsteriaBookingAlert (https://asteria.club, 1.0)"

EVENT_LABELS = {
    "created": "예약 완료",
    "updated": "예약 변경",
    "cancelled": "예약 취소",
}

BOOKING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Discord nonce values are capped at 25 characters; enforce_nonce dedupes any
# repeat within its window. A truncated sha256 of the idempotency key is stable.
NONCE_LEN = 25
MAX_CONTENT = 2000

HTTP_TIMEOUT = 20
KEYCHAIN_TIMEOUT = 15

# HTTP statuses that mean Discord rejected the request WITHOUT creating a
# message. These are safe-before-send: nothing was delivered, so a retry cannot
# duplicate. Anything else after the POST is initiated is treated as uncertain.
PRE_CREATION_REJECT = frozenset({400, 401, 403, 404, 405, 415, 429})


class SafeBeforeSendError(Exception):
    """Failure guaranteed to be before Discord created any message (exit 2).

    ``str`` is a stable, non-secret code only."""


class UncertainError(Exception):
    """A message may have been created but could not be verified (exit 3).

    ``str`` is a stable, non-secret code only."""


# --- Logging: stable codes only; never the token, message body, or PII -----


def log_status(code: str) -> None:
    print(f"send_discord_booking_alert: {code}", flush=True)


def log_error(code: str) -> None:
    print(f"send_discord_booking_alert: {code}", file=sys.stderr, flush=True)


# --- Message building & validation (all failures are safe-before-send) -----


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


def build_message(args: argparse.Namespace) -> str:
    """Render the fixed booking template. Only the allowlisted booking fields
    ever appear — no phone, cancellation code, attendee details, tokens, or
    secrets are accepted or emitted."""
    if args.event not in EVENT_LABELS:
        raise SafeBeforeSendError("invalid_event")
    if not BOOKING_ID_RE.fullmatch(args.booking_id):
        raise SafeBeforeSendError("invalid_booking_id")
    try:
        dt.date.fromisoformat(args.date)
    except ValueError as exc:
        raise SafeBeforeSendError("invalid_date") from exc
    if not 1 <= args.party_size <= 50:
        raise SafeBeforeSendError("invalid_party_size")

    start_time = validate_time(args.start_time, "start_time")
    end_time = validate_time(args.end_time, "end_time", allow_24=True)
    route = clean_text(args.route, "route", 80)
    name = clean_text(args.name, "name", 40)

    lines = [
        f"⛵ 베로니카 차터 {EVENT_LABELS[args.event]}",
        f"예약번호: {args.booking_id}",
        f"예약일: {args.date}",
        f"운항 시간: {start_time}~{end_time} (KST)",
        f"항로: {route}",
        f"예약자: {name}",
        f"예상 인원: {args.party_size}명",
    ]
    message = "\n".join(lines)
    if len(message) > MAX_CONTENT:
        raise SafeBeforeSendError("message_too_long")
    return message


def compute_nonce(idempotency_key: str) -> str:
    """Deterministic Discord nonce derived from the outbox idempotency key.

    Deterministic so that a retry of the SAME logical row yields the SAME nonce,
    which Discord de-duplicates when ``enforce_nonce`` is set."""
    key = idempotency_key.strip()
    if not key or len(key) > 256:
        raise SafeBeforeSendError("invalid_idempotency_key")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return digest[:NONCE_LEN]


# --- Config & token --------------------------------------------------------


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

    guild_id = str(raw.get("guild_id", "")).strip()
    channel_id = str(raw.get("channel_id", "")).strip()
    if guild_id != EXPECTED_GUILD_ID:
        raise SafeBeforeSendError("config_bad_guild_id")
    if channel_id != EXPECTED_CHANNEL_ID:
        raise SafeBeforeSendError("config_bad_channel_id")

    keychain_bin = str(raw.get("keychain_bin", "")).strip()
    if not keychain_bin or not os.path.isabs(keychain_bin):
        raise SafeBeforeSendError("config_keychain_bin_not_absolute")
    if not (os.path.isfile(keychain_bin) and os.access(keychain_bin, os.X_OK)):
        raise SafeBeforeSendError("config_keychain_bin_not_executable")

    keychain_service = str(raw.get("keychain_service", "")).strip()
    keychain_account = str(raw.get("keychain_account", "")).strip()
    if not keychain_service:
        raise SafeBeforeSendError("config_missing_keychain_service")
    if not keychain_account:
        raise SafeBeforeSendError("config_missing_keychain_account")

    return {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "keychain_bin": keychain_bin,
        "keychain_service": keychain_service,
        "keychain_account": keychain_account,
    }


def load_token(config: dict) -> str:
    """Read the Discord bot token from the Keychain. Returned for immediate
    in-memory use only; it is never logged, stored, or passed as an argument."""
    argv = [
        config["keychain_bin"],
        "get",
        config["keychain_service"],
        config["keychain_account"],
    ]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SafeBeforeSendError("keychain_unavailable") from exc
    if proc.returncode != 0:
        # Never surface stderr — it could echo the service/account context.
        raise SafeBeforeSendError("keychain_lookup_failed")
    token = proc.stdout.strip()
    if not token:
        raise SafeBeforeSendError("keychain_empty")
    return token


# --- Discord REST ----------------------------------------------------------


def _auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bot {token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def post_message(config: dict, token: str, message: str, nonce: str) -> str:
    """POST the message to the pinned channel and return the created message id.

    Raises SafeBeforeSendError only when Discord unambiguously rejected the
    request before creating anything; any other post-initiation failure raises
    UncertainError so the caller never auto-resends a possibly-delivered
    message."""
    body = {
        "content": message,
        "nonce": nonce,
        "enforce_nonce": True,
        # Suppress every mention regardless of message content.
        "allowed_mentions": {"parse": []},
    }
    url = f"{API_BASE}/channels/{config['channel_id']}/messages"
    headers = _auth_headers(token)
    headers["Content-Type"] = "application/json"
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
    except HTTPError as exc:
        status = exc.code
        if status in PRE_CREATION_REJECT:
            # Rejected outright — no message was created; safe to retry.
            raise SafeBeforeSendError(f"post_rejected_{status}") from exc
        # 5xx / unexpected: Discord may or may not have created the message.
        raise UncertainError(f"post_http_{status}") from exc
    except (URLError, OSError) as exc:
        # Transport error mid-POST: cannot prove nothing was created.
        raise UncertainError("post_transport_error") from exc

    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UncertainError("post_bad_json") from exc
    if not isinstance(obj, dict):
        raise UncertainError("post_bad_json")

    message_id = str(obj.get("id", ""))
    channel_id = str(obj.get("channel_id", ""))
    content = obj.get("content", "")
    if not message_id:
        raise UncertainError("post_missing_id")
    if channel_id != config["channel_id"]:
        raise UncertainError("post_channel_mismatch")
    if content != message:
        raise UncertainError("post_content_mismatch")
    return message_id


def verify_readback(config: dict, token: str, message_id: str, message: str) -> None:
    """Re-fetch the created message by id and verify its channel and content.

    Any failure here raises UncertainError: the POST reported success, so a
    read-back miss means manual review, never an automatic resend."""
    url = f"{API_BASE}/channels/{config['channel_id']}/messages/{message_id}"
    req = Request(url, method="GET", headers=_auth_headers(token))
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
    except (HTTPError, URLError, OSError) as exc:
        raise UncertainError("readback_failed") from exc

    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UncertainError("readback_bad_json") from exc
    if not isinstance(obj, dict):
        raise UncertainError("readback_bad_json")

    if str(obj.get("id", "")) != message_id:
        raise UncertainError("readback_id_mismatch")
    if str(obj.get("channel_id", "")) != config["channel_id"]:
        raise UncertainError("readback_channel_mismatch")
    if obj.get("content", "") != message:
        raise UncertainError("readback_content_mismatch")


# --- Entrypoint ------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Send one allowlisted Asteria booking alert to Discord"
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
        help="Print the rendered message and exit; no config, keychain, or network.",
    )
    args = parser.parse_args(argv)

    # Phase 1: everything that must succeed BEFORE any network call. Any failure
    # here is safe-before-send (exit 2) — nothing has been delivered.
    try:
        message = build_message(args)
        nonce = compute_nonce(args.idempotency_key)
        if args.render_only:
            # Deliberate local preview only; the launchd path never uses this.
            print(message)
            return 0
        config = load_config(args.config.expanduser())
        token = load_token(config)
    except SafeBeforeSendError as exc:
        log_error(f"safe_before_send code={exc}")
        return 2

    # Phase 2: the POST. A pre-creation rejection is still safe (exit 2); any
    # other post-initiation failure is uncertain (exit 3).
    try:
        message_id = post_message(config, token, message, nonce)
    except SafeBeforeSendError as exc:
        log_error(f"safe_before_send code={exc}")
        return 2
    except UncertainError as exc:
        log_error(f"sent_but_unverified code={exc}")
        return 3

    # Phase 3: read-back verification. Only exit 0 after this confirms.
    try:
        verify_readback(config, token, message_id, message)
    except UncertainError as exc:
        log_error(f"sent_but_unverified code={exc}")
        return 3

    log_status("sent_and_verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
