#!/usr/bin/env python3
"""Stdlib unittest coverage for scripts/send_discord_booking_alert.py.

No network, no Keychain, no real Discord. urlopen and subprocess.run are
mocked; a fake urllib transport records every request and returns / raises
whatever the test configures.
"""

import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import send_discord_booking_alert as send  # noqa: E402

TOKEN_SENTINEL = "BOT_TOKEN_SENTINEL_DO_NOT_LOG"
MSG_ID = "999999999999999999"
IDEMPOTENCY_KEY = "opaque-key-should-never-be-logged"

PII_STRINGS = ["홍길동", "VER-260815", "팔미도", "아라마리나"]


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
    def __init__(self, obj):
        self._data = json.dumps(obj).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code):
    return HTTPError("https://discord.com", code, "err", {}, io.BytesIO(b"{}"))


class FakeTransport:
    """Records requests; a responder decides the reply per call.

    Default: POST echoes the sent content and stores it; GET returns the stored
    content — so the happy path verifies. Tests swap ``post`` / ``get`` to
    return a dict, or raise, to exercise the exit-code branches."""

    def __init__(self):
        self.calls = []          # (method, url, body)
        self._stored = None      # content captured on POST
        self.post = None         # optional override: (method,url,body)->dict|raises
        self.get = None

    def __call__(self, req, timeout=None):
        method = req.get_method()
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.calls.append((method, req.full_url, body))
        if method == "POST":
            if self.post is not None:
                return FakeResp(self.post(method, req.full_url, body))
            self._stored = body.get("content")
            return FakeResp({
                "id": MSG_ID,
                "channel_id": send.EXPECTED_CHANNEL_ID,
                "content": self._stored,
            })
        if method == "GET":
            if self.get is not None:
                return FakeResp(self.get(method, req.full_url, body))
            return FakeResp({
                "id": MSG_ID,
                "channel_id": send.EXPECTED_CHANNEL_ID,
                "content": self._stored,
            })
        raise AssertionError(f"unexpected method {method}")

    @property
    def methods(self):
        return [c[0] for c in self.calls]

    def post_body(self):
        for method, _url, body in self.calls:
            if method == "POST":
                return body
        return None


def fake_keychain(returncode=0, token=TOKEN_SENTINEL):
    def run(argv, **kwargs):
        return types.SimpleNamespace(returncode=returncode, stdout=token, stderr="")
    return run


def write_config(tmpdir, mode=0o600, **overrides):
    # keychain_bin must be an executable file to pass the config check.
    kc = Path(tmpdir) / "hermes-keychain"
    kc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(kc, 0o700)
    cfg = {
        "guild_id": send.EXPECTED_GUILD_ID,
        "channel_id": send.EXPECTED_CHANNEL_ID,
        "keychain_bin": str(kc),
        "keychain_service": "asteria.discord.bot-token",
        "keychain_account": "veronica-booking",
    }
    cfg.update(overrides)
    path = Path(tmpdir) / "booking-alert.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    os.chmod(path, mode)
    return path


def run_main(config_path, transport, keychain=None, argv_overrides=None):
    keychain = keychain or fake_keychain()
    out, err = io.StringIO(), io.StringIO()
    argv = base_argv(config_path, **(argv_overrides or {}))
    with mock.patch.object(send, "urlopen", transport), \
            mock.patch.object(send.subprocess, "run", keychain), \
            redirect_stdout(out), redirect_stderr(err):
        rc = send.main(argv)
    return rc, transport, out.getvalue() + err.getvalue()


class NonceTests(unittest.TestCase):
    def test_nonce_is_deterministic_and_bounded(self):
        a = send.compute_nonce(IDEMPOTENCY_KEY)
        b = send.compute_nonce(IDEMPOTENCY_KEY)
        self.assertEqual(a, b)
        self.assertLessEqual(len(a), send.NONCE_LEN)

    def test_nonce_differs_by_key(self):
        self.assertNotEqual(send.compute_nonce("row-a"), send.compute_nonce("row-b"))

    def test_empty_key_rejected(self):
        with self.assertRaises(send.SafeBeforeSendError):
            send.compute_nonce("   ")


class RenderTests(unittest.TestCase):
    def test_render_only_contains_required_fields_no_network(self):
        # --render-only is a flag: no config, keychain, or network is touched.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = send.main(base_argv("/nonexistent") + ["--render-only"])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        for token in ("예약번호: VER-260815", "예약일: 2026-08-15",
                      "13:00~16:00 (KST)", "아라마리나 → 팔미도", "홍길동", "4명"):
            self.assertIn(token, text)


