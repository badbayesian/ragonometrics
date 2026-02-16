from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


MOJIBAKE_MARKERS = (
    "â€",
    "â€“",
    "â€”",
    "â€˜",
    "â€™",
    "â€œ",
    "â€�",
    "Â",
    "Ã",
    "â€¢",
    "â‰",
    "â†",
    "Ï",
    "âˆ",
)

COMMON_REPLACEMENTS = {
    "â€”": "—",
    "â€“": "–",
    "â€˜": "‘",
    "â€™": "’",
    "â€œ": "“",
    "â€�": "”",
    "â€¢": "•",
    "â€¦": "…",
    "â‰ˆ": "≈",
    "â‰¤": "≤",
    "â‰¥": "≥",
    "â†’": "→",
    "âˆ’": "−",
    "Ï„": "τ",
    "Ïƒ": "σ",
    "Ï†": "φ",
    "Î±": "α",
    "Î²": "β",
    "Î³": "γ",
    "Î¼": "μ",
    "Î»": "λ",
    "Î´": "δ",
    "Î¸": "θ",
    "Â ": " ",
    "Â": "",
}

MATH_PATTERN = re.compile(r"(\$\$.*?\$\$|\$[^$]*\$)", re.DOTALL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a workflow report JSON into an audit Markdown report.",
        epilog=(
            "Examples:\n"
            "  python tools/workflow_report_to_audit_md.py --input reports/workflow-report-<run_id>.json\n"
            "  python tools/workflow_report_to_audit_md.py --input reports/workflow-report-<run_id>.json "
            "--output reports/audit-workflow-report-<run_id>.md --full --clean-text"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="Path to workflow report JSON.")
    parser.add_argument(
        "--output",
        default=None,
        help="Output Markdown path (default: audit-workflow-report-<run_id>.md in same directory).",
    )
    parser.add_argument(
        "--full",
        dest="full",
        action="store_true",
        default=True,
        help="Include full structured Q&A appendix (default: on).",
    )
    parser.add_argument(
        "--no-full",
        dest="full",
        action="store_false",
        help="Skip structured Q&A appendix.",
    )
    parser.add_argument(
        "--clean-text",
        dest="clean_text",
        action="store_true",
        default=True,
        help="Repair common mojibake/unicode artifacts (default: on).",
    )
    parser.add_argument(
        "--no-clean-text",
        dest="clean_text",
        action="store_false",
        help="Preserve raw text without cleanup.",
    )
    return parser.parse_args()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except Exception:
        return str(path.resolve())


def _parse_iso8601(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _format_duration(started_at: Any, finished_at: Any) -> str:
    start_dt = _parse_iso8601(started_at)
    finish_dt = _parse_iso8601(finished_at)
    if not start_dt or not finish_dt:
        return "n/a"
    try:
        return str(finish_dt - start_dt)
    except Exception:
        return "n/a"


def _badness(text: str) -> int:
    score = 0
    for marker in MOJIBAKE_MARKERS:
        score += text.count(marker) * 10
    score += text.count("�") * 20
    score += sum(1 for ch in text if "\u0080" <= ch <= "\u009f") * 20
    return score


def _repair_segment(text: str) -> str:
    if not text:
        return text

    candidates = [text]
    for encoding in ("cp1252", "latin-1"):
        try:
            recoded = text.encode(encoding).decode("utf-8")
            candidates.append(recoded)
        except Exception:
            pass

    cleaned_candidates: list[str] = []
    for candidate in candidates:
        out = candidate
        for bad, good in COMMON_REPLACEMENTS.items():
            out = out.replace(bad, good)
        cleaned_candidates.append(out)

    return min(cleaned_candidates, key=_badness)


def repair_text(text: str) -> str:
    if not text:
        return text
    if not any(marker in text for marker in MOJIBAKE_MARKERS) and "�" not in text:
        return text

    parts = MATH_PATTERN.split(text)
    repaired: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            repaired.append(part)
        else:
            repaired.append(_repair_segment(part))
    return "".join(repaired)


def _sanitize(value: Any, *, clean_text: bool) -> str:
    if value is None:
        return ""
    text = str(value)
    if clean_text:
        text = repair_text(text)
    return text


def _fmt_confidence(item: dict[str, Any], *, clean_text: bool) -> str:
    label = _sanitize(item.get("confidence") or "n/a", clean_text=clean_text)
    score = item.get("confidence_score")
    if score is None:
        return label
    try:
        return f"{label} ({float(score)})"
    except Exception:
        return label


def _fmt_status_with_reason(block: Any, *, clean_text: bool) -> str:
    if not isinstance(block, dict):
        return "n/a"
    status = _sanitize(block.get("status") or "n/a", clean_text=clean_text)
    reason = _sanitize(block.get("reason"), clean_text=clean_text)
    if reason:
        return f"{status} (reason: `{reason}`)"
    return status


def _fmt_ingest(ingest: Any, *, clean_text: bool) -> str:
    if not isinstance(ingest, dict):
        return "n/a"
    bits = []
    if "num_pdfs" in ingest:
        bits.append(f"num_pdfs={ingest.get('num_pdfs')}")
    if "num_papers" in ingest:
        bits.append(f"num_papers={ingest.get('num_papers')}")
    if bits:
        return ", ".join(bits)
    status = _sanitize(ingest.get("status"), clean_text=clean_text)
    return status or "n/a"


def _fmt_enrich(enrich: Any, *, clean_text: bool) -> str:
    if not isinstance(enrich, dict):
        return "n/a"
    openalex = enrich.get("openalex")
    citec = enrich.get("citec")
    if openalex is not None or citec is not None:
        return f"openalex={openalex if openalex is not None else 'n/a'}, citec={citec if citec is not None else 'n/a'}"
    status = _sanitize(enrich.get("status"), clean_text=clean_text)
    return status or "n/a"


def _fmt_anchor(anchor: dict[str, Any], *, clean_text: bool) -> str:
    page = _sanitize(anchor.get("page"), clean_text=clean_text) or "n/a"
    start_word = anchor.get("start_word")
    end_word = anchor.get("end_word")
    words = ""
    if start_word is not None or end_word is not None:
        words = f", words={start_word if start_word is not None else '?'}-{end_word if end_word is not None else '?'}"
    section = _sanitize(anchor.get("section"), clean_text=clean_text)
    note = _sanitize(anchor.get("note"), clean_text=clean_text)
    parts = [f"page={page}{words}"]
    if section:
        parts.append(f"section={section}")
    if note:
        parts.append(f"note={note}")
    return ", ".join(parts)


def _default_output_path(input_path: Path, run_id: str) -> Path:
    return input_path.parent / f"audit-workflow-report-{run_id}.md"


def render_markdown(payload: dict[str, Any], *, input_path: Path, full: bool, clean_text: bool) -> str:
    run_id = _sanitize(payload.get("run_id") or input_path.stem.replace("workflow-report-", ""), clean_text=clean_text)
    started_at = _sanitize(payload.get("started_at"), clean_text=clean_text)
    finished_at = _sanitize(payload.get("finished_at"), clean_text=clean_text)
    papers_dir = _sanitize(payload.get("papers_dir"), clean_text=clean_text)
    duration = _format_duration(payload.get("started_at"), payload.get("finished_at"))

    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    effective = config.get("config_effective") if isinstance(config.get("config_effective"), dict) else config
    chat_model = _sanitize(effective.get("chat_model"), clean_text=clean_text)
    embedding_model = _sanitize(effective.get("embedding_model"), clean_text=clean_text)
    top_k = _sanitize(effective.get("top_k"), clean_text=clean_text)
    chunk_words = _sanitize(effective.get("chunk_words"), clean_text=clean_text)
    chunk_overlap = _sanitize(effective.get("chunk_overlap"), clean_text=clean_text)
    batch_size = _sanitize(effective.get("batch_size"), clean_text=clean_text)
    db_url_configured = bool(effective.get("database_url") or payload.get("index", {}).get("database_url"))

    prep = payload.get("prep")
    ingest = payload.get("ingest")
    enrich = payload.get("enrich")
    econ_data = payload.get("econ_data")
    agentic = payload.get("agentic") if isinstance(payload.get("agentic"), dict) else {}
    index = payload.get("index")
    report_store = payload.get("report_store")

    report_questions = agentic.get("report_questions") if isinstance(agentic.get("report_questions"), list) else []
    conf = agentic.get("report_question_confidence") if isinstance(agentic.get("report_question_confidence"), dict) else {}
    label_counts = conf.get("label_counts") if isinstance(conf.get("label_counts"), dict) else {}

    lines: list[str] = []
    lines.append(f"# Audit Report: Workflow `{run_id}`")
    lines.append("")
    lines.append("## Overview")
    lines.append(f"- Source JSON: `{_display_path(input_path)}`")
    lines.append(f"- Run ID: `{run_id}`")
    lines.append(f"- Papers input: `{papers_dir}`")
    lines.append(f"- Started at: `{started_at or 'n/a'}`")
    lines.append(f"- Finished at: `{finished_at or 'n/a'}`")
    lines.append(f"- Duration: `{duration}`")
    lines.append("")
    lines.append("## Effective Configuration")
    lines.append(f"- Chat model: `{chat_model or 'n/a'}`")
    lines.append(f"- Embedding model: `{embedding_model or 'n/a'}`")
    lines.append(f"- Top K: `{top_k or 'n/a'}`")
    lines.append(f"- Chunk words / overlap: `{chunk_words or 'n/a'}` / `{chunk_overlap or 'n/a'}`")
    lines.append(f"- Batch size: `{batch_size or 'n/a'}`")
    lines.append(f"- Database URL configured: `{db_url_configured}`")
    lines.append("")
    lines.append("## Step Outcomes")
    lines.append(f"- `prep`: `{_fmt_status_with_reason(prep, clean_text=clean_text)}`")
    lines.append(f"- `ingest`: `{_fmt_ingest(ingest, clean_text=clean_text)}`")
    lines.append(f"- `enrich`: `{_fmt_enrich(enrich, clean_text=clean_text)}`")
    lines.append(f"- `econ_data`: `{_fmt_status_with_reason(econ_data, clean_text=clean_text)}`")
    lines.append(f"- `agentic`: `{_fmt_status_with_reason(agentic, clean_text=clean_text)}`")
    lines.append(f"- `index`: `{_fmt_status_with_reason(index, clean_text=clean_text)}`")
    lines.append(f"- `report_store`: `{_fmt_status_with_reason(report_store, clean_text=clean_text)}`")
    lines.append("")
    lines.append("## Agentic Summary")
    lines.append(f"- Status: `{_sanitize(agentic.get('status') or 'n/a', clean_text=clean_text)}`")
    lines.append(f"- Main question: {_sanitize(agentic.get('question') or 'n/a', clean_text=clean_text)}")
    lines.append(f"- Report question set: `{_sanitize(agentic.get('report_questions_set') or 'n/a', clean_text=clean_text)}`")
    lines.append(f"- Structured questions generated: `{len(report_questions)}`")
    lines.append(
        "- Confidence mean/median: "
        f"`{_sanitize(conf.get('mean') if conf else 'n/a', clean_text=clean_text)}` / "
        f"`{_sanitize(conf.get('median') if conf else 'n/a', clean_text=clean_text)}`"
    )
    lines.append(
        "- Confidence labels: "
        f"low={_sanitize(label_counts.get('low', 0), clean_text=clean_text)}, "
        f"medium={_sanitize(label_counts.get('medium', 0), clean_text=clean_text)}, "
        f"high={_sanitize(label_counts.get('high', 0), clean_text=clean_text)}"
    )
    lines.append("")
    lines.append("### Final Answer")
    lines.append("")
    lines.append(_sanitize(agentic.get("final_answer") or "n/a", clean_text=clean_text))
    lines.append("")
    lines.append("### Sub-Answers")
    lines.append("")
    sub_answers = agentic.get("sub_answers") if isinstance(agentic.get("sub_answers"), list) else []
    if not sub_answers:
        lines.append("_No sub-answers recorded._")
        lines.append("")
    else:
        for i, item in enumerate(sub_answers, start=1):
            if not isinstance(item, dict):
                continue
            lines.append(f"#### Sub-answer {i}")
            lines.append(f"- Question: {_sanitize(item.get('question') or 'n/a', clean_text=clean_text)}")
            if item.get("question_tokens_estimate") is not None:
                lines.append(
                    f"- Question tokens estimate: `{_sanitize(item.get('question_tokens_estimate'), clean_text=clean_text)}`"
                )
            lines.append("- Answer:")
            lines.append("")
            lines.append(_sanitize(item.get("answer") or "n/a", clean_text=clean_text))
            lines.append("")

    if full:
        lines.append("## Structured Q&A Appendix")
        lines.append("")
        lines.append("This section mirrors `agentic.report_questions` for audit traceability.")
        lines.append("")
        if not report_questions:
            lines.append("_No structured report questions found._")
            lines.append("")
        for item in report_questions:
            if not isinstance(item, dict):
                continue
            qid = _sanitize(item.get("id") or "n/a", clean_text=clean_text)
            question = _sanitize(item.get("question") or "n/a", clean_text=clean_text)
            lines.append(f"### {qid}: {question}")
            lines.append(f"- Category: `{_sanitize(item.get('category') or 'n/a', clean_text=clean_text)}`")
            lines.append(f"- Confidence: `{_fmt_confidence(item, clean_text=clean_text)}`")
            lines.append(f"- Retrieval method: `{_sanitize(item.get('retrieval_method') or 'n/a', clean_text=clean_text)}`")
            lines.append(f"- Evidence type: `{_sanitize(item.get('evidence_type') or 'n/a', clean_text=clean_text)}`")
            if item.get("data_source") is not None:
                lines.append(f"- Data source: {_sanitize(item.get('data_source'), clean_text=clean_text)}")
            if item.get("table_figure") is not None:
                lines.append(f"- Table/Figure: {_sanitize(item.get('table_figure'), clean_text=clean_text)}")
            if item.get("assumption_flag") is not None:
                lines.append(f"- Assumption flag: `{_sanitize(item.get('assumption_flag'), clean_text=clean_text)}`")
            if item.get("assumption_notes"):
                lines.append(f"- Assumption notes: {_sanitize(item.get('assumption_notes'), clean_text=clean_text)}")
            lines.append("- Answer:")
            lines.append("")
            lines.append(_sanitize(item.get("answer") or "n/a", clean_text=clean_text))
            lines.append("")
            quote = _sanitize(item.get("quote_snippet"), clean_text=clean_text)
            if quote:
                lines.append("- Quote snippet:")
                lines.append("")
                lines.append(f"> {quote}")
                lines.append("")
            anchors = item.get("citation_anchors") if isinstance(item.get("citation_anchors"), list) else []
            if anchors:
                lines.append("- Citation anchors:")
                for anchor in anchors:
                    if isinstance(anchor, dict):
                        lines.append(f"  - {_fmt_anchor(anchor, clean_text=clean_text)}")
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"[error] Input JSON does not exist: {input_path}", file=sys.stderr)
        return 1
    if not input_path.is_file():
        print(f"[error] Input path is not a file: {input_path}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[error] Invalid JSON at {input_path}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[error] Failed to read JSON {input_path}: {exc}", file=sys.stderr)
        return 1

    run_id = str(payload.get("run_id") or input_path.stem.replace("workflow-report-", "")).strip()
    if not run_id:
        run_id = input_path.stem
    output_path = Path(args.output).expanduser().resolve() if args.output else _default_output_path(input_path, run_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    markdown = render_markdown(
        payload,
        input_path=input_path,
        full=bool(args.full),
        clean_text=bool(args.clean_text),
    )
    output_path.write_text(markdown, encoding="utf-8")
    print(f"[ok] Wrote audit markdown: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
