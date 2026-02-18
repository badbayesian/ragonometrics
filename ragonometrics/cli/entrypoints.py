"""Primary CLI entrypoints for indexing, querying, UI, and benchmarks. Wires top-level commands to core pipeline components."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from ragonometrics.db.connection import connect, normalize_alembic_version_marker
from ragonometrics.eval.benchmark import bench_papers
from ragonometrics.eval.web_cache_benchmark import (
    benchmark_web_cached_structured_questions,
    benchmark_web_chat_turns,
    benchmark_web_tabs,
    write_web_cache_benchmark_report,
)
from ragonometrics.llm.runtime import build_llm_runtime
from ragonometrics.indexing.indexer import build_index
from ragonometrics.indexing.paper_store import store_paper_metadata
from ragonometrics.core.main import (
    embed_texts,
    load_papers,
    load_settings,
    prepare_chunks_for_paper,
    top_k_context,
)
from ragonometrics.pipeline import call_llm
from ragonometrics.core.prompts import RESEARCHER_QA_PROMPT
from ragonometrics.pipeline.query_cache import DEFAULT_CACHE_PATH, get_cached_answer, make_cache_key, set_cached_answer
from ragonometrics.integrations.openalex import format_openalex_context
from ragonometrics.integrations.citec import format_citec_context
from ragonometrics.pipeline.workflow import run_workflow
from ragonometrics.pipeline.report_store import store_workflow_reports_from_dir
from ragonometrics.integrations.rq_queue import enqueue_workflow
from ragonometrics.integrations.openalex_store import store_openalex_metadata_by_title_author
from ragonometrics.services import paper_compare as paper_compare_service


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with fallback."""
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        return int(raw)
    except Exception:
        return int(default)


def cmd_db_migrate(args: argparse.Namespace) -> int:
    """Apply Alembic migrations to the target Postgres database.

    Args:
        args (argparse.Namespace): Parsed CLI args.

    Returns:
        int: Process return code.
    """
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        print("No database URL configured. Pass --db-url or set DATABASE_URL.")
        return 1
    try:
        conn = connect(db_url, require_migrated=False)
        try:
            raw, normalized, changed = normalize_alembic_version_marker(conn)
            if changed:
                conn.commit()
                print(f"Normalized alembic revision marker: {raw} -> {normalized}")
        finally:
            conn.close()
    except Exception:
        # Migration command remains best-effort when preflight normalization fails.
        pass
    cmd = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(Path("alembic.ini").resolve()),
        "-x",
        f"db_url={db_url}",
        "upgrade",
        "head",
    ]
    return subprocess.call(cmd)


