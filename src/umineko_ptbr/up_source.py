"""Extractor for the Umineko Project pt-BR script (``pt.txt`` from the
umineko-scripting z4067 release).

Dialogue is emitted as ``d``/``d2`` instructions.  Voice tags use
``[lv 0*"19"*"11900001"]``, which is the same full voice identity as the
Catbox/Switch ``@v19/11900001.`` tag — the strongest possible alignment anchor.
"""

from __future__ import annotations

import bisect
import json
import re
from pathlib import Path
from typing import Any

from .common import (
    atomic_write,
    brace_visible,
    normalize_match,
    read_bytes,
    ruby_escape,
    sha256,
    tag_skeleton,
    tokenize_markup,
    voice_ids,
)

VOICE_RE = re.compile(r'\[lv\s+0\*"(\d+)"\*"(\d+)"\]')
TEXT_RE = re.compile(r"`([^`]*)`")
EPISODE_RE = re.compile(r"^new_episode\s+(\d+)")
DIALOGUE_RE = re.compile(r"^(d\d*)\s")


def clean_segment(text: str) -> str:
    """Strip ONScripter inline commands from a backtick text segment."""
    s = text
    for _ in range(4):
        nxt = re.sub(r"\{([^{}]*)\}", lambda m: brace_visible(m.group(1)), s)
        if nxt == s:
            break
        s = nxt
    s = re.sub(r"\[[^\]]*\]", "", s)   # [gstg N], [@...] etc.
    return s.strip()


def parse_line(line: str) -> dict[str, Any] | None:
    m = DIALOGUE_RE.match(line)
    if not m:
        return None
    voices = [f"{g}/{vid}" for g, vid in VOICE_RE.findall(line)]
    segments: list[dict[str, Any]] = []
    current_voice: str | None = None
    for token in re.finditer(r'\[lv[^]]*\]|`[^`]*`', line):
        raw = token.group(0)
        vm = VOICE_RE.match(raw)
        if vm:
            current_voice = f"{vm.group(1)}/{vm.group(2)}"
        elif raw.startswith("`") and raw.endswith("`"):
            text = clean_segment(raw[1:-1])
            if text:
                segments.append({"voice": current_voice, "text": text})
    if not segments:
        return None
    return {
        "instruction": m.group(1),
        "voice_ids": voices,
        "segments": segments,
        "visible_text": "".join(s["text"] for s in segments),
        "raw": line,
    }


def extract_up_ptbr(path: str | Path) -> list[dict[str, Any]]:
    data = Path(path).read_bytes()
    text = data.decode("utf-8")
    rows: list[dict[str, Any]] = []
    episode = 0
    label = ""
    for lineno, line in enumerate(text.split("\n"), 1):
        em = EPISODE_RE.match(line)
        if em:
            episode = int(em.group(1))
            continue
        if line.startswith("*"):
            label = line[1:].strip()
            continue
        parsed = parse_line(line)
        if not parsed:
            continue
        rows.append({
            "source_file": "umineko-project/pt.txt",
            "line": lineno,
            "byte_start": 0,  # filled by byte-offset pass below if needed
            "source_hash": sha256(line.encode("utf-8")),
            "episode": episode,
            "label": label,
            "voice_ids": parsed["voice_ids"],
            "segments": parsed["segments"],
            "portuguese_text": parsed["visible_text"],
            "raw": parsed["raw"],
            "normal": normalize_match(parsed["visible_text"]),
        })
    return rows


