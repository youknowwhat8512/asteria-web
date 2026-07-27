#!/usr/bin/env python3
"""Stdlib unittest coverage for scripts/consume_booking_outbox.py.

These tests NEVER touch the network, the Keychain, or a real sender. The URL
opener and subprocess.run are mocked; the sender script is never executed.
"""

import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import consume_booking_outbox as consume  # noqa: E402

SECRET_SENTINEL = "SERVICE_ROLE_SENTINEL_DO_NOT_LOG"
ROW_ID = "11111111-1111-1111-1111-111111111111"
IDEMPOTENCY_KEY = "opaque-key-should-never-be-logged"


def valid_payload():
    # 04:00Z / 07:00Z -> Asia/Seoul 13:00 / 16:00 on 2026-08-15.
    return {
        "event_type": "booking_created",
        "booking_code": "VER-260815",
        "vessel": "veronica",
        "starts_at": "2026-08-15T04:00:00+00:00",
        "ends_at": "2026-08-15T07:00:00+00:00",
        "departure": "아라마리나",
        "destination": "팔미도",
        "total_people": 4,
        "booker_name": "홍길동",
    }


def make_row(status="pending", retry_count=0, max_retries=5, payload=None,
             next_attempt_at=None, event_type="booking_created",
             idempotency_key=IDEMPOTENCY_KEY):
    return {
        "id": ROW_ID,
        "event_type": event_type,
        "booking_id": "22222222-2222-2222-2222-222222222222",
        "payload": valid_payload() if payload is None else payload,
        "idempotency_key": idempotency_key,
        "status": status,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "last_error": None,
        "next_attempt_at": next_attempt_at,
        "delivered_at": None,
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00",
    }


def make_config(activated="2026-07-01T00:00:00+00:00", batch_size=5):
    return consume.Config(
        project_ref=consume.EXPECTED_PROJECT_REF,
        activated_at=consume.parse_rfc3339(activated),
        sender_script=consume.EXPECTED_SENDER,
        keychain_bin=consume.EXPECTED_KEYCHAIN_BIN,
        batch_size=batch_size,
    )


