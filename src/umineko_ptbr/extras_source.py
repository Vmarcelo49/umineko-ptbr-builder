"""Alignment of Florescer extra-story lines to Switch messages.

Florescer's ``SCRIPT/jp`` and ``SCRIPT/pt`` files are line-aligned but carry no
voice tags.  We rebuild the voice identity from Florescer's original Japanese
``script.rb``: each Japanese line is matched to a unique text token, whose
preceding ``@v`` tag gives the voice.  The same ordinal selects the Catbox
target, so the insertion is voice-anchored exactly like the main episodes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import normalize_match, tokenize_markup
from .up_source import build_ptbr_text_from_up


def _token_index(originals: list[dict[str, Any]]) -> dict[str, list[tuple[int, str | None]]]:
    index: dict[str, list[tuple[int, str | None]]] = defaultdict(list)
    for msg in originals:
        current: str | None = None
        for token in tokenize_markup(msg["japanese_text"]):
            if token.kind == "arg" and token.marker == "v":
                current = token.value[2:-1]
            elif token.kind == "text":
                key = normalize_match(token.value)
                if key:
                    index[key].append((msg["ordinal"], current))
    return index


def build_extras_review(
    pairs_path: str | Path,
    originals_path: str | Path,
    targets_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    def load(path: str | Path) -> list[dict[str, Any]]:
        return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]

    pairs = load(pairs_path)
    originals = load(originals_path)
    targets = {t["ordinal"]: t for t in load(targets_path)}
    index = _token_index(originals)

    by_ordinal: dict[int, dict[str, Any]] = {}
    stats: dict[str, int] = defaultdict(int)

    for pair in pairs:
        key = normalize_match(pair["japanese_text"])
        candidates = index.get(key, [])
        if len(candidates) != 1:
            stats["ambiguous" if len(candidates) > 1 else "unmatched"] += 1
            continue
        ordinal, voice = candidates[0]
        target = targets.get(ordinal)
        if not target:
            stats["no_target"] += 1
            continue
        target_voices = {v[1:] if v.startswith("v") else v for v in target["voice_ids"]}
        if voice and voice not in target_voices:
            stats["voice_mismatch"] += 1
            continue
        row = by_ordinal.setdefault(ordinal, {
            "ordinal": ordinal,
            "target_id": target["target_id"],
            "target_line": target["line"],
            "target_hash": target["statement_sha256"],
            "english_reference": target["english_text"],
            "voice_ids": target["voice_ids"],
            "source_files": [],
            "source_lines": [],
            "segments": [],
            "status": "verified" if voice else "candidate",
            "confidence_reason": (
                "Japanese line matched to unique original text token with @v anchor"
                if voice else "unvoiced Japanese token; ordinal from unique text match"
            ),
            "reviewer_notes": "",
            "approved": False,
        })
        row["source_files"].append(pair["source_file_pt"])
        row["source_lines"].append(pair["line"])
        row["segments"].append({"voice": voice, "text": pair["portuguese_text"]})

    rows = sorted(by_ordinal.values(), key=lambda r: r["ordinal"])
    stats["messages"] = len(rows)
    stats["source_lines"] = len(pairs)
    return rows, dict(stats)
