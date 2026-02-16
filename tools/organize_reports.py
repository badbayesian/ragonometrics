#!/usr/bin/env python
"""Organize reports directory into subfolders by artifact type."""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class Rule:
    pattern: str
    target_subdir: str


RULES: List[Rule] = [
    Rule("workflow-report-*.json", "workflow"),
    Rule("prep-manifest-*.json", "prep"),
    Rule("audit-workflow-report-*", "audit"),
    Rule("workstream-comparison-*", "workstream"),
    Rule("postgres-current-tables-and-schemas.md", "misc"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Organize files under reports/ into subfolders.")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"), help="Reports root directory.")
    parser.add_argument("--dry-run", action="store_true", help="Preview moves without changing files.")
    parser.add_argument("--force", action="store_true", help="Overwrite target files if they already exist.")
    return parser.parse_args()


def should_skip(path: Path, reports_dir: Path) -> bool:
    if not path.is_file():
        return True
    # Keep archived tree untouched.
    try:
        rel = path.relative_to(reports_dir)
    except Exception:
        return True
    parts = rel.parts
    if not parts:
        return True
    return parts[0] == "archived"


def move_one(src: Path, dst: Path, *, dry_run: bool, force: bool) -> str:
    if dst.exists():
        if src.resolve() == dst.resolve():
            return "unchanged"
        if not force:
            return "skipped_exists"
        if not dry_run:
            if dst.is_file():
                dst.unlink()
            else:
                shutil.rmtree(dst)
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return "moved"


def main() -> int:
    args = parse_args()
    reports_dir = args.reports_dir
    if not reports_dir.exists() or not reports_dir.is_dir():
        print(f"[error] reports dir not found: {reports_dir}")
        return 1

    total = 0
    moved = 0
    skipped_exists = 0
    unchanged = 0

    for rule in RULES:
        target_root = reports_dir / rule.target_subdir
        if not args.dry_run:
            target_root.mkdir(parents=True, exist_ok=True)

        for src in sorted(reports_dir.glob(rule.pattern)):
            if should_skip(src, reports_dir):
                continue
            dst = target_root / src.name
            status = move_one(src, dst, dry_run=args.dry_run, force=args.force)
            total += 1
            if status == "moved":
                moved += 1
            elif status == "skipped_exists":
                skipped_exists += 1
            elif status == "unchanged":
                unchanged += 1
            print(f"[{status}] {src} -> {dst}")

    print(
        f"[done] total={total} moved={moved} "
        f"skipped_exists={skipped_exists} unchanged={unchanged} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
