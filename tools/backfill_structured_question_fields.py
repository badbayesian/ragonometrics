"""Backfill full structured-question fields into workflow.run_records payloads.

This utility targets historical question rows that were stored in compact form
and enriches them with deterministic structured fields so Streamlit "Full"
exports can show detailed metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from ragonometrics.db.connection import connect

INVALID_QUESTION_PATTERNS = (
    re.compile(r"^\s*ResponseTextConfig\(", re.IGNORECASE),
    re.compile(r"^\s*ResponseFormatText\(", re.IGNORECASE),
)

STRUCTURED_FIELD_KEYS = [
    "question_tokens_estimate",
    "evidence_type",
    "confidence",
    "confidence_score",
    "retrieval_method",
    "citation_anchors",
    "quote_snippet",
    "table_figure",
    "data_source",
    "assumption_flag",
    "assumption_notes",
    "related_questions",
    "answer_length_chars",
]


def _normalize_question_key(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_papers_dir(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def _is_valid_structured_question_text(value: Any) -> bool:
    text = _normalize_question_key(value)
    if not text:
        return False
    if len(text) > 600:
        return False
    for pattern in INVALID_QUESTION_PATTERNS:
        if pattern.search(text):
            return False
    return True


def _payload_has_full_structured_fields(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    has_confidence_score = payload.get("confidence_score") is not None
    has_retrieval_method = bool(str(payload.get("retrieval_method") or "").strip())
    has_anchors = isinstance(payload.get("citation_anchors"), list)
    return has_confidence_score and has_retrieval_method and has_anchors


def _payload_signal_score(payload: Dict[str, Any]) -> int:
    score = 0
    anchors = payload.get("citation_anchors")
    if isinstance(anchors, list) and anchors:
        score += 4
    if payload.get("confidence_score") is not None:
        score += 2
    if payload.get("retrieval_method"):
        score += 1
    if payload.get("evidence_type"):
        score += 1
    if payload.get("quote_snippet"):
        score += 1
    return score


def _build_default_structured_fields(*, question: str, answer: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    quote_snippet = str(payload.get("quote_snippet") or "").strip()
    if not quote_snippet:
        quote_snippet = str(answer or "").strip()[:200]
    related_questions = payload.get("related_questions")
    if not isinstance(related_questions, list):
        related_questions = []
    anchors = payload.get("citation_anchors")
    if not isinstance(anchors, list):
        anchors = []
    return {
        "question_tokens_estimate": len([token for token in str(question or "").split() if token]),
        "evidence_type": payload.get("evidence_type") or "backfilled_unknown",
        "confidence": payload.get("confidence") or "unknown",
        "confidence_score": float(payload.get("confidence_score") or 0.0),
        "retrieval_method": payload.get("retrieval_method") or "unknown",
        "citation_anchors": anchors,
        "quote_snippet": quote_snippet,
        "table_figure": payload.get("table_figure"),
        "data_source": payload.get("data_source"),
        "assumption_flag": payload.get("assumption_flag"),
        "assumption_notes": payload.get("assumption_notes"),
        "related_questions": related_questions,
        "answer_length_chars": int(payload.get("answer_length_chars") or len(str(answer or ""))),
    }


def _merge_missing_fields(payload: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})
    for key in STRUCTURED_FIELD_KEYS:
        if key not in fields:
            continue
        current = out.get(key)
        if current is None or current == "" or (key == "citation_anchors" and not isinstance(current, list)):
            out[key] = fields.get(key)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill full structured-question fields in workflow.run_records.")
    parser.add_argument("--db-url", type=str, default=None, help="Postgres URL; defaults to DATABASE_URL env var.")
    parser.add_argument("--papers-like", type=str, default="", help="Optional case-insensitive papers_dir substring filter.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max rows to update (0 means no limit).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates. Without this flag, the script runs in dry-run mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_url = str(args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required (or pass --db-url).")

    conn = connect(db_url, require_migrated=True)
    try:
        cur = conn.cursor()
        sql = """
            SELECT
                q.run_id,
                q.step,
                q.record_key,
                COALESCE(q.question_id, q.payload_json ->> 'id', q.output_json ->> 'id', '') AS question_id,
                COALESCE(q.payload_json ->> 'question', q.output_json ->> 'question', '') AS question_text,
                COALESCE(q.payload_json ->> 'answer', q.output_json ->> 'answer', '') AS answer_text,
                COALESCE(q.payload_json, '{}'::jsonb) AS payload_json,
                COALESCE(q.output_json, '{}'::jsonb) AS output_json,
                COALESCE(q.metadata_json, '{}'::jsonb) AS metadata_json,
                COALESCE(r.papers_dir, '') AS papers_dir,
                q.created_at
            FROM workflow.run_records q
            JOIN workflow.run_records r
              ON r.run_id = q.run_id
             AND r.record_kind = 'run'
             AND r.step = ''
             AND r.record_key = 'main'
            WHERE q.record_kind = 'question'
              AND q.step = 'agentic'
              AND COALESCE(q.payload_json ->> 'question', q.output_json ->> 'question', '') <> ''
        """
        params: List[Any] = []
        papers_like = str(args.papers_like or "").strip().lower()
        if papers_like:
            sql += " AND lower(replace(COALESCE(r.papers_dir, ''), '\\\\', '/')) LIKE %s"
            params.append(f"%{papers_like}%")
        sql += " ORDER BY q.created_at DESC"
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

        donors: Dict[Tuple[str, str], Tuple[int, str, Dict[str, Any]]] = {}
        for row in rows:
            question_text = str(row[4] or "")
            if not _is_valid_structured_question_text(question_text):
                continue
            payload_json = row[6] if isinstance(row[6], dict) else {}
            output_json = row[7] if isinstance(row[7], dict) else {}
            payload_obj = output_json if output_json else payload_json
            if not _payload_has_full_structured_fields(payload_obj):
                continue
            papers_key = _normalize_papers_dir(row[9])
            question_key = _normalize_question_key(question_text)
            donor_key = (papers_key, question_key)
            donor_fields = {key: payload_obj.get(key) for key in STRUCTURED_FIELD_KEYS if key in payload_obj}
            score = _payload_signal_score(payload_obj)
            created_at_text = str(row[10].isoformat() if row[10] is not None else "")
            existing = donors.get(donor_key)
            if existing is None or (score, created_at_text) > (existing[0], existing[1]):
                donors[donor_key] = (score, created_at_text, donor_fields)

        updated = 0
        skipped_invalid = 0
        already_full = 0
        donor_backfills = 0
        default_backfills = 0
        limit = max(0, int(args.limit or 0))
        backfilled_at = datetime.now(timezone.utc).isoformat()

        for row in rows:
            if limit and updated >= limit:
                break
            run_id = str(row[0] or "")
            step = str(row[1] or "")
            record_key = str(row[2] or "")
            question_text = str(row[4] or "")
            answer_text = str(row[5] or "")
            payload_json = row[6] if isinstance(row[6], dict) else {}
            output_json = row[7] if isinstance(row[7], dict) else {}
            metadata_json = row[8] if isinstance(row[8], dict) else {}
            papers_key = _normalize_papers_dir(row[9])
            question_key = _normalize_question_key(question_text)

            if not _is_valid_structured_question_text(question_text):
                skipped_invalid += 1
                continue

            payload_obj = output_json if output_json else payload_json
            if _payload_has_full_structured_fields(payload_obj):
                already_full += 1
                continue

            donor = donors.get((papers_key, question_key))
            donor_fields: Dict[str, Any] = donor[2] if donor else {}
            if donor_fields:
                fields = _build_default_structured_fields(
                    question=question_text,
                    answer=answer_text,
                    payload={**payload_json, **donor_fields},
                )
                donor_backfills += 1
                source = "donor"
            else:
                fields = _build_default_structured_fields(
                    question=question_text,
                    answer=answer_text,
                    payload=payload_json,
                )
                default_backfills += 1
                source = "default"

            merged_payload = _merge_missing_fields(payload_json, fields)
            if merged_payload == payload_json:
                continue

            if args.apply:
                merged_meta = dict(metadata_json)
                merged_meta["full_fields_backfilled"] = True
                merged_meta["full_fields_backfilled_at"] = backfilled_at
                merged_meta["full_fields_backfill_source"] = source
                cur.execute(
                    """
                    UPDATE workflow.run_records
                    SET
                        payload_json = %s::jsonb,
                        metadata_json = %s::jsonb,
                        updated_at = NOW()
                    WHERE run_id = %s
                      AND record_kind = 'question'
                      AND step = %s
                      AND record_key = %s
                    """,
                    (
                        json.dumps(merged_payload, ensure_ascii=False, default=str),
                        json.dumps(merged_meta, ensure_ascii=False, default=str),
                        run_id,
                        step,
                        record_key,
                    ),
                )
            updated += 1

        if args.apply:
            conn.commit()
        else:
            conn.rollback()

        summary = {
            "mode": "apply" if args.apply else "dry_run",
            "rows_scanned": len(rows),
            "rows_updated": updated,
            "rows_already_full": already_full,
            "rows_skipped_invalid_question": skipped_invalid,
            "rows_backfilled_from_donor": donor_backfills,
            "rows_backfilled_with_defaults": default_backfills,
            "donor_keys_available": len(donors),
            "papers_like": papers_like,
            "limit": limit,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

