# task_id: t_082b0f7a
# 구현자: 덱[제품개발자·아스테리아자동화]
# 검증자: 레벨스 플러스[CEO·기업경영]
"""Fail closed unless a built Asteria public tree contains no measurement surface."""
from __future__ import annotations

import argparse
from pathlib import Path

TEXT_SUFFIXES = {".html", ".js", ".css", ".xml", ".txt"}
FORBIDDEN = {
    "measurement_script_refs": "/measurement.js",
    "ga_loader_refs": "googletagmanager.com/gtag/js",
    "ga_measurement_id_refs": "G-0M4KV62PVM",
    "measurement_api_write_refs": "/api/v1/events/view",
    "consent_ui_markers": "data-asteria-consent",
    "counter_display_markers": "data-asteria-view-count",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_root", type=Path)
    args = parser.parse_args()
    root = args.build_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise SystemExit(f"FAIL: build root is not a directory: {root}")

    text_files = sorted(
        path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
    )
    counts = {name: 0 for name in FORBIDDEN}
    for path in text_files:
        text = path.read_text(encoding="utf-8")
        for name, needle in FORBIDDEN.items():
            counts[name] += text.count(needle)

    measurement_assets = [path for path in root.rglob("measurement.js") if path.is_file()]
    home = (root / "index.html").read_text(encoding="utf-8")
    reservation = root / "veronica" / "index.html"
    application_controls = home.count('id="applyAction"') + home.count('id="feeApplyAction"')

    failures = []
    if measurement_assets:
        failures.append(f"measurement_assets={len(measurement_assets)}")
    failures.extend(f"{name}={count}" for name, count in counts.items() if count)
    if application_controls < 2:
        failures.append(f"application_controls={application_controls}")
    if not reservation.is_file():
        failures.append("reservation_page=missing")

    print(f"build_root={root}")
    print(f"text_files_scanned={len(text_files)}")
    print(f"measurement_assets={len(measurement_assets)}")
    for name, count in counts.items():
        print(f"{name}={count}")
    print(f"application_controls={application_controls}")
    print(f"reservation_page={'present' if reservation.is_file() else 'missing'}")
    if failures:
        raise SystemExit("FAIL: " + ", ".join(failures))
    print("PASS: measurement is globally OFF and application/reservation surfaces remain present")


if __name__ == "__main__":
    main()
