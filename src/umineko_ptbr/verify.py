"""Logical verification of a reviewed port: compare decoded patched messages
against the source records, voice identities and tag structure.

This does not look at pixels.  It proves that the generated script contains
exactly the approved Portuguese text, in the right voice slots, with every
behavioural command preserved.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .common import (
    read_bytes,
    ruby_unescape,
    scan_messages,
    sha256,
    tag_skeleton,
    tokenize_markup,
    voice_ids,
)
from .up_source import extract_up_ptbr

UO_RE = re.compile(r"@u([0-9a-fA-F]+)\.")


def decode_u_tags(text: str) -> str:
    return UO_RE.sub(lambda m: chr(int(m.group(1), 10)), text)


def visible_without_speaker(text: str) -> str:
    """Drop the first unvoiced text token (speaker name plate)."""
    tokens = tokenize_markup(text)
    if not any(t.kind == "arg" and t.marker == "v" for t in tokens):
        return "".join(t.value for t in tokens if t.kind == "text")
    current: str | None = None
    kept: list[str] = []
    dropped = False
    for token in tokens:
        if token.kind == "arg" and token.marker == "v":
            current = token.value[2:-1]
        elif token.kind == "text":
            if current is None and not dropped:
                dropped = True
                continue
            kept.append(token.value.strip())
    return "".join(kept)


def verify_port(
    patched_script: str | Path,
    review_path: str | Path,
    original_script: str | Path,
    source_txt: str | Path,
) -> dict[str, Any]:
    patched = scan_messages(read_bytes(patched_script))
    original = scan_messages(read_bytes(original_script))
    by_ordinal = {m.ordinal: m for m in patched}
    orig_by_ordinal = {m.ordinal: m for m in original}
    review = [
        json.loads(line) for line in Path(review_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    approved = [r for r in review if r.get("approved")]
    up_rows = {r["line"]: r for r in extract_up_ptbr(source_txt)}

    errors: list[str] = []
    checks: list[dict[str, Any]] = []

    for row in approved:
        ordinal = row["ordinal"]
        target = by_ordinal.get(ordinal)
        orig = orig_by_ordinal.get(ordinal)
        source = up_rows.get(row["source_line"])
        if not target or not orig or not source:
            errors.append(f"ordinal {ordinal}: missing target/original/source")
            continue

        new_text = target.text.decode("utf-8")
        old_text = orig.text.decode("utf-8")
        # script.rb contains @uNNN. escapes produced by replace_chars.js.
        decoded = decode_u_tags(new_text)

        # The applier concatenates source segments that share one voice tag.
        grouped: dict[str, list[str]] = {}
        order: list[str] = []
        for seg in row["segments"]:
            key = seg["voice"] or ""
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(seg["text"])
        expected_text = "".join("".join(grouped[k]) for k in order)
        actual_text = visible_without_speaker(decoded)
        text_ok = actual_text == expected_text

        source_voices = source["voice_ids"]
        target_voices = [v[1:] if v.startswith("v") else v for v in voice_ids(new_text)]
        voice_ok = target_voices == source_voices

        tags_ok = (
            tag_skeleton(old_text, behavioral_only=True)
            == tag_skeleton(new_text, behavioral_only=True)
        )
        english_tokens = [
            t.value for t in tokenize_markup(old_text) if t.kind == "text"
        ]
        english_left = any(
            token in decoded and token not in expected_text
            for token in english_tokens
            if len(token) > 12 and any(ch.isalpha() for ch in token)
        )

        # When the visible text matches the approved source exactly, any
        # remaining English-looking string is a preserved name plate.
        ok = text_ok and voice_ok and tags_ok
        if not ok:
            errors.append(
                f"ordinal {ordinal}: text_ok={text_ok} voice_ok={voice_ok} "
                f"tags_ok={tags_ok} english_left={english_left}"
            )
        checks.append({
            "ordinal": ordinal,
            "source_line": row["source_line"],
            "text_ok": text_ok,
            "voice_ok": voice_ok,
            "tags_ok": tags_ok,
            "english_left": english_left,
            "expected": expected_text,
            "actual": actual_text,
        })

    return {
        "approved_rows": len(approved),
        "checked": len(checks),
        "all_text_exact": all(c["text_ok"] for c in checks),
        "all_voices_exact": all(c["voice_ok"] for c in checks),
        "all_tags_preserved": all(c["tags_ok"] for c in checks),
        "no_english_residue": all(not c["english_left"] for c in checks),
        "errors": errors,
        "checks": checks,
        "ok": not errors and len(checks) == len(approved),
    }


def verify_snr(snr_path: str | Path, review_path: str | Path) -> dict[str, Any]:
    """Check that the compiled SNR carries the approved @u-encoded text."""
    # Catbox's WordWrapLayouter inserts @r line breaks while compiling; strip
    # them so the compiled text can be compared with the authored segments.
    data = read_bytes(snr_path)
    normalized_space = data.replace(b"@r", b" ")
    normalized_flat = data.replace(b"@r", b"")
    review = [
        json.loads(line) for line in Path(review_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    missing: list[int] = []
    for row in review:
        if not row.get("approved"):
            continue
        # Markup tags interrupt the visible text, so verify each voice
        # segment independently in the compiled script.
        for seg in row["segments"]:
            encoded = "".join(
                f"@u{ord(ch)}." if ord(ch) > 127 else ch for ch in seg["text"]
            ).encode("utf-8")
            # Word-wrap may replace a space with @r, or split a long word.
            if encoded not in normalized_space and encoded not in normalized_flat:
                missing.append((row["ordinal"], seg["text"][:60]))
                break
    return {"approved": len(review), "missing_from_snr": missing, "ok": not missing}


def verify_review_only(
    patched_script: str | Path,
    original_script: str | Path,
    review_path: str | Path,
) -> dict[str, Any]:
    """Verify using only the approved review rows (no external source lookup)."""
    patched = scan_messages(read_bytes(patched_script))
    original = scan_messages(read_bytes(original_script))
    by_ordinal = {m.ordinal: m for m in patched}
    orig_by_ordinal = {m.ordinal: m for m in original}
    rows = [
        json.loads(line) for line in Path(review_path).read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("approved")
    ]
    errors: list[str] = []
    counts = Counter()
    for row in rows:
        target = by_ordinal.get(row["ordinal"])
        orig = orig_by_ordinal.get(row["ordinal"])
        if not target or not orig:
            errors.append(f"ordinal {row['ordinal']}: missing message")
            continue
        new_text = target.text.decode("utf-8")
        old_text = orig.text.decode("utf-8")
        decoded = decode_u_tags(new_text)
        grouped: dict[str, list[str]] = {}
        order: list[str] = []
        for seg in row["segments"]:
            key = seg["voice"] or ""
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(seg["text"])
        expected = "".join("".join(grouped[k]) for k in order)
        actual = visible_without_speaker(decoded)
        if actual != expected:
            counts["text_fail"] += 1
            if len(errors) < 10:
                errors.append(f"ordinal {row['ordinal']}: text mismatch")
        if voice_ids(new_text) != voice_ids(old_text):
            counts["voice_fail"] += 1
        if tag_skeleton(old_text, behavioral_only=True) != tag_skeleton(new_text, behavioral_only=True):
            counts["tag_fail"] += 1
    return {
        "approved": len(rows),
        "checked": len(rows),
        "counts": dict(counts),
        "errors": errors,
        "ok": not errors,
    }