class FakeResp:
    def __init__(self, obj):
        self._data = json.dumps(obj).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Records every request and returns whatever ``responder`` produces."""

    def __init__(self, responder):
        self.responder = responder
        self.calls = []  # list of (method, url, body)

    def __call__(self, req, timeout=None):
        method = req.get_method()
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.calls.append((method, req.full_url, body))
        return FakeResp(self.responder(method, req.full_url, body))

    def patch_bodies(self, status):
        return [c[2] for c in self.calls if c[0] == "PATCH" and c[2].get("status") == status]

    @property
    def patch_calls(self):
        return [c for c in self.calls if c[0] == "PATCH"]


class FakeSender:
    def __init__(self, returncode):
        self.returncode = returncode
        self.argv = None

    def __call__(self, argv, **kwargs):
        self.argv = argv
        return types.SimpleNamespace(returncode=self.returncode)


def default_responder(row, claim_wins=True):
    """Mirror PostgREST: the conditional claim returns the same row flipped to
    'processing' (or [] if another actor won); finalize PATCHes echo one row."""
    def responder(method, url, body):
        if method == "PATCH":
            if body.get("status") == "processing":
                return [dict(row, status="processing")] if claim_wins else []
            return [dict(row, **body)]
        return []
    return responder


def run_process_row(row, responder, sender_rc):
    opener = FakeOpener(responder)
    sender = FakeSender(sender_rc)
    buf = io.StringIO()
    with mock.patch.object(consume, "urlopen", opener), \
            mock.patch.object(consume.subprocess, "run", sender), \
            redirect_stdout(buf):
        http = consume.Http(make_config(), SECRET_SENTINEL)
        outcome = consume.process_row(http, make_config(), row)
    return outcome, opener, sender, buf.getvalue()


class TimeAndBackoffTests(unittest.TestCase):
    def test_parse_rfc3339_requires_offset(self):
        with self.assertRaises(consume.ConsumerError):
            consume.parse_rfc3339("2026-07-01T00:00:00")

    def test_parse_rfc3339_accepts_z(self):
        dt = consume.parse_rfc3339("2026-07-01T00:00:00Z")
        self.assertEqual(dt.utcoffset().total_seconds(), 0)

    def test_backoff_exponential_and_capped(self):
        self.assertEqual(consume.compute_backoff_seconds(0), 60)
        self.assertEqual(consume.compute_backoff_seconds(1), 120)
        self.assertEqual(consume.compute_backoff_seconds(2), 240)
        self.assertEqual(consume.compute_backoff_seconds(20), consume.BACKOFF_CAP_SECONDS)


class ValidationTests(unittest.TestCase):
    def test_kst_formatting_and_idempotency_key(self):
        argv = consume.validate_and_build_argv(make_config(), make_row())
        self.assertIn("--date", argv)
        self.assertEqual(argv[argv.index("--date") + 1], "2026-08-15")
        self.assertEqual(argv[argv.index("--start-time") + 1], "13:00")
        self.assertEqual(argv[argv.index("--end-time") + 1], "16:00")
        self.assertEqual(argv[argv.index("--route") + 1], "아라마리나 → 팔미도")
        self.assertEqual(argv[argv.index("--event") + 1], "created")
        # The row idempotency key is forwarded verbatim for deterministic nonce.
        self.assertEqual(argv[argv.index("--idempotency-key") + 1], IDEMPOTENCY_KEY)
        # No Kakao-era fields leak into the wrapper argv.
        self.assertNotIn("--member-included", argv)
        self.assertNotIn("--calendar-url", argv)
        # The consumer targets the pinned two-leg integration wrapper (which
        # runs Google Calendar first and only then Discord), not a single sender.
        self.assertEqual(argv[1], str(consume.EXPECTED_SENDER))
        self.assertEqual(Path(argv[1]).name, "send_booking_integrations.py")

    def test_midnight_end_formats_as_24_00(self):
        payload = valid_payload()
        payload["starts_at"] = "2026-08-15T12:00:00+00:00"  # KST 21:00
        payload["ends_at"] = "2026-08-15T15:00:00+00:00"    # KST next day 00:00
        argv = consume.validate_and_build_argv(make_config(), make_row(payload=payload))
        self.assertEqual(argv[argv.index("--date") + 1], "2026-08-15")
        self.assertEqual(argv[argv.index("--start-time") + 1], "21:00")
        self.assertEqual(argv[argv.index("--end-time") + 1], "24:00")

    def test_missing_idempotency_key_rejected(self):
        with self.assertRaises(consume.RowRejected):
            consume.validate_and_build_argv(
                make_config(), make_row(idempotency_key=""))
        with self.assertRaises(consume.RowRejected):
            consume.validate_and_build_argv(
                make_config(), make_row(idempotency_key=None))

    def test_forbidden_key_rejected(self):
        payload = valid_payload()
        payload["phone"] = "010-0000-0000"
        with self.assertRaises(consume.RowRejected):
            consume.validate_and_build_argv(make_config(), make_row(payload=payload))

    def test_forbidden_key_nested_rejected(self):
        payload = valid_payload()
        payload["destination"] = "팔미도"
        payload["extra"] = {"passengers": ["a", "b"]}
        with self.assertRaises(consume.RowRejected):
            consume.validate_and_build_argv(make_config(), make_row(payload=payload))

    def test_unexpected_top_level_key_rejected(self):
        payload = valid_payload()
        payload["cancel_code"] = "WAVE-1234"
        with self.assertRaises(consume.RowRejected):
            consume.validate_and_build_argv(make_config(), make_row(payload=payload))

    def test_kst_date_mismatch_rejected(self):
        payload = valid_payload()
        payload["ends_at"] = "2026-08-15T16:00:00+00:00"  # -> KST next day 01:00
        with self.assertRaises(consume.RowRejected):
            consume.validate_and_build_argv(make_config(), make_row(payload=payload))


class EligibilityTests(unittest.TestCase):
    def test_cutoff_query_includes_activated_at(self):
        opener = FakeOpener(lambda m, u, b: [])
        with mock.patch.object(consume, "urlopen", opener):
            http = consume.Http(make_config(activated="2026-07-15T00:00:00+00:00"),
                                SECRET_SENTINEL)
            consume.eligible_rows(http, make_config(activated="2026-07-15T00:00:00+00:00"))
        self.assertEqual(len(opener.calls), 1)
        url = opener.calls[0][1]
        self.assertIn("created_at", url)
        self.assertIn("gte.2026-07-15", url)
        self.assertIn("status=in", url)

    def test_failed_row_eligibility_filtering(self):
        rows = [
            make_row(status="pending"),
            make_row(status="failed", retry_count=1, max_retries=5,
                     next_attempt_at="2000-01-01T00:00:00+00:00"),   # due -> eligible
            make_row(status="failed", retry_count=1, max_retries=5,
                     next_attempt_at="2999-01-01T00:00:00+00:00"),   # not due -> skip
            make_row(status="failed", retry_count=5, max_retries=5,
                     next_attempt_at="2000-01-01T00:00:00+00:00"),   # exhausted -> skip
        ]
        opener = FakeOpener(lambda m, u, b: rows)
        with mock.patch.object(consume, "urlopen", opener):
            http = consume.Http(make_config(), SECRET_SENTINEL)
            eligible = consume.eligible_rows(http, make_config())
        statuses = [r["status"] for r in eligible]
        self.assertEqual(statuses, ["pending", "failed"])


class ProcessRowTests(unittest.TestCase):
    def test_success_delivered(self):
        row = make_row()
        outcome, opener, sender, logs = run_process_row(
            row, default_responder(row), sender_rc=0)
        self.assertEqual(outcome, "delivered")
        # sender argv is correct and shell-free
        self.assertEqual(sender.argv[0], sys.executable)
        self.assertIn("--event", sender.argv)
        self.assertEqual(sender.argv[sender.argv.index("--event") + 1], "created")
        self.assertIn("--idempotency-key", sender.argv)
        # delivered finalize body
        delivered = opener.patch_bodies("delivered")
        self.assertEqual(len(delivered), 1)
        self.assertIsNone(delivered[0]["last_error"])
        self.assertIsNone(delivered[0]["next_attempt_at"])
        self.assertIsNotNone(delivered[0]["delivered_at"])

    def test_atomic_claim_race_skips_without_sending(self):
        row = make_row()
        outcome, opener, sender, logs = run_process_row(
            row, default_responder(row, claim_wins=False), sender_rc=0)
        self.assertEqual(outcome, "lost_race")
        self.assertIsNone(sender.argv)  # sender never invoked
        self.assertEqual(opener.patch_bodies("delivered"), [])
        self.assertEqual(opener.patch_bodies("failed"), [])

    def test_exit2_backoff_retry(self):
        row = make_row(retry_count=1)
        outcome, opener, sender, logs = run_process_row(
            row, default_responder(row), sender_rc=2)
        self.assertEqual(outcome, "failed")
        failed = opener.patch_bodies("failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["retry_count"], 2)  # 1 -> 2
        self.assertEqual(failed[0]["last_error"], consume.CODE_RETRYABLE)
        self.assertIsNotNone(failed[0]["next_attempt_at"])

    def test_exit3_terminal_no_retry(self):
        row = make_row()
        outcome, opener, sender, logs = run_process_row(
            row, default_responder(row), sender_rc=3)
        self.assertEqual(outcome, "manual_review")
        failed = opener.patch_bodies("failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["retry_count"], 5)  # pinned to max_retries
        self.assertEqual(failed[0]["last_error"], consume.CODE_MANUAL_REVIEW)
        self.assertIsNone(failed[0]["next_attempt_at"])  # never auto-resent

    def test_sender_exception_is_retryable(self):
        row = make_row()
        opener = FakeOpener(default_responder(row))

        def boom(argv, **kwargs):
            raise OSError("sender vanished")

        buf = io.StringIO()
        with mock.patch.object(consume, "urlopen", opener), \
                mock.patch.object(consume.subprocess, "run", boom), \
                redirect_stdout(buf):
            http = consume.Http(make_config(), SECRET_SENTINEL)
            outcome = consume.process_row(http, make_config(), row)
        self.assertEqual(outcome, "failed")
        self.assertEqual(len(opener.patch_bodies("failed")), 1)

    def test_forbidden_payload_terminal_without_sending(self):
        payload = valid_payload()
        payload["waiver_text"] = "leaked"
        row = make_row(payload=payload)
        outcome, opener, sender, logs = run_process_row(
            row, default_responder(row), sender_rc=0)
        self.assertEqual(outcome, "manual_review")
        self.assertIsNone(sender.argv)  # sender never invoked on a bad payload
        failed = opener.patch_bodies("failed")
        self.assertEqual(failed[0]["last_error"], consume.CODE_VALIDATION)

    def test_no_secret_or_pii_in_logs(self):
        row = make_row()
        outcome, opener, sender, logs = run_process_row(
            row, default_responder(row), sender_rc=0)
        # Opaque row id is allowed; secrets/PII/keys must never appear.
        self.assertIn(ROW_ID, logs)
        for forbidden in (SECRET_SENTINEL, "홍길동", "VER-260815", "팔미도",
                          "아라마리나", IDEMPOTENCY_KEY):
            self.assertNotIn(forbidden, logs)


class DryRunTests(unittest.TestCase):
    def test_dry_run_mutates_nothing(self):
        opener = FakeOpener(lambda m, u, b: [])
        sender = FakeSender(0)
        buf = io.StringIO()
        with mock.patch.object(consume, "urlopen", opener), \
                mock.patch.object(consume.subprocess, "run", sender), \
                mock.patch.object(consume, "load_service_role",
                                  return_value=SECRET_SENTINEL), \
                redirect_stdout(buf):
            rc = consume.run_dry_run(make_config())
        self.assertEqual(rc, 0)
        self.assertEqual(opener.patch_calls, [])   # no claims, no mutations
        self.assertIsNone(sender.argv)             # sender never invoked
        self.assertNotIn(SECRET_SENTINEL, buf.getvalue())


class ConfigTests(unittest.TestCase):
    def _write_config(self, tmpdir, mode=0o600, **overrides):
        cfg = {
            "project_ref": consume.EXPECTED_PROJECT_REF,
            "activated_at": "2026-07-01T00:00:00+09:00",
            "sender_script": str(consume.EXPECTED_SENDER),
            "keychain_bin": consume.EXPECTED_KEYCHAIN_BIN,
            "batch_size": 5,
        }
        cfg.update(overrides)
        path = Path(tmpdir) / "outbox-consumer.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_valid_config_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp)
            config = consume.load_config(path)
            self.assertEqual(config.project_ref, consume.EXPECTED_PROJECT_REF)
            self.assertEqual(config.batch_size, 5)
            self.assertEqual(str(config.sender_script), str(consume.EXPECTED_SENDER))

    def test_world_readable_config_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp, mode=0o644)
            with self.assertRaises(consume.ConsumerError) as ctx:
                consume.load_config(path)
            self.assertEqual(str(ctx.exception), "config_permissions_too_open")

    def test_wrong_project_ref_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp, project_ref="someotherproject")
            with self.assertRaises(consume.ConsumerError):
                consume.load_config(path)

    def test_sender_not_allowlisted_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp, sender_script="/usr/bin/python3")
            with self.assertRaises(consume.ConsumerError) as ctx:
                consume.load_config(path)
            self.assertEqual(str(ctx.exception), "config_sender_not_allowlisted")

    def test_batch_size_out_of_range_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp, batch_size=99)
            with self.assertRaises(consume.ConsumerError):
                consume.load_config(path)


if __name__ == "__main__":
    unittest.main()
