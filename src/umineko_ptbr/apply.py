"""Strict, byte-preserving replacement of reviewed 0x86 strings.

Only rows explicitly approved by a reviewer are applied.  Every edit is
guarded by the SHA-256 of the exact original statement; any mismatch aborts
the whole operation before a single output byte is written.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .align import load_jsonl
from .common import (
    atomic_write,
    normalize_match,
    normalize_source_line,
    read_bytes,
    ruby_escape,
    sha256,
    tag_skeleton,
    tokenize_markup,
    voice_ids,
)


@dataclass(frozen=True)
class Edit:
    ordinal: int
    start: int
    end: int
    old_hash: str
    new_statement: bytes
    source_file: str
    source_line: int
    new_text: str


def _text_token_indices(tokens: list) -> list[int]:
    return [i for i, token in enumerate(tokens) if token.kind == "text"]


def _token_voices(tokens: list) -> dict[int, str | None]:
    """Map each visible-text token to the voice tag immediately before it."""
    current: str | None = None
    result: dict[int, str | None] = {}
    for index, token in enumerate(tokens):
        if token.kind == "arg" and token.marker == "v":
            current = token.value[1:-1]
        elif token.kind == "text":
            result[index] = current
    return result


def build_ptbr_text(
    target_text: str,
    japanese_original: str,
    japanese_row_text: str,
    portuguese_text: str,
) -> str:
    """Replace one visible text span while preserving every @-tag verbatim.

    Voice tags are the primary anchor: a source line is mapped to the text
    token preceded by the same ``@v...`` tag in the target.  Positional
    alignment is only a fallback for unvoiced messages with identical token
    counts.  Behavioural tags, voice order, click/wait/end markers and
    external-code tags are never modified.
    """
    target_tokens = tokenize_markup(target_text)
    original_tokens = tokenize_markup(japanese_original)
    original_indices = _text_token_indices(original_tokens)
    target_indices = _text_token_indices(target_tokens)

    wanted = {normalize_match(japanese_row_text), normalize_source_line(japanese_row_text)}
    matches = [
        idx for idx in original_indices
        if normalize_match(original_tokens[idx].value) in wanted
    ]
    if len(matches) != 1:
        raise ValueError(f"source Japanese line matches {len(matches)} original tokens")

    original_voices = _token_voices(original_tokens)
    target_voices = _token_voices(target_tokens)
    voice = original_voices.get(matches[0])
    if voice:
        candidates = [idx for idx, value in target_voices.items() if value == voice]
        if len(candidates) != 1:
            raise ValueError(f"voice {voice} maps to {len(candidates)} target text tokens")
        token_index = candidates[0]
    else:
        if len(target_indices) != len(original_indices):
            raise ValueError("unvoiced target/original have different visible-text token counts")
        token_index = target_indices[original_indices.index(matches[0])]

    rebuilt: list[str] = []
    for i, token in enumerate(target_tokens):
        rebuilt.append(portuguese_text if i == token_index else token.value)
    return "".join(rebuilt)


def plan_edits(
    script_path: str | Path,
    review_path: str | Path,
    targets_path: str | Path,
    originals_path: str | Path,
) -> tuple[bytes, list[Edit], list[str]]:
    """Compute all edits; raise before writing if any invariant fails."""
    data = read_bytes(script_path)
    rows = load_jsonl(review_path)
    targets = {r["ordinal"]: r for r in load_jsonl(targets_path)}
    originals = {r["ordinal"]: r for r in load_jsonl(originals_path)}
    errors: list[str] = []
    by_ordinal: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.get("approved"):
            continue
        if row.get("status") != "verified":
            errors.append(f"row {row.get('source_file')}:{row.get('source_line')} is not verified")
            continue
        if row.get("ordinal") is None:
            errors.append("approved row has no ordinal")
            continue
        by_ordinal[row["ordinal"]].append(row)

    edits: list[Edit] = []
    for ordinal, group in sorted(by_ordinal.items()):
        target = targets.get(ordinal)
        original = originals.get(ordinal)
        if target is None or original is None:
            errors.append(f"ordinal {ordinal}: missing target/original record")
            continue
        start, end = target["start"], target["end"]
        old_bytes = data[start:end]
        old_hash = sha256(old_bytes)
        if old_hash != target["statement_sha256"]:
            errors.append(
                f"ordinal {ordinal}: target hash mismatch (upstream changed); expected "
                f"{target['statement_sha256']}, found {old_hash}"
            )
            continue
        if any(row.get("target_hash") != old_hash for row in group):
            errors.append(f"ordinal {ordinal}: review row hash is stale")
            continue
        target_text = target["english_text"]
        original_text = original["japanese_text"]
        new_text = target_text
        for row in sorted(group, key=lambda r: r["source_line"]):
            try:
                new_text = build_ptbr_text(
                    new_text, original_text, row["japanese_text"], row["portuguese_text"]
                )
            except ValueError as exc:
                errors.append(f"ordinal {ordinal}: {exc}")
                break
        if tag_skeleton(target_text, behavioral_only=True) != tag_skeleton(new_text, behavioral_only=True):
            errors.append(f"ordinal {ordinal}: behavioural tag skeleton changed")
            continue
        if voice_ids(target_text) != voice_ids(new_text):
            errors.append(f"ordinal {ordinal}: voice sequence changed")
            continue
        try:
            encoded = ruby_escape(new_text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            errors.append(f"ordinal {ordinal}: cannot encode text: {exc}")
            continue
        if target["wrapped"]:
            statement = b"s.layout('" + encoded + b"')"
        else:
            statement = b"'" + encoded + b"'"
        # Rebuild the statement prefix from the original bytes (never reformat).
        # The prefix ends immediately before the string expression.
        marker = old_bytes.find(b"'")
        if marker == -1:
            errors.append(f"ordinal {ordinal}: cannot locate string literal")
            continue
        if b"s.layout(" in old_bytes[:marker]:
            marker = old_bytes.rfind(b"s.layout(")
        new_statement = old_bytes[:marker] + statement
        edits.append(
            Edit(
                ordinal=ordinal,
                start=start,
                end=end,
                old_hash=old_hash,
                new_statement=new_statement,
                source_file=group[0]["source_file"],
                source_line=group[0]["source_line"],
                new_text=new_text,
            )
        )
    return data, edits, errors


def apply_edits(
    script_path: str | Path,
    review_path: str | Path,
    targets_path: str | Path,
    originals_path: str | Path,
    output_path: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    if Path(output_path).resolve() == Path(script_path).resolve():
        raise SystemExit("refusing to overwrite the original Catbox script.rb")
    data, edits, errors = plan_edits(script_path, review_path, targets_path, originals_path)
    if errors:
        raise SystemExit("apply aborted:\n  - " + "\n  - ".join(errors))
    result = data
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        result = result[: edit.start] + edit.new_statement + result[edit.end :]
    log = {
        "input_sha256": sha256(data),
        "output_sha256": sha256(result),
        "applied": [
            {
                "ordinal": e.ordinal,
                "old_statement_sha256": e.old_hash,
                "new_text": e.new_text,
                "source": f"{e.source_file}:{e.source_line}",
            }
            for e in edits
        ],
        "dry_run": dry_run,
    }
    if not dry_run:
        atomic_write(output_path, result)
    return log