def cmd_usage(args: argparse.Namespace) -> int:
    """Print token usage rollup rows for a run/workstream.

    Args:
        args (argparse.Namespace): Parsed CLI args.

    Returns:
        int: Process return code.
    """
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        print("No database URL configured. Pass --db-url or set DATABASE_URL.")
        return 1
    if not args.run_id and not args.workstream_id:
        print("Provide at least one filter: --run-id or --workstream-id.")
        return 1

    conn = connect(db_url, require_migrated=True)
    try:
        cur = conn.cursor()
        if args.run_id:
            cur.execute(
                """
                SELECT run_id, step, model, question_id, call_count, input_tokens, output_tokens, total_tokens, cost_usd_total
                FROM observability.token_usage_rollup
                WHERE run_id = %s
                ORDER BY step, model, question_id
                """,
                (args.run_id,),
            )
        else:
            cur.execute(
                """
                SELECT
                    t.run_id, t.step, t.model, t.question_id, t.call_count,
                    t.input_tokens, t.output_tokens, t.total_tokens, t.cost_usd_total
                FROM observability.token_usage_rollup t
                JOIN workflow.run_records r
                  ON r.run_id = t.run_id
                 AND r.record_kind = 'run'
                 AND r.step = ''
                 AND r.record_key = 'main'
                WHERE r.workstream_id = %s
                ORDER BY t.run_id, t.step, t.model, t.question_id
                """,
                (args.workstream_id,),
            )
        rows = cur.fetchall() or []
    finally:
        conn.close()

    if not rows:
        print("No usage rows found.")
        return 0
    print("run_id\tstep\tmodel\tquestion_id\tcalls\tinput\toutput\ttotal\tcost_usd_total")
    for row in rows:
        print(
            f"{row[0]}\t{row[1]}\t{row[2]}\t{row[3]}\t"
            f"{int(row[4] or 0)}\t{int(row[5] or 0)}\t{int(row[6] or 0)}\t"
            f"{int(row[7] or 0)}\t{float(row[8] or 0.0):.6f}"
        )
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """Build vector indexes from PDFs.

    Args:
        args (argparse.Namespace): Additional arguments forwarded to the underlying call.

    Returns:
        int: Computed integer result.
    """
    settings = load_settings()
    papers_dir = Path(args.papers_dir) if args.papers_dir else settings.papers_dir
    pdfs = sorted(papers_dir.glob("*.pdf"))
    if args.limit and args.limit > 0:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print("No PDFs found to index.")
        return 1
    build_index(settings, pdfs, index_path=Path(args.index_path), meta_db_url=args.meta_db_url)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """Run a single query against a paper.

    Args:
        args (argparse.Namespace): Additional arguments forwarded to the underlying call.

    Returns:
        int: Computed integer result.
    """
    settings = load_settings()
    paper_path = Path(args.paper)
    papers = load_papers([paper_path])
    if not papers:
        print("No paper text extracted.")
        return 1
    paper = papers[0]
    chunks = prepare_chunks_for_paper(paper, settings)
    if not chunks:
        print("No chunks extracted.")
        return 1
    client = build_llm_runtime(settings)
    chunk_texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
    chunk_embeddings = embed_texts(client, chunk_texts, settings.embedding_model, settings.batch_size)
    context = top_k_context(
        chunks,
        chunk_embeddings,
        query=args.question,
        client=client,
        settings=settings,
        paper_path=paper.path,
    )
    model = args.model or settings.chat_model
    cache_key = make_cache_key(args.question, str(paper_path), model, context)
    cached = get_cached_answer(DEFAULT_CACHE_PATH, cache_key)
    if cached is not None:
        answer = cached
    else:
        openalex_context = format_openalex_context(paper.openalex)
        citec_context = format_citec_context(paper.citec)
        user_input = f"Context:\n{context}\n\nQuestion: {args.question}"
        prefix_parts = [ctx for ctx in (openalex_context, citec_context) if ctx]
        if prefix_parts:
            prefix = "\n\n".join(prefix_parts)
            user_input = f"{prefix}\n\n{user_input}"
        answer = call_llm(
            client,
            model=model,
            instructions=RESEARCHER_QA_PROMPT,
            user_input=user_input,
            max_output_tokens=None,
        ).strip()
        set_cached_answer(
            DEFAULT_CACHE_PATH,
            cache_key=cache_key,
            query=args.question,
            paper_path=str(paper_path),
            model=model,
            context=context,
            answer=answer,
        )
    print(answer)
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Launch the Streamlit UI.

    Args:
        args (argparse.Namespace): Additional arguments forwarded to the underlying call.

    Returns:
        int: Computed integer result.
    """
    app_path = Path(__file__).resolve().parents[1] / "ui" / "streamlit_app.py"
    try:
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app_path)])
    except FileNotFoundError:
        print("Streamlit is not installed.")
        return 1


def cmd_web(args: argparse.Namespace) -> int:
    """Launch the Flask web app (API + SPA static assets)."""
    app_target = "ragonometrics.web.app:create_app()"
    host = str(args.host or "0.0.0.0")
    port = int(args.port or 8590)
    workers = int(args.workers or 1)
    timeout = int(args.timeout or _env_int("WEB_GUNICORN_TIMEOUT", 180))
    graceful_timeout = int(args.graceful_timeout or _env_int("WEB_GUNICORN_GRACEFUL_TIMEOUT", 30))
    keep_alive = int(args.keep_alive or _env_int("WEB_GUNICORN_KEEPALIVE", 5))
    use_gunicorn = bool(args.gunicorn)
    if use_gunicorn:
        cmd = [
            sys.executable,
            "-m",
            "gunicorn",
            "--workers",
            str(workers),
            "--timeout",
            str(timeout),
            "--graceful-timeout",
            str(graceful_timeout),
            "--keep-alive",
            str(keep_alive),
            "--bind",
            f"{host}:{port}",
            app_target,
        ]
        return subprocess.call(cmd)
    try:
        os.environ.setdefault("WEB_HOST", host)
        os.environ.setdefault("WEB_PORT", str(port))
        from ragonometrics.web.app import main as web_main

        web_main()
        return 0
    except Exception as exc:
        print(f"Failed to launch web app: {exc}")
        return 1


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Run benchmark suite.

    Args:
        args (argparse.Namespace): Additional arguments forwarded to the underlying call.

    Returns:
        int: Computed integer result.
    """
    settings = load_settings()
    papers_dir = Path(args.papers_dir) if args.papers_dir else settings.papers_dir
    bench_papers(
        Path(papers_dir),
        Path(args.out),
        limit=args.limit,
        use_openai=args.use_openai,
        db_url=args.db_url,
        force_ocr=args.force_ocr,
    )
    return 0