class HappyPathTests(unittest.TestCase):
    def test_post_body_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, FakeTransport())
        self.assertEqual(rc, 0)
        body = tr.post_body()
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertTrue(body["enforce_nonce"])
        self.assertEqual(body["nonce"], send.compute_nonce(IDEMPOTENCY_KEY))
        self.assertIn("예약번호: VER-260815", body["content"])

    def test_readback_get_after_post_then_exit0(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, FakeTransport())
        self.assertEqual(rc, 0)
        self.assertEqual(tr.methods, ["POST", "GET"])
        # GET is the created message id.
        self.assertIn(MSG_ID, tr.calls[1][1])

    def test_no_token_or_pii_in_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, FakeTransport())
        self.assertEqual(rc, 0)
        self.assertNotIn(TOKEN_SENTINEL, logs)
        for pii in PII_STRINGS:
            self.assertNotIn(pii, logs)


class ExitCodeTests(unittest.TestCase):
    def test_guild_mismatch_exit2_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp, guild_id="0000000000000000000")
            rc, tr, logs = run_main(cfg, FakeTransport())
        self.assertEqual(rc, 2)
        self.assertEqual(tr.calls, [])  # never hit the network

    def test_channel_mismatch_exit2_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp, channel_id="0000000000000000000")
            rc, tr, logs = run_main(cfg, FakeTransport())
        self.assertEqual(rc, 2)
        self.assertEqual(tr.calls, [])

    def test_keychain_failure_exit2_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, FakeTransport(),
                                    keychain=fake_keychain(returncode=1))
        self.assertEqual(rc, 2)
        self.assertEqual(tr.calls, [])
        self.assertNotIn(TOKEN_SENTINEL, logs)

    def test_invalid_party_size_exit2(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, FakeTransport(),
                                    argv_overrides={"--party-size": "99"})
        self.assertEqual(rc, 2)
        self.assertEqual(tr.calls, [])

    def test_post_403_is_safe_before_send_exit2(self):
        tr = FakeTransport()

        def post(method, url, body):
            raise http_error(403)
        tr.post = post
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, tr)
        self.assertEqual(rc, 2)
        self.assertEqual(tr.methods, ["POST"])  # no GET readback attempted

    def test_post_500_is_uncertain_exit3(self):
        tr = FakeTransport()

        def post(method, url, body):
            raise http_error(500)
        tr.post = post
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, tr)
        self.assertEqual(rc, 3)
        self.assertEqual(tr.methods, ["POST"])

    def test_post_transport_error_is_uncertain_exit3(self):
        tr = FakeTransport()

        def post(method, url, body):
            raise URLError("connection reset")
        tr.post = post
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, tr)
        self.assertEqual(rc, 3)

    def test_post_content_mismatch_exit3(self):
        tr = FakeTransport()

        def post(method, url, body):
            return {"id": MSG_ID, "channel_id": send.EXPECTED_CHANNEL_ID,
                    "content": "tampered"}
        tr.post = post
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, tr)
        self.assertEqual(rc, 3)
        self.assertEqual(tr.methods, ["POST"])  # never proceeds to readback

    def test_post_wrong_channel_exit3(self):
        tr = FakeTransport()

        def post(method, url, body):
            return {"id": MSG_ID, "channel_id": "0000000000000000000",
                    "content": body["content"]}
        tr.post = post
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, tr)
        self.assertEqual(rc, 3)

    def test_readback_failure_exit3(self):
        tr = FakeTransport()

        def get(method, url, body):
            raise http_error(404)
        tr.get = get
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, tr)
        self.assertEqual(rc, 3)
        self.assertEqual(tr.methods, ["POST", "GET"])

    def test_readback_content_mismatch_exit3(self):
        tr = FakeTransport()

        def get(method, url, body):
            return {"id": MSG_ID, "channel_id": send.EXPECTED_CHANNEL_ID,
                    "content": "changed after send"}
        tr.get = get
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp)
            rc, tr, logs = run_main(cfg, tr)
        self.assertEqual(rc, 3)


class ConfigTests(unittest.TestCase):
    def test_world_readable_config_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp, mode=0o644)
            with self.assertRaises(send.SafeBeforeSendError) as ctx:
                send.load_config(cfg)
            self.assertEqual(str(ctx.exception), "config_permissions_too_open")

    def test_missing_keychain_account_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp, keychain_account="")
            with self.assertRaises(send.SafeBeforeSendError):
                send.load_config(cfg)


if __name__ == "__main__":
    unittest.main()
