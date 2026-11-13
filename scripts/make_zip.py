#!/usr/bin/env python3
"""Builds dist/hon_hub.zip: unzip it inside the Home Assistant /config folder to install by hand."""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "custom_components" / "hon_hub"
OUT = ROOT / "dist" / "hon_hub.zip"

OUT.parent.mkdir(exist_ok=True)
files = sorted(p for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for p in files:
        info = zipfile.ZipInfo(str(p.relative_to(ROOT)), date_time=(2026, 1, 1, 0, 0, 0))   # same bytes at every build
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, p.read_bytes())
version = json.loads((SRC / "manifest.json").read_text())["version"]
print(f"{OUT} - {len(files)} files, version {version}, {OUT.stat().st_size // 1024} kB")
