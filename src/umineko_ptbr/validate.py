"""Static validation for review manifests, patched scripts and localized.toml."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .align import load_jsonl
from .common import file_sha256, scan_messages, sha256

ALLOWED_STATUS = {"verified", "candidate", "ambiguous", "unmatched", "incompatible"}


def _placeholders(text: str) -> list[str]:
    return re.findall(r"\{([A-Za-z0-9_]*)", text)


def validate_toml(path: str | Path, reference: str | Path | None = None) -> dict[str, Any]:
    data = Path(path).read_bytes()
    data.decode("utf-8")  # strict UTF-8
    table = tomllib.loads(data.decode("utf-8"))
    errors: list[str] = []
    for key, value in table.items():
        if not isinstance(value, str):
            errors.append(f"{key!r}: value is not a string")
            continue
        if not key:
            errors.append("empty key")
        if any(ord(ch) > 0xFFFF for ch in value):
            errors.append(f"{key!r}: non-BMP character rejected by Shin's font range check")
    if reference is not None:
        ref = tomllib.loads(Path(reference).read_text(encoding="utf-8"))
        for key, value in table.items():
            if key in ref and isinstance(ref[key], str):
                if sorted(_placeholders(key)) != sorted(_placeholders(value)):
                    errors.append(f"{key!r}: placeholder variables changed")
        missing = sorted(set(ref) - set(table))
    else:
        missing = []
    return {
        "file": str(path),
        "sha256": sha256(data),
        "keys": len(table),
        "errors": errors,
        "reference_keys_not_translated": missing,
        "ok": not errors,
    }


def validate_review(path: str | Path) -> dict[str, Any]:
    rows = load_jsonl(path)
    errors: list[str] = []
    for i, row in enumerate(rows, 1):
        if row.get("status") not in ALLOWED_STATUS:
            errors.append(f"row {i}: invalid status {row.get('status')!r}")
        if row.get("approved") and row.get("status") != "verified":
            errors.append(f"row {i}: approved but status is {row.get('status')!r}")
        if row.get("status") == "verified" and not row.get("target_hash"):
            errors.append(f"row {i}: verified row lacks target_hash")
    return {
        "file": str(path),
        "rows": len(rows),
        "approved": sum(1 for r in rows if r.get("approved")),
        "statuses": {s: sum(1 for r in rows if r.get("status") == s) for s in ALLOWED_STATUS},
        "errors": errors,
        "ok": not errors,
    }


def validate_patch(original: str | Path, patched: str | Path, expected_ordinals: set[int]) -> dict[str, Any]:
    a = scan_messages(read_bytes_safe(original))
    b = scan_messages(read_bytes_safe(patched))
    errors: list[str] = []
    if len(a) != len(b):
        errors.append(f"message count changed: {len(a)} -> {len(b)}")
    changed = set()
    for x, y in zip(a, b):
        if x.text != y.text:
            changed.add(x.ordinal)
        if x.ordinal not in expected_ordinals and x.raw_literal != y.raw_literal:
            errors.append(f"unexpected byte change at ordinal {x.ordinal}")
    unexpected = changed - expected_ordinals
    if unexpected:
        errors.append(f"unexpected text changes: {sorted(unexpected)[:10]}")
    return {
        "original_sha256": file_sha256(original),
        "patched_sha256": file_sha256(patched),
        "messages": len(b),
        "changed_ordinals": sorted(changed),
        "errors": errors,
        "ok": not errors,
    }


def read_bytes_safe(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def validate_noop(original: str | Path, patched: str | Path) -> dict[str, Any]:
    same = Path(original).read_bytes() == Path(patched).read_bytes()
    return {"byte_identical": same, "ok": same}


def glyph_report(texts: list[str]) -> dict[str, Any]:
    chars = sorted({ch for text in texts for ch in text})
    non_bmp = [ch for ch in chars if ord(ch) > 0xFFFF]
    accented = [ch for ch in chars if ch in "áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ"]
    return {
        "distinct_chars": len(chars),
        "accented_chars": "".join(accented),
        "non_bmp_chars": non_bmp,
        "ok": not non_bmp,
    }