def cmd_benchmark_web_cache(args: argparse.Namespace) -> int:
    """Benchmark concurrent web access to cached structured questions."""
    base_url = str(args.base_url or "http://localhost:8590").strip()
    identifier = str(args.identifier or os.getenv("STREAMLIT_USERNAME", "")).strip()
    password = str(args.password or os.getenv("STREAMLIT_PASSWORD", "")).strip()
    credentials_file = str(args.credentials_file or "").strip() or None
    if not credentials_file and (not identifier or not password):
        print("Provide --identifier/--password or --credentials-file.")
        return 1
    try:
        report = benchmark_web_cached_structured_questions(
            base_url=base_url,
            identifier=identifier,
            password=password,
            users=int(args.users or 20),
            iterations=int(args.iterations or 5),
            paper_id=str(args.paper_id or "").strip() or None,
            paper_name=str(args.paper_name or "").strip() or None,
            model=str(args.model or "").strip() or None,
            timeout_seconds=float(args.timeout or 30.0),
            auth_mode=str(args.auth_mode or "shared-session").strip(),
            credentials_file=credentials_file,
            think_time_ms=int(args.think_time_ms or 0),
            verify_tls=not bool(args.insecure),
        )
    except Exception as exc:
        print(f"Benchmark failed: {exc}")
        return 1

    out_path = write_web_cache_benchmark_report(report, str(args.out))
    summary = report.get("summary") if isinstance(report, dict) else {}
    coverage = report.get("cache_coverage") if isinstance(report, dict) else {}
    config = report.get("config") if isinstance(report, dict) else {}
    print(f"Wrote web cache benchmark report to {out_path}")
    print(
        "Summary: "
        f"target={int(summary.get('target_iterations') or 0)}, "
        f"successful={int(summary.get('successful_iterations') or 0)}, "
        f"failed={int(summary.get('failed_iterations') or 0)}, "
        f"throughput={float(summary.get('iterations_per_second') or 0.0):.2f} iter/s"
    )
    print(
        "Cache coverage: "
        f"avg_ratio={float(coverage.get('avg_ratio') or 0.0):.3f}, "
        f"min_ratio={float(coverage.get('min_ratio') or 0.0):.3f}, "
        f"max_ratio={float(coverage.get('max_ratio') or 0.0):.3f}, "
        f"avg_cached={float(coverage.get('avg_cached_questions') or 0.0):.2f}/"
        f"{float(coverage.get('avg_total_questions') or 0.0):.2f}"
    )
    print(f"Mode: auth_mode={config.get('auth_mode')}, users={config.get('users')}, base_url={config.get('base_url')}")

    avg_ratio = float(coverage.get("avg_ratio") or 0.0)
    min_ratio_required = float(args.min_cache_ratio or 0.0)
    if min_ratio_required > 0.0 and avg_ratio < min_ratio_required:
        print(f"Cache ratio gate failed: avg_ratio={avg_ratio:.3f} < min_required={min_ratio_required:.3f}")
        return 2
    if int(summary.get("successful_iterations") or 0) == 0:
        return 1
    return 0


def cmd_benchmark_web_tabs(args: argparse.Namespace) -> int:
    """Benchmark web tab endpoint reads under concurrent users."""
    base_url = str(args.base_url or "http://localhost:8590").strip()
    identifier = str(args.identifier or os.getenv("STREAMLIT_USERNAME", "")).strip()
    password = str(args.password or os.getenv("STREAMLIT_PASSWORD", "")).strip()
    credentials_file = str(args.credentials_file or "").strip() or None
    if not credentials_file and (not identifier or not password):
        print("Provide --identifier/--password or --credentials-file.")
        return 1
    try:
        report = benchmark_web_tabs(
            base_url=base_url,
            identifier=identifier,
            password=password,
            users=int(args.users or 20),
            iterations=int(args.iterations or 3),
            paper_id=str(args.paper_id or "").strip() or None,
            paper_name=str(args.paper_name or "").strip() or None,
            model=str(args.model or "").strip() or None,
            timeout_seconds=float(args.timeout or 30.0),
            auth_mode=str(args.auth_mode or "shared-session").strip(),
            credentials_file=credentials_file,
            think_time_ms=int(args.think_time_ms or 0),
            verify_tls=not bool(args.insecure),
            include_chat=not bool(args.no_chat),
            include_structured=not bool(args.no_structured),
            include_openalex=not bool(args.no_openalex),
            include_network=not bool(args.no_network),
            include_usage=not bool(args.no_usage),
            network_max_references=int(args.network_max_references or 10),
            network_max_citing=int(args.network_max_citing or 10),
            usage_recent_limit=int(args.usage_recent_limit or 200),
            usage_session_only=bool(args.usage_session_only),
        )
    except Exception as exc:
        print(f"Benchmark failed: {exc}")
        return 1

    out_path = write_web_cache_benchmark_report(report, str(args.out))
    summary = report.get("summary") if isinstance(report, dict) else {}
    config = report.get("config") if isinstance(report, dict) else {}
    print(f"Wrote web tabs benchmark report to {out_path}")
    print(
        "Summary: "
        f"target={int(summary.get('target_iterations') or 0)}, "
        f"successful={int(summary.get('successful_iterations') or 0)}, "
        f"failed={int(summary.get('failed_iterations') or 0)}, "
        f"throughput={float(summary.get('iterations_per_second') or 0.0):.2f} iter/s"
    )
    print(f"Mode: auth_mode={config.get('auth_mode')}, users={config.get('users')}, base_url={config.get('base_url')}")
    if int(summary.get("successful_iterations") or 0) == 0:
        return 1
    return 0


