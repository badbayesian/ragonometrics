#!/usr/bin/env python
"""Enqueue async full-workflow jobs for every PDF in a directory."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    from ragonometrics.integrations.rq_queue import enqueue_workflow
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ragonometrics.integrations.rq_queue import enqueue_workflow

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - fallback for environments without tqdm
    tqdm = None


DEFAULT_QUESTION = (
    "What are the paper's main contribution, identification strategy, key results, and limitations?"
)


def slugify(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "paper"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enqueue one async full workflow run per PDF."
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=Path("papers"),
        help="Directory containing PDF files (default: papers).",
    )
    parser.add_argument(
        "--pattern",
        type=str,
        default="*.pdf",
        help="Glob pattern for selecting files (default: *.pdf).",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=os.environ.get("DATABASE_URL"),
        help="Queue DB URL (defaults to DATABASE_URL).",
    )
    parser.add_argument(
        "--meta-db-url",
        type=str,
        default=None,
        help="Workflow metadata/index DB URL (default: unset; worker fallback is used).",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Optional config.toml path.",
    )
    parser.add_argument(
        "--question",
        type=str,
        default=DEFAULT_QUESTION,
        help="Main workflow question prompt.",
    )
    parser.add_argument(
        "--report-question-set",
        choices=("none", "structured", "agentic", "both"),
        default="both",
        help="Report question mode (default: both).",
    )
    parser.add_argument(
        "--arm",
        type=str,
        default="gpt-5-nano",
        help="Arm label for run grouping (default: gpt-5-nano).",
    )
    parser.add_argument(
        "--trigger-source",
        type=str,
        default="cli",
        help="Trigger source label (default: cli).",
    )
    parser.add_argument(
        "--workstream-prefix",
        type=str,
        default="",
        help="Optional prefix for generated workstream IDs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print jobs that would be enqueued without writing to DB.",
    )
    parser.add_argument(
        "--no-tqdm",
        action="store_true",
        help="Disable per-paper tqdm progress bar.",
    )
    parser.add_argument(
        "--sub-tqdm",
        action="store_true",
        help="Enable nested per-paper sub-progress bars.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    papers_dir = args.papers_dir
    if not papers_dir.exists() or not papers_dir.is_dir():
        print(f"[error] papers dir not found: {papers_dir}", file=sys.stderr)
        return 1

    db_url = (args.db_url or "").strip()
    if not db_url and not args.dry_run:
        print(
            "[error] --db-url is required (or set DATABASE_URL).",
            file=sys.stderr,
        )
        return 1
    meta_db_url = (args.meta_db_url or "").strip() or None

    pdfs = sorted(papers_dir.glob(args.pattern))
    pdfs = [p for p in pdfs if p.is_file() and p.suffix.lower() == ".pdf"]
    if not pdfs:
        print(f"[error] no PDFs matched {papers_dir / args.pattern}", file=sys.stderr)
        return 1

    prefix = slugify(args.workstream_prefix) if args.workstream_prefix else ""
    queued = 0
    use_tqdm = bool(tqdm is not None and not args.no_tqdm)
    sub_tqdm = bool(args.sub_tqdm and use_tqdm)
    outer_iter = (
        tqdm(pdfs, desc="Enqueue workflows", unit="paper")
        if use_tqdm
        else pdfs
    )
    for pdf in outer_iter:
        inner = None
        if sub_tqdm:
            inner = tqdm(
                total=3,
                desc=f"{pdf.name[:32]}",
                unit="step",
                leave=False,
                position=1,
            )
        stem_slug = slugify(pdf.stem)
        if inner is not None:
            inner.set_postfix_str("build-workstream")
            inner.update(1)
        workstream_id = f"{prefix}-{stem_slug}" if prefix else stem_slug
        if args.dry_run:
            msg = f"[dry-run] would enqueue: paper={pdf} workstream_id={workstream_id} arm={args.arm}"
            if use_tqdm:
                tqdm.write(msg)
            else:
                print(msg)
            if inner is not None:
                inner.set_postfix_str("dry-run")
                inner.update(2)
                inner.close()
            queued += 1
            continue
        if inner is not None:
            inner.set_postfix_str("enqueue")
            inner.update(1)
        job = enqueue_workflow(
            papers_dir=pdf,
            db_url=db_url,
            config_path=args.config_path,
            meta_db_url=meta_db_url,
            agentic=True,
            question=args.question,
            agentic_citations=True,
            report_question_set=args.report_question_set,
            workstream_id=workstream_id,
            arm=args.arm,
            trigger_source=args.trigger_source,
        )
        msg = f"[enqueued] job_id={job.id} paper={pdf.name} workstream_id={workstream_id} arm={args.arm}"
        if use_tqdm:
            tqdm.write(msg)
        else:
            print(msg)
        if inner is not None:
            inner.set_postfix_str("log")
            inner.update(1)
            inner.close()
        queued += 1

    print(f"[done] queued {queued} workflow job(s).")
    if not args.dry_run:
        print("[note] ensure a queue worker is running to execute async jobs.")
        print("       python -m ragonometrics.integrations.rq_queue --worker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
