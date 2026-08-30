#!/usr/bin/env python3
"""Strip JPEG APPn/COM metadata segments (EXIF, GPS, XMP) without re-encoding pixels.

Usage: python3 scripts/strip_jpeg_metadata.py images/foo.jpg [more.jpg ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

DROP_MARKERS = set(range(0xE0, 0xF0)) | {0xFE}  # APP0..APP15 + COM
KEEP_APP0_JFIF = True


def strip(data: bytes) -> bytes:
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            out.extend(data[i:])
            break
        marker = data[i + 1]
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out.extend(data[i:i + 2])
            i += 2
            continue
        if marker == 0xDA:  # start of scan: copy the rest verbatim
            out.extend(data[i:])
            break
        length = int.from_bytes(data[i + 2:i + 4], "big")
        segment = data[i:i + 2 + length]
        keep = marker not in DROP_MARKERS
        if not keep and KEEP_APP0_JFIF and marker == 0xE0 and segment[4:9] == b"JFIF\x00":
            keep = True
        if keep:
            out.extend(segment)
        i += 2 + length
    return bytes(out)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for arg in argv:
        path = Path(arg)
        original = path.read_bytes()
        cleaned = strip(original)
        path.write_bytes(cleaned)
        print(f"{path}: {len(original)} -> {len(cleaned)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