def cmd_benchmark_web_chat(args: argparse.Namespace) -> int:
    """Benchmark concurrent web chat turns and cache-hit behavior."""
    base_url = str(args.base_url or "http://localhost:8590").strip()
    identifier = str(args.identifier or os.getenv("STREAMLIT_USERNAME", "")).strip()
    password = str(args.password or os.getenv("STREAMLIT_PASSWORD", "")).strip()
    credentials_file = str(args.credentials_file or "").strip() or None
    if not credentials_file and (not identifier or not password):
        print("Provide --identifier/--password or --credentials-file.")
        return 1
    try:
        report = benchmark_web_chat_turns(
            base_url=base_url,
            identifier=identifier,
            password=password,
            users=int(args.users or 10),
            iterations=int(args.iterations or 3),
            question=str(args.question or "What is the main research question of this paper?").strip(),
            paper_id=str(args.paper_id or "").strip() or None,
            paper_name=str(args.paper_name or "").strip() or None,
            model=str(args.model or "").strip() or None,
            timeout_seconds=float(args.timeout or 60.0),
            auth_mode=str(args.auth_mode or "shared-session").strip(),
            credentials_file=credentials_file,
            think_time_ms=int(args.think_time_ms or 0),
            verify_tls=not bool(args.insecure),
            variation_mode=bool(args.variation_mode),
            top_k=int(args.top_k) if args.top_k is not None else None,
        )
    except Exception as exc:
        print(f"Benchmark failed: {exc}")
        return 1

    out_path = write_web_cache_benchmark_report(report, str(args.out))
    summary = report.get("summary") if isinstance(report, dict) else {}
    cache = report.get("chat_cache") if isinstance(report, dict) else {}
    config = report.get("config") if isinstance(report, dict) else {}
    print(f"Wrote web chat benchmark report to {out_path}")
    print(
        "Summary: "
        f"target={int(summary.get('target_iterations') or 0)}, "
        f"successful={int(summary.get('successful_iterations') or 0)}, "
        f"failed={int(summary.get('failed_iterations') or 0)}, "
        f"throughput={float(summary.get('iterations_per_second') or 0.0):.2f} iter/s"
    )
    print(
        "Chat cache: "
        f"hit_ratio={float(cache.get('cache_hit_ratio') or 0.0):.3f}, "
        f"hits={int(cache.get('hit_count') or 0)}/{int(cache.get('sample_count') or 0)}, "
        f"layers={cache.get('layer_counts') or {}}"
    )
    print(f"Mode: auth_mode={config.get('auth_mode')}, users={config.get('users')}, base_url={config.get('base_url')}")

    min_hit_ratio = float(args.min_cache_hit_ratio or 0.0)
    actual_hit_ratio = float(cache.get("cache_hit_ratio") or 0.0)
    if min_hit_ratio > 0.0 and actual_hit_ratio < min_hit_ratio:
        print(f"Cache hit gate failed: hit_ratio={actual_hit_ratio:.3f} < min_required={min_hit_ratio:.3f}")
        return 2
    if int(summary.get("successful_iterations") or 0) == 0:
        return 1
    return 0


def cmd_store_metadata(args: argparse.Namespace) -> int:
    """Store paper-level metadata in Postgres without building vector indexes.

    Args:
        args (argparse.Namespace): Additional arguments forwarded to the underlying call.

    Returns:
        int: Computed integer result.
    """
    settings = load_settings()
    papers_dir = Path(args.papers_dir) if args.papers_dir else settings.papers_dir
    pdfs = sorted(papers_dir.glob("*.pdf"))
    if args.limit and args.limit > 0:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print("No PDFs found to store metadata.")
        return 1
    count = store_paper_metadata(paper_paths=pdfs, meta_db_url=args.meta_db_url, progress=True)
    print(f"Stored metadata for {count} paper(s).")
    return 0


def cmd_store_workflow_reports(args: argparse.Namespace) -> int:
    """Store workflow report JSON files in Postgres JSONB.

    Args:
        args (argparse.Namespace): Additional arguments forwarded to the underlying call.

    Returns:
        int: Computed integer result.
    """
    settings = load_settings()
    db_url = args.meta_db_url or (settings.config_effective or {}).get("database_url")
    if not db_url:
        print("No database URL configured. Pass --meta-db-url or set DATABASE_URL.")
        return 1
    reports_dir = Path(args.reports_dir)
    stats = store_workflow_reports_from_dir(
        reports_dir=reports_dir,
        db_url=str(db_url),
        recursive=bool(args.recursive),
        limit=int(args.limit or 0),
    )
    if stats["total"] == 0:
        print(f"No workflow report files found in {reports_dir}.")
        return 1
    print(
        f"Stored {stats['stored']} workflow report(s) "
        f"(scanned {stats['total']}, skipped {stats['skipped']})."
    )
    return 0


def cmd_store_openalex_metadata(args: argparse.Namespace) -> int:
    """Resolve OpenAlex papers by title+authors and persist results to Postgres.

    Args:
        args (argparse.Namespace): Additional arguments forwarded to the underlying call.

    Returns:
        int: Computed integer result.
    """
    settings = load_settings()
    papers_dir = Path(args.papers_dir) if args.papers_dir else settings.papers_dir
    pdfs = sorted(papers_dir.glob("*.pdf"))
    if args.limit and args.limit > 0:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print("No PDFs found to store OpenAlex metadata.")
        return 1

    db_url = args.meta_db_url or (settings.config_effective or {}).get("database_url")
    if not db_url:
        print("No database URL configured. Pass --meta-db-url or set DATABASE_URL.")
        return 1

    stats = store_openalex_metadata_by_title_author(
        paper_paths=pdfs,
        db_url=str(db_url),
        progress=True,
        refresh=bool(args.refresh),
    )
    print(
        "OpenAlex title+author metadata stored for "
        f"{stats['total']} paper(s): matched={stats['matched']}, "
        f"not_found={stats['not_found']}, error={stats['error']}, skipped={stats['skipped']}."
    )
    return 0


