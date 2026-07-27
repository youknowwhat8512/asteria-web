#!/usr/bin/env python3
"""Stdlib unittest coverage for scripts/send_google_calendar_booking.py.

No network, no real Google, no real OAuth, and never the real credentials file.
``urlopen`` is replaced by an in-memory fake Calendar that also answers the
OAuth token exchange; the hard-pinned credentials path is redirected to a temp
0600 file so tests never read the operator's real credentials.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import send_google_calendar_booking as send  # noqa: E402

TOKEN_URI = "https://oauth2.googleapis.test/token"
ACCESS_TOKEN = "ACCESS_TOKEN_SENTINEL_DO_NOT_LOG"
REFRESH_TOKEN = "REFRESH_TOKEN_SENTINEL_DO_NOT_LOG"
CLIENT_SECRET = "CLIENT_SECRET_SENTINEL_DO_NOT_LOG"
IDEMPOTENCY_KEY = "opaque-key-should-never-be-logged"

PII_STRINGS = ["홍길동", "VER-260815", "팔미도", "아라마리나"]
SECRET_STRINGS = [ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET, IDEMPOTENCY_KEY]


def base_argv(config_path, **overrides):
    args = {
        "--event": "created",
        "--booking-id": "VER-260815",
        "--date": "2026-08-15",
        "--start-time": "13:00",
        "--end-time": "16:00",
        "--route": "아라마리나 → 팔미도",
        "--name": "홍길동",
        "--party-size": "4",
        "--idempotency-key": IDEMPOTENCY_KEY,
        "--config": str(config_path),
    }
    args.update(overrides)
    argv = []
    for key, value in args.items():
        argv.extend([key, value])
    return argv


class FakeResp:
    def __init__(self, obj, status=200):
        self._data = b"" if obj is None else json.dumps(obj).encode("utf-8")
        self.status = status

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"{}"):
    return HTTPError("https://www.googleapis.com", code, "err", {}, io.BytesIO(body))


class FakeGoogle:
    """In-memory Calendar + OAuth token endpoint.

    Default behaviour mirrors Google: insert stores the event and echoes it back
    with offset-form dateTimes; GET returns it; PUT updates it; DELETE removes
    it; a missing id yields 404 and a duplicate insert yields 409. Tests set the
    ``on_*`` hooks to force a specific status, raise, or diverge the content."""

    def __init__(self):
        self.events = {}          # id -> raw sent body
        self.calls = []           # (method, tag)
        self.on_insert = None
        self.on_get = None
        self.on_update = None
        self.on_delete = None
        self.oauth_error = None   # HTTPError/URLError to raise on token exchange
        self.oauth_response = None

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _event_id(url):
        return url.split("/events/", 1)[1].split("?", 1)[0]

    @staticmethod
    def _normalize(body):
        """Return the event as Google would: dateTimes carry a +09:00 offset."""
        out = dict(body)
        for key in ("start", "end"):
            node = body[key]
            out[key] = {
                "dateTime": node["dateTime"] + "+09:00",
                "timeZone": node["timeZone"],
            }
        return out

    # --- dispatch --------------------------------------------------------
    def __call__(self, req, timeout=None):
        method = req.get_method()
        url = req.full_url
        if url.startswith(TOKEN_URI):
            self.calls.append((method, "OAUTH"))
            if self.oauth_error is not None:
                raise self.oauth_error
            if self.oauth_response is not None:
                return FakeResp(self.oauth_response)
            return FakeResp({"access_token": ACCESS_TOKEN, "expires_in": 3599})

        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.calls.append((method, url))
        if method == "POST":
            return (self.on_insert or self._insert)(url, body)
        if method == "GET":
            return (self.on_get or self._get)(url, body)
        if method == "PUT":
            return (self.on_update or self._update)(url, body)
        if method == "DELETE":
            return (self.on_delete or self._delete)(url, body)
        raise AssertionError(f"unexpected method {method}")

    def _insert(self, url, body):
        if body["id"] in self.events:
            raise http_error(409)
        self.events[body["id"]] = body
        return FakeResp(self._normalize(body), status=200)

    def _get(self, url, body):
        eid = self._event_id(url)
        if eid not in self.events:
            raise http_error(404)
        return FakeResp(self._normalize(self.events[eid]), status=200)

    def _update(self, url, body):
        eid = self._event_id(url)
        if eid not in self.events:
            raise http_error(404)
        self.events[eid] = body
        return FakeResp(self._normalize(body), status=200)

    def _delete(self, url, body):
        eid = self._event_id(url)
        if eid not in self.events:
            raise http_error(404)
        del self.events[eid]
        return FakeResp(None, status=204)

    # --- introspection ---------------------------------------------------
    @property
    def methods(self):
        return [m for m, _ in self.calls]

    @property
    def calendar_calls(self):
        return [c for c in self.calls if c[1] != "OAUTH"]


def write_config(tmpdir, *, cfg_mode=0o600, cred_mode=0o600, cfg_overrides=None):
    """Write a temp credentials file and config file, and return the paths.

    The credentials path is what EXPECTED_CREDENTIALS_FILE must be patched to."""
    creds = {
        "client_id": "client-id-value",
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "token_uri": TOKEN_URI,
        "token": "unused",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    }
    cred_path = Path(tmpdir) / "creds.json"
    cred_path.write_text(json.dumps(creds), encoding="utf-8")
    os.chmod(cred_path, cred_mode)

    cfg = {
        "user_email": send.EXPECTED_USER_EMAIL,
        "calendar_id": send.EXPECTED_CALENDAR_ID,
        "credentials_file": str(cred_path),
    }
    cfg.update(cfg_overrides or {})
    cfg_path = Path(tmpdir) / "booking-alert.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    os.chmod(cfg_path, cfg_mode)
    return cfg_path, cred_path


def run_main(cfg_path, cred_path, fake, argv_overrides=None):
    out, err = io.StringIO(), io.StringIO()
    argv = base_argv(cfg_path, **(argv_overrides or {}))
    with mock.patch.object(send, "urlopen", fake), \
            mock.patch.object(send, "EXPECTED_CREDENTIALS_FILE", str(cred_path)), \
            redirect_stdout(out), redirect_stderr(err):
        rc = send.main(argv)
    return rc, fake, out.getvalue() + err.getvalue()


def parse_args(**overrides):
    ns = mock.Mock()
    # Build a real Namespace via the parser for fidelity.
    import argparse
    p = argparse.ArgumentParser()
    for flag in ("--event", "--booking-id", "--date", "--start-time",
                 "--end-time", "--route", "--name", "--idempotency-key"):
        p.add_argument(flag)
    p.add_argument("--party-size", type=int)
    argv = base_argv("/unused")
    ns = p.parse_args([a for a in argv if a != "--config" and a != "/unused"])
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


# --- Determinism / event id ------------------------------------------------


class EventIdTests(unittest.TestCase):
    def test_event_id_is_deterministic_and_valid(self):
        a = send.compute_event_id("VER-260815")
        b = send.compute_event_id("VER-260815")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[a-v0-9]{5,1024}$")
        self.assertEqual(len(a), 64)

    def test_event_id_differs_by_booking(self):
        self.assertNotEqual(
            send.compute_event_id("VER-1"), send.compute_event_id("VER-2")
        )

    def test_one_event_id_across_all_events(self):
        # created / updated / cancelled all address the same deterministic id.
        ids = set()
        for event in ("created", "updated", "cancelled"):
            plan = send.build_plan(parse_args(event=event))
            ids.add(plan["event_id"])
        self.assertEqual(len(ids), 1)


# --- 24:00 and time handling ----------------------------------------------


class TimeTests(unittest.TestCase):
    def test_midnight_end_maps_to_next_day(self):
        plan = send.build_plan(parse_args(start_time="21:00", end_time="24:00"))
        self.assertEqual(plan["body"]["start"]["dateTime"], "2026-08-15T21:00:00")
        self.assertEqual(plan["body"]["end"]["dateTime"], "2026-08-16T00:00:00")
        self.assertEqual(plan["body"]["start"]["timeZone"], "Asia/Seoul")
        self.assertEqual(plan["body"]["end"]["timeZone"], "Asia/Seoul")

    def test_normal_end_same_day(self):
        plan = send.build_plan(parse_args(start_time="13:00", end_time="16:00"))
        self.assertEqual(plan["body"]["end"]["dateTime"], "2026-08-15T16:00:00")

    def test_end_before_start_rejected(self):
        with self.assertRaises(send.SafeBeforeSendError):
            send.build_plan(parse_args(start_time="16:00", end_time="13:00"))


# --- Payload privacy -------------------------------------------------------


class PayloadPrivacyTests(unittest.TestCase):
    def test_body_shape_and_no_pii_channels(self):
        plan = send.build_plan(parse_args())
        body = plan["body"]
        self.assertEqual(body["summary"], "베로니카 차터 예약 · 홍길동")
        self.assertEqual(body["location"], "아라마리나")  # departure only
        self.assertEqual(body["visibility"], "private")
        self.assertEqual(body["status"], "confirmed")
        self.assertNotIn("attendees", body)
        # description carries only booking number, route, people, source marker.
        desc = body["description"]
        self.assertIn("예약번호: VER-260815", desc)
        self.assertIn("항로: 아라마리나 → 팔미도", desc)
        self.assertIn("예상 인원: 4명", desc)
        self.assertIn(send.SOURCE_MARKER, desc)
        self.assertNotIn("홍길동", desc)  # name never in description
        priv = body["extendedProperties"]["private"]
        self.assertEqual(priv["asteria_source"], send.SOURCE_MARKER)
        self.assertEqual(priv["asteria_booking_id"], "VER-260815")
        self.assertEqual(priv["asteria_idempotency_key"], IDEMPOTENCY_KEY)

    def test_forbidden_fields_never_present(self):
        blob = json.dumps(send.build_plan(parse_args())["body"], ensure_ascii=False)
        for forbidden in ("phone", "010-", "passenger", "cancel", "attendee"):
            self.assertNotIn(forbidden, blob.lower())


# --- Config & credentials --------------------------------------------------


class ConfigTests(unittest.TestCase):
    def _load(self, tmp, **kw):
        cfg_path, cred_path = write_config(tmp, **kw)
        with mock.patch.object(send, "EXPECTED_CREDENTIALS_FILE", str(cred_path)):
            return send.load_config(cfg_path), cfg_path, cred_path

    def test_valid_config_and_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, _cfg, cred_path = self._load(tmp)
            self.assertEqual(config["user_email"], send.EXPECTED_USER_EMAIL)
            self.assertEqual(config["calendar_id"], send.EXPECTED_CALENDAR_ID)
            creds = send.load_credentials(config)
            self.assertEqual(creds["refresh_token"], REFRESH_TOKEN)
            self.assertEqual(creds["token_uri"], TOKEN_URI)

    def test_bad_user_email_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(send.SafeBeforeSendError) as ctx:
                self._load(tmp, cfg_overrides={"user_email": "evil@example.com"})
            self.assertEqual(str(ctx.exception), "config_bad_user_email")

    def test_bad_calendar_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(send.SafeBeforeSendError) as ctx:
                self._load(tmp, cfg_overrides={"calendar_id": "other@group.calendar.google.com"})
            self.assertEqual(str(ctx.exception), "config_bad_calendar_id")

    def test_credentials_path_not_allowlisted_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path, cred_path = write_config(
                tmp, cfg_overrides={"credentials_file": "/tmp/elsewhere.json"})
            with mock.patch.object(send, "EXPECTED_CREDENTIALS_FILE", str(cred_path)):
                with self.assertRaises(send.SafeBeforeSendError) as ctx:
                    send.load_config(cfg_path)
            self.assertEqual(str(ctx.exception), "config_credentials_not_allowlisted")

    def test_world_readable_config_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(send.SafeBeforeSendError) as ctx:
                self._load(tmp, cfg_mode=0o644)
            self.assertEqual(str(ctx.exception), "config_permissions_too_open")

    def test_group_readable_credentials_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path, cred_path = write_config(tmp, cred_mode=0o640)
            with mock.patch.object(send, "EXPECTED_CREDENTIALS_FILE", str(cred_path)):
                config = send.load_config(cfg_path)
                with self.assertRaises(send.SafeBeforeSendError) as ctx:
                    send.load_credentials(config)
            self.assertEqual(str(ctx.exception), "credentials_permissions_too_open")

    def test_missing_credentials_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cred = {"client_id": "x", "client_secret": "y", "token_uri": TOKEN_URI}
            cred_path = Path(tmp) / "creds.json"
            cred_path.write_text(json.dumps(cred), encoding="utf-8")
            os.chmod(cred_path, 0o600)
            with self.assertRaises(send.SafeBeforeSendError) as ctx:
                send.load_credentials({"credentials_file": str(cred_path)})
            self.assertEqual(str(ctx.exception), "credentials_missing_field")

    def test_non_https_token_uri_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cred = {
                "client_id": "x", "client_secret": "y",
                "refresh_token": "z", "token_uri": "http://insecure/token",
            }
            cred_path = Path(tmp) / "creds.json"
            cred_path.write_text(json.dumps(cred), encoding="utf-8")
            os.chmod(cred_path, 0o600)
            with self.assertRaises(send.SafeBeforeSendError) as ctx:
                send.load_credentials({"credentials_file": str(cred_path)})
            self.assertEqual(str(ctx.exception), "credentials_token_uri_not_https")


# --- Render-only -----------------------------------------------------------


class RenderTests(unittest.TestCase):
    def test_render_only_no_network_no_config(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = send.main(base_argv("/nonexistent") + ["--render-only"])
        self.assertEqual(rc, 0)
        obj = json.loads(buf.getvalue())
        self.assertEqual(obj["summary"], "베로니카 차터 예약 · 홍길동")
        self.assertEqual(obj["visibility"], "private")


# --- OAuth -----------------------------------------------------------------


class OAuthTests(unittest.TestCase):
    def test_oauth_http_failure_is_exit2_no_calendar_call(self):
        fake = FakeGoogle()
        fake.oauth_error = http_error(400)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 2)
        self.assertEqual(fake.calendar_calls, [])   # never touched the calendar
        for s in SECRET_STRINGS:
            self.assertNotIn(s, logs)

    def test_oauth_transport_failure_is_exit2(self):
        fake = FakeGoogle()
        fake.oauth_error = URLError("connection reset")
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 2)
        self.assertEqual(fake.calendar_calls, [])

    def test_oauth_missing_access_token_is_exit2(self):
        fake = FakeGoogle()
        fake.oauth_response = {"expires_in": 10}
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 2)
        self.assertEqual(fake.calendar_calls, [])


# --- Create ----------------------------------------------------------------


class CreateTests(unittest.TestCase):
    def test_create_happy_exit0_post_then_get(self):
        fake = FakeGoogle()
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["POST", "GET"])
        for s in SECRET_STRINGS + PII_STRINGS:
            self.assertNotIn(s, logs)

    def test_create_conflict_get_verifies_exit0(self):
        fake = FakeGoogle()
        # Pre-seed the exact event so insert 409s, then GET verifies.
        plan = send.build_plan(parse_args())
        fake.events[plan["event_id"]] = plan["body"]
        fake.on_insert = lambda url, body: (_ for _ in ()).throw(http_error(409))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["POST", "GET"])

    def test_create_conflict_but_divergent_event_exit3(self):
        fake = FakeGoogle()
        plan = send.build_plan(parse_args())
        divergent = dict(plan["body"], summary="tampered summary")
        fake.events[plan["event_id"]] = divergent
        fake.on_insert = lambda url, body: (_ for _ in ()).throw(http_error(409))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 3)

    def test_create_conflict_then_vanished_exit2(self):
        fake = FakeGoogle()
        # 409 on insert, but the event is absent on the follow-up GET.
        fake.on_insert = lambda url, body: (_ for _ in ()).throw(http_error(409))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 2)

    def test_create_rejected_403_exit2(self):
        fake = FakeGoogle()
        fake.on_insert = lambda url, body: (_ for _ in ()).throw(http_error(403))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 2)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["POST"])  # no GET

    def test_create_server_error_exit3(self):
        fake = FakeGoogle()
        fake.on_insert = lambda url, body: (_ for _ in ()).throw(http_error(500))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 3)

    def test_create_transport_error_exit3(self):
        fake = FakeGoogle()
        fake.on_insert = lambda url, body: (_ for _ in ()).throw(URLError("reset"))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 3)

    def test_create_readback_mismatch_exit3(self):
        fake = FakeGoogle()

        def on_get(url, body):
            eid = FakeGoogle._event_id(url)
            ev = dict(fake.events[eid], description="changed after write")
            return FakeResp(FakeGoogle._normalize(ev), status=200)

        fake.on_get = on_get
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake)
        self.assertEqual(rc, 3)


# --- Update ----------------------------------------------------------------


class UpdateTests(unittest.TestCase):
    def test_update_existing_exit0_put_then_get(self):
        fake = FakeGoogle()
        plan = send.build_plan(parse_args(event="updated"))
        fake.events[plan["event_id"]] = dict(plan["body"], description="old")
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake, {"--event": "updated"})
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["PUT", "GET"])

    def test_update_missing_falls_back_to_insert_exit0(self):
        fake = FakeGoogle()  # no pre-seeded event -> PUT 404 -> insert
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake, {"--event": "updated"})
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["PUT", "POST", "GET"])

    def test_update_rejected_400_exit2(self):
        fake = FakeGoogle()
        fake.on_update = lambda url, body: (_ for _ in ()).throw(http_error(400))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake, {"--event": "updated"})
        self.assertEqual(rc, 2)


# --- Cancel ----------------------------------------------------------------


class CancelTests(unittest.TestCase):
    def test_cancel_deletes_then_confirms_absent_exit0(self):
        fake = FakeGoogle()
        plan = send.build_plan(parse_args(event="cancelled"))
        fake.events[plan["event_id"]] = plan["body"]
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake, {"--event": "cancelled"})
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["DELETE", "GET"])

    def test_cancel_already_gone_is_idempotent_exit0(self):
        fake = FakeGoogle()  # no event -> DELETE 404 -> GET 404 -> success
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake, {"--event": "cancelled"})
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["DELETE", "GET"])

    def test_cancel_tombstone_is_verified_exit0(self):
        fake = FakeGoogle()
        plan = send.build_plan(parse_args(event="cancelled"))
        tombstone = dict(plan["body"])
        tombstone["status"] = "cancelled"
        # Google may retain a deleted event as a cancelled tombstone.
        fake.events[plan["event_id"]] = tombstone
        fake.on_delete = lambda url, body: FakeResp(None, status=204)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake, {"--event": "cancelled"})
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["DELETE", "GET"])

    def test_cancel_still_present_after_delete_exit3(self):
        fake = FakeGoogle()
        plan = send.build_plan(parse_args(event="cancelled"))
        fake.events[plan["event_id"]] = plan["body"]
        # DELETE reports success but the verify GET still finds a confirmed event.
        fake.on_delete = lambda url, body: FakeResp(None, status=204)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake, {"--event": "cancelled"})
        self.assertEqual(rc, 3)

    def test_cancel_rejected_403_exit2(self):
        fake = FakeGoogle()
        fake.on_delete = lambda url, body: (_ for _ in ()).throw(http_error(403))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cred = write_config(tmp)
            rc, fake, logs = run_main(cfg, cred, fake, {"--event": "cancelled"})
        self.assertEqual(rc, 2)
        self.assertEqual([m for m, _ in fake.calendar_calls], ["DELETE"])  # no verify


if __name__ == "__main__":
    unittest.main()