def align_up_to_catbox(
    source_rows: list[dict[str, Any]], catbox_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Anchor by the exact ordered voice-id sequence, then by monotonic order."""
    by_voice: dict[tuple[str, ...], list[int]] = {}
    for target in catbox_rows:
        # Catbox's voice_ids() keeps the leading 'v' from '@v19/...'; the
        # ONScripter source has no such prefix.
        key = tuple(v[1:] if v.startswith("v") else v for v in target["voice_ids"])
        by_voice.setdefault(key, []).append(target["ordinal"])

    all_ordinals = sorted(t["ordinal"] for t in catbox_rows)
    target_by_ordinal = {t["ordinal"]: t for t in catbox_rows}
    results: list[dict[str, Any]] = []
    last_ordinal = -1
    for row in source_rows:
        key = tuple(row["voice_ids"])
        candidates = by_voice.get(key, [])
        status = "unmatched"
        ordinal = None
        reason = "no exact voice sequence match"
        if len(candidates) == 1:
            ordinal = candidates[0]
            status = "verified"
            reason = "unique full voice-sequence match"
        elif len(candidates) > 1:
            after = [o for o in candidates if o > last_ordinal]
            if len(after) == 1:
                ordinal = after[0]
                status = "candidate"
                reason = f"voice sequence has {len(candidates)} matches; monotonic order selected this one"
            else:
                reason = f"voice sequence has {len(candidates)} matches; ambiguous"
        if ordinal is not None:
            last_ordinal = max(last_ordinal, ordinal)
        target = target_by_ordinal.get(ordinal)
        results.append({
            "voice_key": key,
            "target_id": target["target_id"] if target else None,
            "ordinal": ordinal,
            "target_line": target["line"] if target else None,
            "target_hash": target["statement_sha256"] if target else None,
            "english_reference": target["english_text"] if target else None,
            "voice_ids": row["voice_ids"],
            "source_file": row["source_file"],
            "source_line": row["line"],
            "source_hash": row["source_hash"],
            "source_hash_raw": row["source_hash"],
            "episode": row["episode"],
            "scene": row["label"],
            "portuguese_text": row["portuguese_text"],
            "segments": row["segments"],
            "status": status,
            "confidence_reason": reason,
            "reviewer_notes": "",
            "approved": False,
        })

    # Fill unvoiced lines only when the count of source lines and target
    # messages between two voiced anchors is exactly the same.  This is
    # monotonic-order evidence, so the result stays a candidate.
    anchors = [(i, r["ordinal"]) for i, r in enumerate(results) if r["ordinal"] is not None]
    for (left_i, left_o), (right_i, right_o) in zip(anchors, anchors[1:]):
        gap_src = results[left_i + 1:right_i]
        lo = bisect.bisect_right(all_ordinals, left_o)
        hi = bisect.bisect_left(all_ordinals, right_o)
        gap_tgt = [target_by_ordinal[o] for o in all_ordinals[lo:hi]]
        if not gap_src or len(gap_src) != len(gap_tgt):
            continue
        for row, target in zip(gap_src, gap_tgt):
            row["ordinal"] = target["ordinal"]
            row["target_id"] = target["target_id"]
            row["target_line"] = target["line"]
            row["target_hash"] = target["statement_sha256"]
            row["english_reference"] = target["english_text"]
            row["status"] = "candidate"
            row["confidence_reason"] = (
                f"unvoiced line aligned by exact count/order between voice anchors "
                f"{left_o} and {right_o}; human review required"
            )
    return results


def _target_token_voices(tokens: list) -> dict[int, str | None]:
    current: str | None = None
    result: dict[int, str | None] = {}
    for index, token in enumerate(tokens):
        if token.kind == "arg" and token.marker == "v":
            current = token.value[2:-1]  # strip '@v' and '.'
        elif token.kind == "text":
            # A space left after ``@k `` is layout, not a voice-bearing slot.
            if token.value.strip():
                result[index] = current
    return result


def build_ptbr_text_from_up(target_text: str, segments: list[dict[str, Any]]) -> str:
    """Replace target text tokens using the source's voice anchors.

    Several source segments may share one voice id (the Switch target often
    merges them into a single ``@[...]@]`` token); they are concatenated in
    order.  Unvoiced segments are assigned to unvoiced target slots, leaving
    the leading speaker-name token untouched on voiced messages.
    """
    tokens = tokenize_markup(target_text)
    token_voices = _target_token_voices(tokens)

    by_voice: dict[str, list[str]] = {}
    unvoiced_parts: list[str] = []
    for seg in segments:
        if seg["voice"]:
            by_voice.setdefault(seg["voice"], []).append(seg["text"])
        else:
            unvoiced_parts.append(seg["text"])

    replacements: dict[int, str] = {}
    for index, voice in token_voices.items():
        if voice and voice in by_voice:
            replacements[index] = "".join(by_voice[voice])

    missing = {v for v in by_voice if v not in set(token_voices.values())}
    if missing:
        raise ValueError(f"source voices absent from target: {sorted(missing)}")

    unvoiced_tokens = [i for i, v in token_voices.items() if v is None]
    if any(token_voices.values()):
        # First unvoiced token is the speaker name plate; keep it.
        unvoiced_tokens = unvoiced_tokens[1:]
    if len(unvoiced_parts) > len(unvoiced_tokens):
        raise ValueError(
            f"unvoiced segment count {len(unvoiced_parts)} > target {len(unvoiced_tokens)}"
        )
    for index, text in zip(unvoiced_tokens, unvoiced_parts):
        replacements[index] = text

    return "".join(replacements.get(i, t.value) for i, t in enumerate(tokens))


def apply_up_edits(
    script_path: str | Path,
    review_path: str | Path,
    targets_path: str | Path,
    output_path: str | Path,
    log_path: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write a patched script from approved UP-source review rows."""
    data = read_bytes(script_path)
    rows = [r for r in (json.loads(l) for l in Path(review_path).read_text(encoding="utf-8").splitlines() if l.strip()) if r.get("approved")]
    targets = {r["ordinal"]: r for r in (
        json.loads(l) for l in Path(targets_path).read_text(encoding="utf-8").splitlines() if l.strip()
    )}
    errors: list[str] = []
    edits = []
    for row in rows:
        if row.get("status") not in ("verified", "candidate"):
            errors.append(f"row {row.get('source_line')} not verified/candidate")
            continue
        target = targets.get(row.get("ordinal"))
        if target is None:
            errors.append(f"ordinal {row.get('ordinal')} missing target")
            continue
        start, end = target["start"], target["end"]
        old = data[start:end]
        if sha256(old) != target["statement_sha256"]:
            errors.append(f"ordinal {row['ordinal']} target hash mismatch")
            continue
        try:
            new_text = build_ptbr_text_from_up(target["english_text"], row["segments"])
        except ValueError as exc:
            errors.append(f"ordinal {row['ordinal']}: {exc}")
            continue
        if tag_skeleton(target["english_text"], behavioral_only=True) != tag_skeleton(new_text, behavioral_only=True):
            errors.append(f"ordinal {row['ordinal']}: behavioural tags changed")
            continue
        if voice_ids(target["english_text"]) != voice_ids(new_text):
            errors.append(f"ordinal {row['ordinal']}: voice sequence changed")
            continue
        encoded = ruby_escape(new_text.encode("utf-8"))
        statement = (b"s.layout('" if target["wrapped"] else b"'") + encoded + (b"')" if target["wrapped"] else b"'")
        marker = old.find(b"'")
        if b"s.layout(" in old[:marker]:
            marker = old.rfind(b"s.layout(")
        edits.append((start, end, old[:marker] + statement, row, new_text))
    if errors:
        raise SystemExit("apply aborted:\n  - " + "\n  - ".join(errors))
    result = data
    for start, end, statement, row, new_text in sorted(edits, key=lambda e: e[0], reverse=True):
        result = result[:start] + statement + result[end:]
    log = {
        "input_sha256": sha256(data),
        "output_sha256": sha256(result),
        "applied": [
            {"ordinal": row["ordinal"], "source_line": row.get("source_line"), "new_text": new_text}
            for _, _, _, row, new_text in edits
        ],
        "dry_run": dry_run,
    }
    if not dry_run:
        atomic_write(output_path, result)
    if log_path:
        Path(log_path).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return log