def cmd_workflow(args: argparse.Namespace) -> int:
    """Run or enqueue the multi-step workflow.

    Args:
        args (argparse.Namespace): Additional arguments forwarded to the underlying call.

    Returns:
        int: Computed integer result.
    """
    papers_dir = Path(args.papers) if args.papers else load_settings().papers_dir
    if args.async_mode:
        queue_db_url = args.queue_db_url or args.meta_db_url
        job = enqueue_workflow(
            papers_dir,
            db_url=queue_db_url,
            config_path=Path(args.config_path) if args.config_path else None,
            meta_db_url=args.meta_db_url,
            agentic=args.agentic,
            question=args.question,
            agentic_model=args.agentic_model,
            agentic_citations=args.agentic_citations,
            report_question_set=args.report_question_set,
            workstream_id=args.workstream_id,
            arm=args.arm,
            parent_run_id=args.parent_run_id,
            trigger_source=args.trigger_source,
        )
        print(f"Enqueued workflow job: {job.id}")
        return 0

    summary = run_workflow(
        papers_dir=papers_dir,
        config_path=Path(args.config_path) if args.config_path else None,
        meta_db_url=args.meta_db_url,
        agentic=args.agentic,
        question=args.question,
        agentic_model=args.agentic_model,
        agentic_citations=args.agentic_citations,
        report_question_set=args.report_question_set,
        workstream_id=args.workstream_id,
        arm=args.arm,
        parent_run_id=args.parent_run_id,
        trigger_source=args.trigger_source,
    )
    print(f"Workflow run completed: {summary.get('run_id')}")
    print(f"Report: {summary.get('report_path')}")
    return 0


