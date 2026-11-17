"""Command line entry point for the Umineko pt-BR port tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .align import align_all, load_jsonl, select_manual_pilot, write_review
from .apply import apply_edits
from .extract import extract_catbox, extract_florescer, write_extractions
from .validate import (
    glyph_report,
    validate_noop,
    validate_patch,
    validate_review,
    validate_toml,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="umineko-ptbr", description=__doc__)
    parser.add_argument("--version", action="version", version="0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="extract structured records from the upstream repositories")
    p.add_argument("--florescer-root", required=True)
    p.add_argument("--catbox-root", required=True)
    p.add_argument("--work-dir", default="work")
    p.add_argument("--reports-dir", default="reports")

    p = sub.add_parser("align", help="build auditable alignment/review records")
    p.add_argument("--work-dir", default="work")
    p.add_argument("--reports-dir", default="reports")
    p.add_argument("--pilot-start", type=int, default=None)

    p = sub.add_parser("pilot", help="select a contiguous source range for manual review")
    p.add_argument("--review", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--start-line", type=int, required=True)
    p.add_argument("--end-line", type=int, required=True)

    p = sub.add_parser("confirm", help="record manual confirmation of a pilot review file")
    p.add_argument("--review", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--note", required=True)

    p = sub.add_parser("approve", help="mark verified review rows as approved")
    p.add_argument("--review", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--ordinals", required=True, help="comma-separated message ordinals")
    p.add_argument("--note", default="manually reviewed")

    p = sub.add_parser("apply", help="write a patched copy of script.rb from approved rows")
    p.add_argument("--script", required=True)
    p.add_argument("--review", required=True)
    p.add_argument("--targets", required=True)
    p.add_argument("--originals", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("validate-review", help="validate a review manifest")
    p.add_argument("--review", required=True)

    p = sub.add_parser("validate-toml", help="validate localized.toml")
    p.add_argument("--toml", required=True)
    p.add_argument("--reference", default=None)

    p = sub.add_parser("validate-patch", help="compare original and patched script.rb")
    p.add_argument("--original", required=True)
    p.add_argument("--patched", required=True)
    p.add_argument("--expected-ordinals", required=True)
    p.add_argument("--output", required=True)

    p = sub.add_parser("validate-noop", help="prove a no-op patch is byte-identical")
    p.add_argument("--original", required=True)
    p.add_argument("--patched", required=True)

    p = sub.add_parser("report", help="print alignment and extraction summaries")
    p.add_argument("--work-dir", default="work")

    p = sub.add_parser("smoke-test", help="check a user-supplied assets folder and print launch steps")
    p.add_argument("--assets-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "extract":
        catbox, catbox_stats = extract_catbox(args.catbox_root)
        florescer, florescer_stats = extract_florescer(args.florescer_root)
        summary = write_extractions(catbox, catbox_stats, florescer, florescer_stats, args.work_dir)
        Path(args.reports_dir).mkdir(parents=True, exist_ok=True)
        (Path(args.reports_dir) / "inventory.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "align":
        print(json.dumps(align_all(args.work_dir, args.reports_dir, args.pilot_start), ensure_ascii=False, indent=2))
        return 0

    if args.command == "pilot":
        rows = select_manual_pilot(
            load_jsonl(args.review), args.scenario, args.start_line, args.end_line
        )
        write_review(args.output, rows)
        print(f"pilot rows: {len(rows)} (ordinals {sorted({r['ordinal'] for r in rows})[:5]}...)")
        return 0

    if args.command == "confirm":
        rows = load_jsonl(args.review)
        changed = 0
        for row in rows:
            if row.get("ordinal") is not None:
                row["status"] = "verified"
                row["approved"] = True
                row["reviewer_notes"] = args.note
                changed += 1
        write_review(args.output, rows)
        print(f"manually confirmed {changed} rows")
        return 0

    if args.command == "approve":
        ordinals = {int(x) for x in args.ordinals.split(",") if x.strip()}
        rows = load_jsonl(args.review)
        count = 0
        for row in rows:
            if row.get("ordinal") in ordinals and row.get("status") == "verified":
                row["approved"] = True
                row["reviewer_notes"] = args.note
                count += 1
        Path(args.output).write_text(
            "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8",
        )
        print(f"approved {count} verified rows")
        return 0

    if args.command == "apply":
        log = apply_edits(
            args.script, args.review, args.targets, args.originals, args.output, args.dry_run
        )
        Path(args.log).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"applied {len(log['applied'])} edits; output {log['output_sha256']}")
        return 0

    if args.command == "validate-review":
        result = validate_review(args.review)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if args.command == "validate-toml":
        result = validate_toml(args.toml, args.reference)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if args.command == "validate-patch":
        expected = {int(x) for x in args.expected_ordinals.split(",") if x.strip()}
        result = validate_patch(args.original, args.patched, expected)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if args.command == "validate-noop":
        result = validate_noop(args.original, args.patched)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if args.command == "report":
        for name in ("extraction_summary.json", "alignment_summary.json"):
            path = Path(args.work_dir) / name if name == "extraction_summary.json" else Path("reports") / name
            if path.exists():
                print(f"=== {path} ===")
                print(path.read_text(encoding="utf-8"))
        return 0

    if args.command == "smoke-test":
        assets = Path(args.assets_dir)
        required = ["data.rom", "mod.rom", "localized.toml"]
        found = {name: (assets / name).is_file() for name in required}
        print(json.dumps({"assets_dir": str(assets), "files": found}, indent=2))
        print("Launch:")
        print("  cargo run --release -p shin -- --game umineko --assets-dir " + str(assets.resolve()))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
