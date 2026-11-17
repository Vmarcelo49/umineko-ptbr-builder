"""Structured extractors for Florescer and the Catbox ``script.rb``.

Everything written here is generated data under ``work/``; the upstream
repositories are only ever read.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .common import (
    decode_utf8,
    file_sha256,
    normalize_match,
    normalize_source_line,
    onscripter_clean,
    read_bytes,
    scan_messages,
    sha256,
    tag_skeleton,
    voice_ids,
)


def _jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            count += 1
    return count


def extract_catbox(catbox_root: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(catbox_root)
    path = root / "script.rb"
    data = read_bytes(path)
    messages = scan_messages(data, "catbox")
    rows: list[dict[str, Any]] = []
    for msg in messages:
        text = decode_utf8(msg.text)
        rows.append(
            {
                "target_id": f"msg_{msg.ordinal:05d}",
                "ordinal": msg.ordinal,
                "ushort": msg.ushort,
                "byte_a": msg.byte_a.decode("ascii", "replace"),
                "byte_b": msg.byte_b.decode("ascii", "replace"),
                "line": msg.line,
                "start": msg.start,
                "end": msg.end,
                "wrapped": msg.wrapped,
                "statement_sha256": sha256(data[msg.start : msg.end]),
                "raw_literal_sha256": sha256(msg.raw_literal),
                "text_sha256": sha256(msg.text),
                "english_text": text,
                "voice_ids": voice_ids(text),
                "tag_skeleton": tag_skeleton(text),
                "behavioral_skeleton": tag_skeleton(text, behavioral_only=True),
            }
        )
    stats = {
        "file": str(path),
        "sha256": file_sha256(path),
        "bytes": len(data),
        "nul_bytes": data.count(b"\x00"),
        "newlines": data.count(b"\n"),
        "message_count": len(rows),
        "wrapped_count": sum(1 for r in rows if r["wrapped"]),
        "unique_voice_ids": len({v for r in rows for v in r["voice_ids"]}),
        "voice_tag_count": sum(len(r["voice_ids"]) for r in rows),
    }
    return rows, stats


def _orig_message_index(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, list[int]], dict[str, list[int]], dict[str, list[int]]]:
    """Build exact, tag-stripped and per-segment indexes over Japanese messages."""
    exact: dict[str, list[int]] = defaultdict(list)
    stripped: dict[str, list[int]] = defaultdict(list)
    segments: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        text = row["japanese_text"]
        exact[normalize_match(text, strip_tags=False)].append(row["ordinal"])
        stripped[normalize_match(text)].append(row["ordinal"])
        for part in re.split(r"@[kr]", text):
            key = normalize_match(part)
            if key and row["ordinal"] not in segments[key]:
                segments[key].append(row["ordinal"])
    return exact, stripped, segments


def _resolve(source_text: str, exact: dict, stripped: dict, segments: dict) -> list[int]:
    key = normalize_match(source_text, strip_tags=False)
    if key in exact:
        return exact[key]
    key = normalize_source_line(source_text)
    if key in stripped:
        return stripped[key]
    return segments.get(key, [])


def extract_florescer(florescer_root: str | Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    root = Path(florescer_root)
    out: dict[str, list[dict[str, Any]]] = {}

    # ---- original Japanese script.rb (all 0x86 messages) ----
    script_rb = root / "SCRIPT/originais/script.rb"
    rb_data = read_bytes(script_rb)
    rb_rows: list[dict[str, Any]] = []
    for msg in scan_messages(rb_data, "florescer-original"):
        text = decode_utf8(msg.text)
        rb_rows.append(
            {
                "ordinal": msg.ordinal,
                "ushort": msg.ushort,
                "line": msg.line,
                "start": msg.start,
                "end": msg.end,
                "wrapped": msg.wrapped,
                "text_sha256": sha256(msg.text),
                "japanese_text": text,
                "voice_ids": voice_ids(text),
                "behavioral_skeleton": tag_skeleton(text, behavioral_only=True),
            }
        )
    out["florescer_original_messages"] = rb_rows

    # ---- original line-based script.txt (all episodes + extras) ----
    script_txt = root / "SCRIPT/originais/script.txt"
    txt_data = read_bytes(script_txt)
    txt_lines = txt_data.decode("utf-8").split("\n")
    exact_idx, stripped_idx, segment_idx = _orig_message_index(rb_rows)
    txt_rows: list[dict[str, Any]] = []
    section = ""
    for lineno, line in enumerate(txt_lines, 1):
        if line.startswith("==="):
            continue
        stripped_line = line.strip()
        if stripped_line and "===" not in stripped_line and normalized_is_header(stripped_line):
            section = stripped_line
            continue
        if not stripped_line:
            continue
        candidates = _resolve(stripped_line, exact_idx, stripped_idx, segment_idx)
        txt_rows.append(
            {
                "source_file": "SCRIPT/originais/script.txt",
                "line": lineno,
                "section": section,
                "sha256": sha256(line.encode("utf-8")),
                "japanese_text": stripped_line,
                "candidate_ordinals": candidates[:8],
                "candidate_count": len(candidates),
                "match_status": (
                    "unique" if len(candidates) == 1
                    else "ambiguous" if len(candidates) > 1
                    else "unmatched"
                ),
            }
        )
    out["florescer_original_lines"] = txt_rows

    # ---- per-scenario Japanese/Portuguese line-aligned pairs ----
    pair_rows: list[dict[str, Any]] = []
    for jp_path in sorted((root / "SCRIPT/jp").glob("*.txt")):
        pt_path = root / "SCRIPT/pt" / jp_path.name.replace(".txt", "_pt.txt")
        if not pt_path.exists():
            continue
        jp_lines = jp_path.read_bytes().decode("utf-8").split("\n")
        pt_lines = pt_path.read_bytes().decode("utf-8").split("\n")
        if len(jp_lines) != len(pt_lines):
            continue
        scenario = jp_path.stem
        for i, (jline, pline) in enumerate(zip(jp_lines, pt_lines), 1):
            jp_visible = onscripter_clean(jline)
            pt_visible = onscripter_clean(pline)
            candidates = _resolve(jline, exact_idx, stripped_idx, segment_idx)
            pair_rows.append(
                {
                    "source_file_jp": f"SCRIPT/jp/{jp_path.name}",
                    "source_file_pt": f"SCRIPT/pt/{pt_path.name}",
                    "scenario": scenario,
                    "line": i,
                    "source_hash": sha256((jline + "\n" + pline).encode("utf-8")),
                    "japanese_raw": jline,
                    "japanese_text": jp_visible,
                    "portuguese_raw": pline,
                    "portuguese_text": pt_visible,
                    "candidate_ordinals": candidates[:8],
                    "candidate_count": len(candidates),
                    "match_status": (
                        "unique" if len(candidates) == 1
                        else "ambiguous" if len(candidates) > 1
                        else "unmatched"
                    ),
                }
            )
    out["florescer_pt_jp_pairs"] = pair_rows

    # ---- SCRIPT/0.txt: EP4 scenario with ;langjp + langjp^ pairs ----
    zero_path = root / "SCRIPT/0.txt"
    zero_lines = zero_path.read_bytes().decode("utf-8").splitlines()
    zero_rows: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for lineno, line in enumerate(zero_lines, 1):
        if line.startswith(";langjp"):
            pending = {"line": lineno, "japanese_text": onscripter_clean(line[len(";langjp") :])}
        elif line.startswith("langjp^") and pending:
            pt_text = onscripter_clean(line[len("langjp^") :])
            candidates = _resolve(pending["japanese_text"], exact_idx, stripped_idx, segment_idx)
            zero_rows.append(
                {
                    "source_file": "SCRIPT/0.txt",
                    "line_jp": pending["line"],
                    "line_pt": lineno,
                    "source_hash": sha256(line.encode("utf-8")),
                    "japanese_text": pending["japanese_text"],
                    "portuguese_text": pt_text,
                    "candidate_ordinals": candidates[:8],
                    "candidate_count": len(candidates),
                    "match_status": (
                        "unique" if len(candidates) == 1
                        else "ambiguous" if len(candidates) > 1
                        else "unmatched"
                    ),
                }
            )
            pending = None
    out["florescer_ep4_pairs"] = zero_rows

    stats = {
        "script_rb": {
            "file": str(script_rb), "sha256": file_sha256(script_rb),
            "bytes": len(rb_data), "nul_bytes": rb_data.count(b"\x00"),
            "message_count": len(rb_rows),
        },
        "script_txt": {
            "file": str(script_txt), "sha256": file_sha256(script_txt),
            "bytes": len(txt_data), "line_count": len(txt_lines),
            "dialogue_lines": len(txt_rows),
            "unique_matches": sum(1 for r in txt_rows if r["match_status"] == "unique"),
            "ambiguous_matches": sum(1 for r in txt_rows if r["match_status"] == "ambiguous"),
            "unmatched": sum(1 for r in txt_rows if r["match_status"] == "unmatched"),
        },
        "pt_jp_pairs": {
            "count": len(pair_rows),
            "scenarios": sorted({r["scenario"] for r in pair_rows}),
            "unique_matches": sum(1 for r in pair_rows if r["match_status"] == "unique"),
            "ambiguous_matches": sum(1 for r in pair_rows if r["match_status"] == "ambiguous"),
            "unmatched": sum(1 for r in pair_rows if r["match_status"] == "unmatched"),
        },
        "ep4_pairs": {
            "count": len(zero_rows),
            "unique_matches": sum(1 for r in zero_rows if r["match_status"] == "unique"),
            "ambiguous_matches": sum(1 for r in zero_rows if r["match_status"] == "ambiguous"),
            "unmatched": sum(1 for r in zero_rows if r["match_status"] == "unmatched"),
        },
    }
    return out, stats


def normalized_is_header(text: str) -> bool:
    """Section headers in script.txt are short English/Japanese titles."""
    return (
        len(text) < 80
        and "==" not in text
        and (
            text.startswith("Episode")
            or text.startswith("Story")
            or text.startswith("Opening")
            or text.startswith("オープニング")
            or text in {"うみねこのなく頃に翼", "うみねこのなく頃に羽", "うみねこのなく頃に咲"}
        )
    )


def write_extractions(
    catbox_rows: list[dict[str, Any]],
    catbox_stats: dict[str, Any],
    florescer: dict[str, list[dict[str, Any]]],
    florescer_stats: dict[str, Any],
    out_dir: str | Path,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts = {
        "catbox_messages": _jsonl(out / "catbox_messages.jsonl", catbox_rows),
    }
    for key, rows in florescer.items():
        counts[key] = _jsonl(out / f"{key}.jsonl", rows)
    summary = {
        "catbox": catbox_stats,
        "florescer": florescer_stats,
        "counts": counts,
    }
    (out / "extraction_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary
