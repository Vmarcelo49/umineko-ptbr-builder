"""Alignment from Florescer pt-BR lines to Catbox 0x86 messages.

The bridge is the Japanese original: both the Catbox ``script.rb`` and
Florescer's ``SCRIPT/originais/script.rb`` contain the same 73,007 0x86
instructions in the same order with the same ``ushort`` sequence.  Florescer's
line-based Japanese files are matched to those original messages, so no
English/Portuguese fuzzy similarity is ever used as evidence.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .common import normalize_match, normalize_source_line, sha256, tag_skeleton, voice_ids
from .extract import write_extractions


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _segment_index(
    messages: list[dict[str, Any]],
) -> tuple[dict[str, list[tuple[int, int]]], dict[str, list[int]]]:
    """Index messages by full text and by text split at @k/@r boundaries."""
    full: dict[str, list[int]] = defaultdict(list)
    segments: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for msg in messages:
        text = msg["japanese_text"]
        full[normalize_match(text)].append(msg["ordinal"])
        parts = re.split(r"@[kr]", text)
        for index, part in enumerate(parts):
            key = normalize_match(part)
            if key:
                segments[key].append((msg["ordinal"], index))
    return segments, full


def _candidates(jp_text: str, segments: dict, full: dict) -> tuple[list[int], str]:
    keys = [normalize_match(jp_text), normalize_source_line(jp_text)]
    for key in keys:
        if not key:
            continue
        ordinals = full.get(key)
        if ordinals and len(ordinals) == 1:
            return ordinals, "full-message"
        seg = segments.get(key, [])
        if seg:
            uniq = sorted({ordinal for ordinal, _ in seg})
            if len(uniq) == 1:
                return uniq, "message-segment"
            return uniq, "ambiguous-segment"
        if ordinals:
            return ordinals, "ambiguous-full-message"
    return [], "unmatched"


def build_review_rows(
    pairs: list[dict[str, Any]],
    originals: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    source_kind: str,
) -> list[dict[str, Any]]:
    segments, full = _segment_index(originals)
    by_ordinal = {m["ordinal"]: m for m in originals}
    target_by_ordinal = {m["ordinal"]: m for m in targets}
    previous_by_pair: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        current_line = pair.get("line_pt") or pair.get("line", 0)
        previous_by_pair[(pair.get("scenario", "ep4"), current_line - 1)] = pair

    rows: list[dict[str, Any]] = []
    for pair in pairs:
        ordinals, evidence = _candidates(pair["japanese_text"], segments, full)
        if len(ordinals) == 1:
            ordinal = ordinals[0]
            orig = by_ordinal[ordinal]
            target = target_by_ordinal.get(ordinal)
            if target is None:
                status, reason = "unmatched", "no target at matching ordinal"
                row = _base_row(pair, source_kind, status, reason)
                rows.append(row)
                continue
            voice_orig = orig["voice_ids"]
            voice_target = target["voice_ids"]
            voice_ok = voice_orig == voice_target
            prev = previous_by_pair.get((pair.get("scenario", "ep4"), (pair.get("line_pt") or pair.get("line", 0)) - 1))
            next_pair = None  # filled below when the list is indexed
            unsupported = [
                tag for tag in target["tag_skeleton"]
                if tag.startswith("@|") or tag == "@y"
            ]
            if unsupported:
                status = "candidate"
                reason = f"external-code tags present: {unsupported}"
            elif not voice_ok:
                status = "candidate"
                reason = "voice sequence differs between original and Catbox target"
            elif evidence == "message-segment":
                status = "candidate"
                reason = "Japanese line matches one segment of a merged message; split/merge review needed"
            else:
                status = "verified"
                reason = f"unique Japanese original message via {evidence}; voice sequence identical"
            row = _base_row(pair, source_kind, status, reason)
            row.update(
                {
                    "target_id": target["target_id"],
                    "ordinal": ordinal,
                    "episode": pair.get("scenario", "EP4"),
                    "scene": orig.get("section", ""),
                    "target_line": target["line"],
                    "target_hash": target["statement_sha256"],
                    "english_reference": target["english_text"],
                    "japanese_original": orig["japanese_text"],
                    "voice_ids": voice_target,
                    "target_behavioral_skeleton": target["behavioral_skeleton"],
                    "previous_context": (prev or {}).get("portuguese_text", ""),
                }
            )
            rows.append(row)
        else:
            status = "ambiguous" if len(ordinals) > 1 else "unmatched"
            reason = f"Japanese anchor resolution: {evidence} ({len(ordinals)} candidates)"
            rows.append(_base_row(pair, source_kind, status, reason, ordinals))
    rows = _resolve_by_order(rows, target_by_ordinal, by_ordinal)
    for i, row in enumerate(rows[:-1]):
        row["next_context"] = rows[i + 1].get("portuguese_text", "")
    if rows:
        rows[-1]["next_context"] = ""
    return rows


def _resolve_by_order(
    rows: list[dict[str, Any]],
    targets: dict[int, dict[str, Any]],
    originals: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Conservatively resolve ambiguous rows using monotonic source order.

    A candidate is promoted only when exactly one of its ordinals lies
    between the nearest resolved rows before and after it in the same
    scenario.  The result stays a *candidate* requiring human review.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row.get("episode", "")].append(row)
    for group in groups.values():
        group.sort(key=lambda r: r.get("source_line") or 0)
        resolved = [r["ordinal"] for r in group if r.get("ordinal") is not None]
        for i, row in enumerate(group):
            if row.get("status") != "ambiguous" or not row.get("candidate_ordinals"):
                continue
            before = [r["ordinal"] for r in group[:i] if r.get("ordinal") is not None]
            after = [r["ordinal"] for r in group[i + 1 :] if r.get("ordinal") is not None]
            lower = max(before) if before else None
            upper = min(after) if after else None
            valid = [
                o for o in row["candidate_ordinals"]
                if (lower is None or o >= lower) and (upper is None or o <= upper)
            ]
            if len(valid) != 1:
                continue
            ordinal = valid[0]
            target = targets.get(ordinal)
            if target is None:
                continue
            row["ordinal"] = ordinal
            row["target_id"] = target["target_id"]
            row["target_line"] = target["line"]
            row["target_hash"] = target["statement_sha256"]
            row["english_reference"] = target["english_text"]
            row["japanese_original"] = originals.get(ordinal, {}).get("japanese_text", "")
            row["voice_ids"] = target["voice_ids"]
            row["target_behavioral_skeleton"] = target["behavioral_skeleton"]
            row["status"] = "candidate"
            row["confidence_reason"] = (
                f"monotonic order between resolved neighbours ({lower}, {upper}) "
                f"selected ordinal {ordinal}; human review required"
            )
    return rows


def _base_row(
    pair: dict[str, Any],
    source_kind: str,
    status: str,
    reason: str,
    ordinals: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "source_kind": source_kind,
        "source_file": pair.get("source_file_pt") or pair.get("source_file", ""),
        "source_line": pair.get("line_pt") or pair.get("line", 0),
        "source_hash": pair["source_hash"],
        "portuguese_text": pair["portuguese_text"],
        "japanese_text": pair["japanese_text"],
        "candidate_ordinals": ordinals or pair.get("candidate_ordinals", []),
        "status": status,
        "confidence_reason": reason,
        "reviewer_notes": "",
        "previous_context": "",
        "next_context": "",
        "episode": pair.get("scenario", "EP4" if source_kind == "ep4" else ""),
    }


def select_pilot(
    rows: list[dict[str, Any]],
    min_lines: int = 10,
    max_lines: int = 30,
    start_ordinal: int | None = None,
) -> list[dict[str, Any]]:
    """Pick the longest contiguous run of verified, voice-anchored rows."""
    if start_ordinal is not None:
        run = [r for r in rows if r.get("ordinal") == start_ordinal]
    else:
        best: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        for row in rows:
            usable = (
                row.get("status") == "verified"
                and row.get("voice_ids")
                and row.get("ordinal") is not None
            )
            if usable and current and row["ordinal"] == current[-1]["ordinal"] + 1:
                current.append(row)
            elif usable:
                current = [row]
            else:
                current = []
            if len(current) > len(best):
                best = list(current)
        run = best
    if len(run) > max_lines:
        run = run[:max_lines]
    return run if len(run) >= min_lines else []


def select_manual_pilot(
    rows: list[dict[str, Any]], scenario: str, start_line: int, end_line: int
) -> list[dict[str, Any]]:
    """Select a contiguous source-line range for manual review."""
    return [
        row for row in rows
        if row.get("episode") == scenario
        and start_line <= (row.get("source_line") or 0) <= end_line
        and row.get("ordinal") is not None
    ]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(r["status"] for r in rows)
    per_episode: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        per_episode[row.get("episode", "")][row["status"]] += 1
    return {
        "total_rows": len(rows),
        "statuses": dict(statuses),
        "per_episode": {k: dict(v) for k, v in sorted(per_episode.items())},
        "verified_with_target_hash": sum(
            1 for r in rows if r["status"] == "verified" and r.get("target_hash")
        ),
    }


def write_review(path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def align_all(work_dir: str | Path, reports_dir: str | Path, pilot_start: int | None = None) -> dict[str, Any]:
    work = Path(work_dir)
    reports = Path(reports_dir)
    reports.mkdir(parents=True, exist_ok=True)
    originals = load_jsonl(work / "florescer_original_messages.jsonl")
    targets = load_jsonl(work / "catbox_messages.jsonl")
    pt_pairs = load_jsonl(work / "florescer_pt_jp_pairs.jsonl")
    ep4_pairs = load_jsonl(work / "florescer_ep4_pairs.jsonl")

    all_rows: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    for name, pairs in (("extras", pt_pairs), ("ep4", ep4_pairs)):
        rows = build_review_rows(pairs, originals, targets, name)
        write_review(work / f"{name}.review.jsonl", rows)
        all_rows.extend(rows)
        results[name] = summarize(rows)
    write_review(work / "all.review.jsonl", all_rows)

    pilot = select_pilot(
        [r for r in all_rows if r["source_kind"] == "extras"],
        start_ordinal=pilot_start,
    )
    write_review(work / "pilot.review.jsonl", pilot)
    results["pilot"] = {
        "rows": len(pilot),
        "ordinals": [r.get("ordinal") for r in pilot],
        "episode": pilot[0].get("episode") if pilot else None,
    }
    results["combined"] = summarize(all_rows)
    (reports / "alignment_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return results
