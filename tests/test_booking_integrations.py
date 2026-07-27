#!/usr/bin/env python3
"""Stdlib unittest coverage for scripts/send_booking_integrations.py.

No child process ever actually runs: subprocess.run is mocked to record each
leg's argv/kwargs and return a configured exit code. The wrapper's ordering,
short-circuit, exit mapping, and output redaction are asserted here."""

import io
import subprocess
import sys
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import send_booking_integrations as wrap  # noqa: E402

IDEMPOTENCY_KEY = "opaque-key-should-never-be-logged"
PII_STRINGS = ["홍길동", "VER-260815", "팔미도", "아라마리나"]


def base_argv(**overrides):
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
    }
    args.update(overrides)
    argv = []
    for key, value in args.items():
        argv.extend([key, value])
    return argv


class FakeRunner:
    """Records each subprocess.run call and returns a configured exit code.

    ``rc_by_leg`` maps 'calendar'/'discord' -> return code. ``raise_for`` maps a
    leg -> an exception to raise instead of returning. The child's PII-laden
    stdout/stderr are simulated so redaction can be asserted."""

    def __init__(self, rc_by_leg, raise_for=None):
        self.rc_by_leg = rc_by_leg
        self.raise_for = raise_for or {}
        self.calls = []  # list of (leg, argv, kwargs)

    @staticmethod
    def _leg(argv):
        script = argv[1]
        if script == str(wrap.EXPECTED_CALENDAR_SENDER):
            return "calendar"
        if script == str(wrap.EXPECTED_DISCORD_SENDER):
            return "discord"
        raise AssertionError(f"unexpected sender {script}")

    def __call__(self, argv, **kwargs):
        leg = self._leg(argv)
        self.calls.append((leg, argv, kwargs))
        if leg in self.raise_for:
            raise self.raise_for[leg]
        # The child prints PII; the wrapper must capture and never echo it.
        return types.SimpleNamespace(
            returncode=self.rc_by_leg[leg],
            stdout=b"booker=\xed\x99\x8d\xea\xb8\xb8 route=leaked",
            stderr=b"VER-260815 secret",
        )

    @property
    def legs(self):
        return [leg for leg, _argv, _kw in self.calls]


def run_wrapper(rc_by_leg, raise_for=None, argv_overrides=None):
    runner = FakeRunner(rc_by_leg, raise_for)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(wrap.subprocess, "run", runner), \
            redirect_stdout(out), redirect_stderr(err):
        rc = wrap.main(base_argv(**(argv_overrides or {})))
    return rc, runner, out.getvalue() + err.getvalue()


class OrderingTests(unittest.TestCase):
    def test_calendar_runs_before_discord(self):
        rc, runner, logs = run_wrapper({"calendar": 0, "discord": 0})
        self.assertEqual(rc, 0)
        self.assertEqual(runner.legs, ["calendar", "discord"])

    def test_calendar_failure_short_circuits_discord(self):
        rc, runner, logs = run_wrapper({"calendar": 2, "discord": 0})
        self.assertEqual(rc, 2)
        self.assertEqual(runner.legs, ["calendar"])  # Discord never attempted

    def test_calendar_uncertain_short_circuits_discord(self):
        rc, runner, logs = run_wrapper({"calendar": 3, "discord": 0})
        self.assertEqual(rc, 3)
        self.assertEqual(runner.legs, ["calendar"])


class ExitMappingTests(unittest.TestCase):
    def test_both_ok_is_zero(self):
        rc, runner, logs = run_wrapper({"calendar": 0, "discord": 0})
        self.assertEqual(rc, 0)

    def test_discord_retryable_is_two(self):
        rc, runner, logs = run_wrapper({"calendar": 0, "discord": 2})
        self.assertEqual(rc, 2)
        self.assertEqual(runner.legs, ["calendar", "discord"])

    def test_discord_uncertain_is_three(self):
        rc, runner, logs = run_wrapper({"calendar": 0, "discord": 3})
        self.assertEqual(rc, 3)

    def test_unexpected_child_code_is_uncertain(self):
        rc, runner, logs = run_wrapper({"calendar": 7, "discord": 0})
        self.assertEqual(rc, 3)
        self.assertEqual(runner.legs, ["calendar"])

    def test_child_start_failure_is_retryable(self):
        rc, runner, logs = run_wrapper(
            {"calendar": 0, "discord": 0},
            raise_for={"calendar": OSError("no such file")},
        )
        self.assertEqual(rc, 2)
        self.assertEqual(runner.legs, ["calendar"])  # discord not attempted

    def test_child_timeout_is_uncertain(self):
        rc, runner, logs = run_wrapper(
            {"calendar": 0, "discord": 0},
            raise_for={"calendar": subprocess.TimeoutExpired("cmd", 100)},
        )
        self.assertEqual(rc, 3)
        self.assertEqual(runner.legs, ["calendar"])


class ArgvForwardingTests(unittest.TestCase):
    def test_common_args_and_idempotency_reach_both_legs(self):
        rc, runner, logs = run_wrapper({"calendar": 0, "discord": 0})
        self.assertEqual(rc, 0)
        for leg, argv, _kw in runner.calls:
            self.assertEqual(argv[0], sys.executable)
            self.assertIn("--idempotency-key", argv)
            self.assertEqual(argv[argv.index("--idempotency-key") + 1], IDEMPOTENCY_KEY)
            self.assertEqual(argv[argv.index("--event") + 1], "created")
            self.assertEqual(argv[argv.index("--route") + 1], "아라마리나 → 팔미도")
            self.assertIn("--config", argv)

    def test_each_leg_targets_its_pinned_sender_and_config(self):
        rc, runner, logs = run_wrapper({"calendar": 0, "discord": 0})
        by_leg = {leg: argv for leg, argv, _kw in runner.calls}
        self.assertEqual(by_leg["calendar"][1], str(wrap.EXPECTED_CALENDAR_SENDER))
        self.assertEqual(by_leg["discord"][1], str(wrap.EXPECTED_DISCORD_SENDER))
        cal_cfg = by_leg["calendar"][by_leg["calendar"].index("--config") + 1]
        dis_cfg = by_leg["discord"][by_leg["discord"].index("--config") + 1]
        self.assertEqual(cal_cfg, str(wrap.DEFAULT_CALENDAR_CONFIG))
        self.assertEqual(dis_cfg, str(wrap.DEFAULT_DISCORD_CONFIG))


class RedactionTests(unittest.TestCase):
    def test_children_are_captured_not_inherited(self):
        rc, runner, logs = run_wrapper({"calendar": 0, "discord": 0})
        for _leg, _argv, kwargs in runner.calls:
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.PIPE)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_wrapper_output_has_no_pii(self):
        # Even though every child emits PII on stdout/stderr, the wrapper only
        # ever prints its own stable status codes.
        rc, runner, logs = run_wrapper({"calendar": 0, "discord": 3})
        for pii in PII_STRINGS + [IDEMPOTENCY_KEY]:
            self.assertNotIn(pii, logs)
        self.assertIn("result=", logs)


if __name__ == "__main__":
    unittest.main()