def cmd_compare_suggest(args: argparse.Namespace) -> int:
    """Suggest similar papers for a seed paper id."""
    try:
        try:
            out = paper_compare_service.suggest_similar_papers(
                str(args.paper_id or "").strip(),
                limit=int(args.limit or 20),
                project_id=str(args.project_id or "").strip() or None,
            )
        except TypeError:
            out = paper_compare_service.suggest_similar_papers(
                str(args.paper_id or "").strip(),
                limit=int(args.limit or 20),
            )
    except Exception as exc:
        print(f"Compare suggest failed: {exc}")
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_compare_create(args: argparse.Namespace) -> int:
    """Create one comparison run from paper ids and custom questions."""
    paper_ids = [str(item or "").strip() for item in list(args.paper_id or []) if str(item or "").strip()]
    questions = [str(item or "").strip() for item in list(args.question or []) if str(item or "").strip()]
    try:
        try:
            out = paper_compare_service.create_comparison_run(
                seed_paper_id=str(args.seed_paper_id or "").strip() or None,
                paper_ids=paper_ids,
                questions=questions,
                model=str(args.model or "").strip() or None,
                name=str(args.name or "").strip() or None,
                created_by_user_id=None,
                created_by_username=str(args.username or "cli"),
                project_id=str(args.project_id or "").strip() or None,
                persona_id=str(args.persona_id or "").strip() or None,
            )
        except TypeError:
            out = paper_compare_service.create_comparison_run(
                seed_paper_id=str(args.seed_paper_id or "").strip() or None,
                paper_ids=paper_ids,
                questions=questions,
                model=str(args.model or "").strip() or None,
                name=str(args.name or "").strip() or None,
                created_by_user_id=None,
                created_by_username=str(args.username or "cli"),
            )
    except Exception as exc:
        print(f"Compare create failed: {exc}")
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_compare_show(args: argparse.Namespace) -> int:
    """Show one comparison run payload."""
    try:
        out = paper_compare_service.get_comparison_run(
            str(args.comparison_id or "").strip(),
            project_id=str(args.project_id or "").strip() or None,
        )
    except TypeError:
        out = paper_compare_service.get_comparison_run(str(args.comparison_id or "").strip())
    if not out:
        print("Comparison run not found.")
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_compare_fill_missing(args: argparse.Namespace) -> int:
    """Fill missing cells for one comparison run."""
    paper_ids = [str(item or "").strip() for item in list(args.paper_id or []) if str(item or "").strip()]
    question_ids = [str(item or "").strip() for item in list(args.question_id or []) if str(item or "").strip()]
    try:
        try:
            out = paper_compare_service.fill_missing_cells(
                comparison_id=str(args.comparison_id or "").strip(),
                paper_ids=paper_ids,
                question_ids=question_ids,
                project_id=str(args.project_id or "").strip() or None,
                persona_id=str(args.persona_id or "").strip() or None,
            )
        except TypeError:
            out = paper_compare_service.fill_missing_cells(
                comparison_id=str(args.comparison_id or "").strip(),
                paper_ids=paper_ids,
                question_ids=question_ids,
            )
    except Exception as exc:
        print(f"Compare fill-missing failed: {exc}")
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_compare_export(args: argparse.Namespace) -> int:
    """Export one comparison run to JSON or CSV."""
    fmt = str(args.format or "json").strip().lower()
    try:
        try:
            out = paper_compare_service.export_comparison(
                str(args.comparison_id or "").strip(),
                export_format=fmt,
                project_id=str(args.project_id or "").strip() or None,
            )
        except TypeError:
            out = paper_compare_service.export_comparison(
                str(args.comparison_id or "").strip(),
                export_format=fmt,
            )
    except Exception as exc:
        print(f"Compare export failed: {exc}")
        return 1
    output_path = Path(args.out) if args.out else Path(out.get("filename") or f"comparison.{fmt}")
    if fmt == "json":
        payload = out.get("payload") if isinstance(out.get("payload"), dict) else {}
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output_path.write_text(str(out.get("content") or ""), encoding="utf-8")
    print(f"Wrote {fmt} export to {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        argparse.ArgumentParser: Configured argument parser.
    """
    p = argparse.ArgumentParser(prog="ragonometrics")
    sub = p.add_subparsers(dest="cmd", required=True)

    db = sub.add_parser("db", help="Database operations")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    db_migrate = db_sub.add_parser("migrate", help="Apply Alembic migrations to Postgres")
    db_migrate.add_argument("--db-url", type=str, default=None)
    db_migrate.set_defaults(func=cmd_db_migrate)

    usage = sub.add_parser("usage", help="Show token usage rollups from Postgres")
    usage.add_argument("--db-url", type=str, default=None)
    usage.add_argument("--run-id", type=str, default=None)
    usage.add_argument("--workstream-id", type=str, default=None)
    usage.set_defaults(func=cmd_usage)

    s = sub.add_parser("index", help="Build Postgres/FAISS vector indexes from PDFs")
    s.add_argument("--papers-dir", type=str, default=None)
    s.add_argument("--index-path", type=str, default="vectors-3072.index")
    s.add_argument("--meta-db-url", type=str, default=None)
    s.add_argument("--limit", type=int, default=0)
    s.set_defaults(func=cmd_index)

    q = sub.add_parser("query", help="Ask a question against a paper")
    q.add_argument("--paper", type=str, required=True)
    q.add_argument("--question", type=str, required=True)
    q.add_argument("--model", type=str, default=None)
    q.set_defaults(func=cmd_query)

    u = sub.add_parser("ui", help="Launch the Streamlit UI")
    u.set_defaults(func=cmd_ui)

    web = sub.add_parser("web", help="Launch Flask web API + SPA")
    web.add_argument("--host", type=str, default="0.0.0.0")
    web.add_argument("--port", type=int, default=8590)
    web.add_argument("--gunicorn", action="store_true", help="Run with gunicorn instead of Flask dev server.")
    web.add_argument("--workers", type=int, default=2, help="Gunicorn worker count when --gunicorn is set.")
    web.add_argument(
        "--timeout",
        type=int,
        default=_env_int("WEB_GUNICORN_TIMEOUT", 180),
        help="Gunicorn hard timeout in seconds.",
    )
    web.add_argument(
        "--graceful-timeout",
        type=int,
        default=_env_int("WEB_GUNICORN_GRACEFUL_TIMEOUT", 30),
        help="Gunicorn graceful worker shutdown timeout in seconds.",
    )
    web.add_argument(
        "--keep-alive",
        type=int,
        default=_env_int("WEB_GUNICORN_KEEPALIVE", 5),
        help="Gunicorn keep-alive duration in seconds.",
    )
    web.set_defaults(func=cmd_web)

    b = sub.add_parser("benchmark", help="Run benchmark suite")
    b.add_argument("--papers-dir", type=str, default=None)
    b.add_argument("--out", type=str, default="bench/benchmark.csv")
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--use-openai", action="store_true")
    b.add_argument("--force-ocr", action="store_true")
    b.add_argument("--db-url", type=str, default=None)
    b.set_defaults(func=cmd_benchmark)

    bw = sub.add_parser("benchmark-web-cache", help="Benchmark concurrent access to cached structured questions on web API")
    bw.add_argument("--base-url", type=str, default=os.getenv("WEB_BENCH_BASE_URL", "http://localhost:8590"))
    bw.add_argument("--identifier", type=str, default=os.getenv("WEB_BENCH_IDENTIFIER", os.getenv("STREAMLIT_USERNAME", "")))
    bw.add_argument("--password", type=str, default=os.getenv("WEB_BENCH_PASSWORD", os.getenv("STREAMLIT_PASSWORD", "")))
    bw.add_argument("--credentials-file", type=str, default=None, help="CSV with columns: identifier,password")
    bw.add_argument("--users", type=int, default=20, help="Number of concurrent virtual users.")
    bw.add_argument("--iterations", type=int, default=5, help="Structured tab reads per user.")
    bw.add_argument("--paper-id", type=str, default=None)
    bw.add_argument("--paper-name", type=str, default=None, help="Optional paper title/name substring match.")
    bw.add_argument("--model", type=str, default=None, help="Optional model filter for cached structured answers.")
    bw.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    bw.add_argument(
        "--auth-mode",
        type=str,
        choices=["shared-session", "per-user-login"],
        default="shared-session",
        help="shared-session logs in once then fans out reads; per-user-login logs in each virtual user.",
    )
    bw.add_argument("--think-time-ms", type=int, default=0, help="Optional delay between iterations per user.")
    bw.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification.")
    bw.add_argument("--out", type=str, default="bench/web-cache-benchmark.json")
    bw.add_argument(
        "--min-cache-ratio",
        type=float,
        default=0.0,
        help="Optional gate: return non-zero when average cache ratio is below this value.",
    )
    bw.set_defaults(func=cmd_benchmark_web_cache)

    bt = sub.add_parser("benchmark-web-tabs", help="Benchmark tab-level web API endpoint reads")
    bt.add_argument("--base-url", type=str, default=os.getenv("WEB_BENCH_BASE_URL", "http://localhost:8590"))
    bt.add_argument("--identifier", type=str, default=os.getenv("WEB_BENCH_IDENTIFIER", os.getenv("STREAMLIT_USERNAME", "")))
    bt.add_argument("--password", type=str, default=os.getenv("WEB_BENCH_PASSWORD", os.getenv("STREAMLIT_PASSWORD", "")))
    bt.add_argument("--credentials-file", type=str, default=None, help="CSV with columns: identifier,password")
    bt.add_argument("--users", type=int, default=20)
    bt.add_argument("--iterations", type=int, default=3)
    bt.add_argument("--paper-id", type=str, default=None)
    bt.add_argument("--paper-name", type=str, default=None)
    bt.add_argument("--model", type=str, default=None)
    bt.add_argument("--timeout", type=float, default=30.0)
    bt.add_argument(
        "--auth-mode",
        type=str,
        choices=["shared-session", "per-user-login"],
        default="shared-session",
    )
    bt.add_argument("--think-time-ms", type=int, default=0)
    bt.add_argument("--insecure", action="store_true")
    bt.add_argument("--network-max-references", type=int, default=10)
    bt.add_argument("--network-max-citing", type=int, default=10)
    bt.add_argument("--usage-recent-limit", type=int, default=200)
    bt.add_argument("--usage-session-only", action="store_true")
    bt.add_argument("--no-chat", action="store_true")
    bt.add_argument("--no-structured", action="store_true")
    bt.add_argument("--no-openalex", action="store_true")
    bt.add_argument("--no-network", action="store_true")
    bt.add_argument("--no-usage", action="store_true")
    bt.add_argument("--out", type=str, default="bench/web-tabs-benchmark.json")
    bt.set_defaults(func=cmd_benchmark_web_tabs)

    bc = sub.add_parser("benchmark-web-chat", help="Benchmark web chat turns and cache-hit behavior")
    bc.add_argument("--base-url", type=str, default=os.getenv("WEB_BENCH_BASE_URL", "http://localhost:8590"))
    bc.add_argument("--identifier", type=str, default=os.getenv("WEB_BENCH_IDENTIFIER", os.getenv("STREAMLIT_USERNAME", "")))
    bc.add_argument("--password", type=str, default=os.getenv("WEB_BENCH_PASSWORD", os.getenv("STREAMLIT_PASSWORD", "")))
    bc.add_argument("--credentials-file", type=str, default=None, help="CSV with columns: identifier,password")
    bc.add_argument("--users", type=int, default=10)
    bc.add_argument("--iterations", type=int, default=3)
    bc.add_argument("--paper-id", type=str, default=None)
    bc.add_argument("--paper-name", type=str, default=None)
    bc.add_argument("--question", type=str, default="What is the main research question of this paper?")
    bc.add_argument("--model", type=str, default=None)
    bc.add_argument("--top-k", type=int, default=None)
    bc.add_argument("--variation-mode", action="store_true")
    bc.add_argument("--timeout", type=float, default=60.0)
    bc.add_argument(
        "--auth-mode",
        type=str,
        choices=["shared-session", "per-user-login"],
        default="shared-session",
    )
    bc.add_argument("--think-time-ms", type=int, default=0)
    bc.add_argument("--insecure", action="store_true")
    bc.add_argument("--out", type=str, default="bench/web-chat-benchmark.json")
    bc.add_argument(
        "--min-cache-hit-ratio",
        type=float,
        default=0.0,
        help="Optional gate: return non-zero when chat cache-hit ratio is below this value.",
    )
    bc.set_defaults(func=cmd_benchmark_web_chat)

    pm = sub.add_parser("store-metadata", help="Store paper metadata (authors/DOIs/etc.) in Postgres")
    pm.add_argument("--papers-dir", type=str, default=None)
    pm.add_argument("--meta-db-url", type=str, default=None)
    pm.add_argument("--limit", type=int, default=0)
    pm.set_defaults(func=cmd_store_metadata)

    wr = sub.add_parser("store-workflow-reports", help="Store workflow report JSON files in Postgres JSONB")
    wr.add_argument("--reports-dir", type=str, default="reports")
    wr.add_argument("--meta-db-url", type=str, default=None)
    wr.add_argument("--recursive", action="store_true")
    wr.add_argument("--limit", type=int, default=0)
    wr.set_defaults(func=cmd_store_workflow_reports)

    oa = sub.add_parser(
        "store-openalex-metadata",
        help="Find papers on OpenAlex by title+authors and store matches in Postgres",
    )
    oa.add_argument("--papers-dir", type=str, default=None)
    oa.add_argument("--meta-db-url", type=str, default=None)
    oa.add_argument("--limit", type=int, default=0)
    oa.add_argument("--refresh", action="store_true", help="Re-query and overwrite already matched rows.")
    oa.set_defaults(func=cmd_store_openalex_metadata)

    w = sub.add_parser("workflow", help="Run or enqueue a multi-step workflow")
    w.add_argument("--papers", type=str, default=None, help="PDF file or directory")
    w.add_argument("--papers-dir", dest="papers", type=str, help=argparse.SUPPRESS)
    w.add_argument("--config-path", type=str, default=None)
    w.add_argument("--meta-db-url", type=str, default=None)
    w.add_argument(
        "--queue-db-url",
        type=str,
        default=None,
        help="Postgres URL for async queue (defaults to --meta-db-url or DATABASE_URL).",
    )
    w.add_argument("--async", dest="async_mode", action="store_true", help="Enqueue workflow via Postgres async queue")
    w.add_argument("--agentic", action="store_true", help="Enable agentic LLM sub-question workflow")
    w.add_argument("--question", type=str, default=None, help="Main question for agentic workflow")
    w.add_argument("--agentic-model", type=str, default=None, help="Model override for agentic workflow")
    w.add_argument("--agentic-citations", action="store_true", help="Use citations API to enrich agentic context")
    w.add_argument("--workstream-id", type=str, default=None, help="Logical workstream identifier for run grouping.")
    w.add_argument("--arm", type=str, default=None, help="Variant arm label (for example: baseline, gpt-5-nano).")
    w.add_argument("--parent-run-id", type=str, default=None, help="Optional parent/baseline run id.")
    w.add_argument("--trigger-source", type=str, default=None, help="Run trigger source label (cli, queue, api, etc.).")
    w.add_argument(
        "--report-question-set",
        type=str,
        default=None,
        help="Report questions: structured|agentic|both|none (overrides WORKFLOW_REPORT_QUESTIONS_SET).",
    )
    w.set_defaults(func=cmd_workflow)

    cmp_parser = sub.add_parser("compare", help="Multi-paper comparison workflows")
    cmp_sub = cmp_parser.add_subparsers(dest="compare_cmd", required=True)

    cmp_suggest = cmp_sub.add_parser("suggest", help="Suggest similar papers by topic/concept overlap")
    cmp_suggest.add_argument("--paper-id", type=str, required=True)
    cmp_suggest.add_argument("--limit", type=int, default=20)
    cmp_suggest.add_argument("--project-id", type=str, default=None)
    cmp_suggest.set_defaults(func=cmd_compare_suggest)

    cmp_create = cmp_sub.add_parser("create", help="Create one cache-first comparison run")
    cmp_create.add_argument("--seed-paper-id", type=str, default=None)
    cmp_create.add_argument("--paper-id", action="append", required=True, help="Repeat --paper-id for multiple papers")
    cmp_create.add_argument("--question", action="append", required=True, help="Repeat --question for multiple custom questions")
    cmp_create.add_argument("--model", type=str, default=None)
    cmp_create.add_argument("--name", type=str, default=None)
    cmp_create.add_argument("--username", type=str, default="cli")
    cmp_create.add_argument("--project-id", type=str, default=None)
    cmp_create.add_argument("--persona-id", type=str, default=None)
    cmp_create.set_defaults(func=cmd_compare_create)

    cmp_show = cmp_sub.add_parser("show", help="Show a saved comparison run")
    cmp_show.add_argument("--comparison-id", type=str, required=True)
    cmp_show.add_argument("--project-id", type=str, default=None)
    cmp_show.set_defaults(func=cmd_compare_show)

    cmp_fill = cmp_sub.add_parser("fill-missing", help="Generate answers for missing comparison cells")
    cmp_fill.add_argument("--comparison-id", type=str, required=True)
    cmp_fill.add_argument("--paper-id", action="append", default=[], help="Optional paper-id filter, repeatable")
    cmp_fill.add_argument("--question-id", action="append", default=[], help="Optional question-id filter, repeatable")
    cmp_fill.add_argument("--project-id", type=str, default=None)
    cmp_fill.add_argument("--persona-id", type=str, default=None)
    cmp_fill.set_defaults(func=cmd_compare_fill_missing)

    cmp_export = cmp_sub.add_parser("export", help="Export a saved comparison run")
    cmp_export.add_argument("--comparison-id", type=str, required=True)
    cmp_export.add_argument("--format", choices=["json", "csv"], default="json")
    cmp_export.add_argument("--out", type=str, default=None)
    cmp_export.add_argument("--project-id", type=str, default=None)
    cmp_export.set_defaults(func=cmd_compare_export)

    return p


def main() -> int:
    """CLI entrypoint for ragonometrics.

    Returns:
        int: Computed integer result.
    """
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

