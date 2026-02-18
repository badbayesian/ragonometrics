"""Chat service for paper-scoped RAG answers."""

from __future__ import annotations

import json
import os
import queue
import re
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

from ragonometrics.core.main import top_k_context
from ragonometrics.core.prompts import RESEARCHER_QA_PROMPT
from ragonometrics.integrations.citec import format_citec_context
from ragonometrics.integrations.openalex import format_openalex_context
from ragonometrics.llm.runtime import build_llm_runtime
from ragonometrics.pipeline import call_llm
from ragonometrics.pipeline.query_cache import (
    DEFAULT_CACHE_PATH,
    get_cached_answer,
    get_cached_answer_by_normalized_query,
    get_cached_answer_hybrid,
    make_cache_key,
    profile_hash,
    set_cached_answer,
    set_cached_answer_hybrid,
)
from ragonometrics.pipeline.token_usage import record_usage
from ragonometrics.services.papers import PaperRef, load_prepared

_INVALID_CHAT_ANSWER_PATTERNS = (
    re.compile(r"^\s*ResponseTextConfig\(", re.IGNORECASE),
    re.compile(r"^\s*ResponseFormatText\(", re.IGNORECASE),
)


def _is_valid_chat_answer(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return not any(pattern.search(text) for pattern in _INVALID_CHAT_ANSWER_PATTERNS)


def parse_context_chunks(context: str) -> List[dict]:
    """Parse retrieval context blocks into structured citation chunks."""
    chunks: List[dict] = []
    for block in str(context or "").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        meta = None
        text = block
        page: Optional[int] = None
        start_word: Optional[int] = None
        end_word: Optional[int] = None
        section: Optional[str] = None
        if block.startswith("(page "):
            parts = block.split("\n", 1)
            meta = parts[0].strip()
            text = parts[1].strip() if len(parts) > 1 else ""
            m = re.search(r"\(page\s+(\d+)\b", meta)
            if m:
                try:
                    page = int(m.group(1))
                except ValueError:
                    page = None
            m_words = re.search(r"\bwords\s+(\d+)-(\d+)\b", meta)
            if m_words:
                try:
                    start_word = int(m_words.group(1))
                    end_word = int(m_words.group(2))
                except ValueError:
                    start_word = None
                    end_word = None
            m_section = re.search(r"\bsection\s+(.+?)\)$", meta)
            if m_section:
                section = str(m_section.group(1) or "").strip() or None
        chunks.append(
            {
                "meta": meta,
                "text": text,
                "page": page,
                "start_word": start_word,
                "end_word": end_word,
                "section": section,
            }
        )
    return chunks


def build_chat_history_context(history: List[dict], *, paper_path: str, max_turns: int = 6, max_answer_chars: int = 800) -> str:
    """Build compact history context for conversation continuity."""
    if not history:
        return ""
    rows: List[str] = []
    for item in list(history)[-max_turns:]:
        if not isinstance(item, dict):
            continue
        if str(item.get("paper_path") or "") != paper_path:
            continue
        query = str(item.get("query") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not query or not answer:
            continue
        if len(answer) > max_answer_chars:
            answer = answer[: max_answer_chars - 3] + "..."
        rows.append(f"User: {query}\nAssistant: {answer}")
    return "\n\n".join(rows)


def suggested_paper_questions(*, paper_title: Optional[str] = None) -> List[str]:
    """Return deterministic starter prompts for one selected paper."""
    questions = [
        "What is the main research question of this paper?",
        "What identification strategy does the paper use?",
        "What dataset and sample period are used?",
        "What are the key quantitative findings?",
        "What are the main limitations and caveats?",
        "What policy implications follow from the results?",
    ]
    title = str(paper_title or "").strip()
    if title:
        questions[0] = f'What is the main research question in "{title}"?'
    return questions


def _resolve_chat_endpoint(runtime_or_client: Any, usage_context: str) -> Any:
    if not hasattr(runtime_or_client, "chat"):
        return runtime_or_client
    usage = (usage_context or "").strip().lower()
    if usage in {"query_expansion", "query_expand"} and hasattr(runtime_or_client, "query_expand_chat"):
        return runtime_or_client.query_expand_chat
    if usage in {"rerank", "agent_report_rerank"} and hasattr(runtime_or_client, "rerank_chat"):
        return runtime_or_client.rerank_chat
    if usage in {"metadata_title", "openalex_title"} and hasattr(runtime_or_client, "metadata_title_chat"):
        return runtime_or_client.metadata_title_chat
    return runtime_or_client.chat


def stream_llm_answer(
    *,
    client: Any,
    model: str,
    instructions: str,
    user_input: str,
    temperature: Optional[float],
    usage_context: str,
    session_id: Optional[str],
    request_id: Optional[str],
    project_id: Optional[str],
    persona_id: Optional[str],
    on_delta: Callable[[str], None],
) -> str:
    """Provider-routed streaming answer with non-stream fallback."""
    endpoint = _resolve_chat_endpoint(client, usage_context)
    if hasattr(endpoint, "stream"):
        response = endpoint.stream(
            instructions=instructions,
            user_input=user_input,
            temperature=temperature,
            max_output_tokens=None,
            model=model,
            metadata={"capability": "stream_chat"},
            on_delta=on_delta,
        )
        try:
            usage_meta = {
                "provider": getattr(response, "provider", None),
                "capability": getattr(response, "capability", "stream_chat"),
                "fallback_from": getattr(response, "fallback_from", None),
            }
            record_usage(
                model=model,
                operation=usage_context,
                step=usage_context,
                input_tokens=int(getattr(response, "input_tokens", 0) or 0),
                output_tokens=int(getattr(response, "output_tokens", 0) or 0),
                total_tokens=int(getattr(response, "total_tokens", 0) or 0),
                project_id=project_id,
                persona_id=persona_id,
                session_id=session_id,
                request_id=request_id,
                provider_request_id=getattr(response, "provider_request_id", None),
                meta=usage_meta,
            )
        except Exception:
            pass
        return str(getattr(response, "text", "") or "").strip()
    text = call_llm(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        max_output_tokens=None,
        temperature=temperature,
        usage_context=usage_context,
        meta={"project_id": project_id, "persona_id": persona_id},
        session_id=session_id,
        request_id=request_id,
    )
    # Emit coarse deltas so clients can render stream-like updates.
    acc = ""
    for token in text.split(" "):
        if not token:
            continue
        acc = (acc + " " + token).strip()
        on_delta(acc)
    return text


def _chat_user_input(
    *,
    query: str,
    context: str,
    openalex_context: str,
    citec_context: str,
    history_context: str,
) -> str:
    parts: List[str] = []
    if history_context:
        parts.append(
            "Prior conversation (for continuity; prefer current question + evidence context if conflicts):\n"
            f"{history_context}"
        )
    parts.append(f"Context:\n{context}\n\nQuestion: {query}")
    user_input = "\n\n".join(parts)
    prefix_parts = [ctx for ctx in (openalex_context, citec_context) if ctx]
    if prefix_parts:
        user_input = f"{chr(10).join(prefix_parts)}\n\n{user_input}"
    return user_input


def chat_turn(
    *,
    paper_ref: PaperRef,
    query: str,
    model: Optional[str] = None,
    top_k: Optional[int] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    history: Optional[List[dict]] = None,
    variation_mode: bool = False,
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    allow_cross_project_answer_reuse: bool = True,
    allow_custom_question_sharing: bool = False,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute one paper-scoped chat turn and return payload."""
    paper, chunks, chunk_embeddings, settings = load_prepared(paper_ref)
    if not chunks:
        return {"answer": "", "context": "", "citations": [], "cache_hit": False, "request_id": request_id or uuid4().hex}
    runtime = build_llm_runtime(settings)
    selected_model = str(model or settings.chat_model)
    retrieved_top_k = int(top_k or settings.top_k)
    retrieval_settings = settings if retrieved_top_k == settings.top_k else settings.__class__(**{**settings.__dict__, "top_k": retrieved_top_k})
    req_id = str(request_id or uuid4().hex)
    history_context = build_chat_history_context(
        list(history or []),
        paper_path=paper_ref.path,
        max_turns=max(1, int(os.getenv("CHAT_HISTORY_TURNS", "6"))),
    )
    context, retrieval_stats = top_k_context(
        chunks,
        chunk_embeddings,
        query=str(query or ""),
        client=runtime,
        settings=retrieval_settings,
        paper_path=paper.path,
        session_id=session_id,
        request_id=req_id,
        return_stats=True,
    )
    openalex_context = format_openalex_context(paper.openalex)
    citec_context = format_citec_context(paper.citec)
    user_input = _chat_user_input(
        query=str(query or ""),
        context=context,
        openalex_context=openalex_context,
        citec_context=citec_context,
        history_context=history_context,
    )
    temperature: Optional[float] = None
    cache_allowed = not variation_mode
    if variation_mode:
        try:
            temperature = float(os.getenv("RAG_VARIATION_TEMPERATURE", "0.7"))
        except Exception:
            temperature = 0.7
    cache_context = context if not history_context else f"Conversation History:\n{history_context}\n\n{context}"
    cache_key = make_cache_key(str(query or ""), paper_ref.path, selected_model, cache_context)
    prompt_profile = profile_hash(RESEARCHER_QA_PROMPT)
    retrieval_profile = profile_hash(f"top_k={int(retrieval_settings.top_k)}")
    persona_profile = profile_hash(str(persona_id or "default"))
    if str(project_id or "").strip():
        hybrid = get_cached_answer_hybrid(
            DEFAULT_CACHE_PATH,
            cache_key=cache_key,
            query=str(query or ""),
            paper_path=paper_ref.path,
            model=selected_model,
            project_id=project_id,
            prompt_profile_hash=prompt_profile,
            retrieval_profile_hash=retrieval_profile,
            persona_profile_hash=persona_profile,
            allow_cross_project_answer_reuse=bool(allow_cross_project_answer_reuse),
            variation_mode=bool(variation_mode),
            has_history=bool(history_context),
            validate_answer=_is_valid_chat_answer,
        )
        answer = str(hybrid.get("answer") or "").strip()
        cache_hit = bool(hybrid.get("cache_hit"))
        cache_hit_layer = str(hybrid.get("cache_hit_layer") or "none")
        cache_scope = str(hybrid.get("cache_scope") or "fresh")
        cache_miss_reason = str(hybrid.get("cache_miss_reason") or "")
    else:
        cache_scope = "shared"
        cache_hit_layer = "none"
        cache_miss_reason = ""
        cached = get_cached_answer(DEFAULT_CACHE_PATH, cache_key) if cache_allowed else None
        if cached is not None and _is_valid_chat_answer(cached):
            answer = str(cached).strip()
            cache_hit = True
            cache_hit_layer = "strict"
        else:
            if cache_allowed:
                fallback_cached = get_cached_answer_by_normalized_query(
                    DEFAULT_CACHE_PATH,
                    query=str(query or ""),
                    paper_path=paper_ref.path,
                    model=selected_model,
                )
                if fallback_cached is not None and _is_valid_chat_answer(fallback_cached):
                    answer = str(fallback_cached).strip()
                    cache_hit = True
                    cache_hit_layer = "fallback"
                else:
                    answer = ""
                    cache_hit = False
                    if cached is not None and not _is_valid_chat_answer(cached):
                        cache_miss_reason = "invalid_strict_cached_answer"
                    elif fallback_cached is not None and not _is_valid_chat_answer(fallback_cached):
                        cache_miss_reason = "invalid_normalized_cached_answer"
                    else:
                        cache_miss_reason = "strict_and_normalized_miss"
            else:
                answer = ""
                cache_hit = False
                cache_miss_reason = "variation_mode_bypass"
    if not cache_hit:
        answer = call_llm(
            runtime,
            model=selected_model,
            instructions=RESEARCHER_QA_PROMPT,
            user_input=user_input,
            max_output_tokens=None,
            temperature=temperature,
            usage_context="answer",
            meta={"project_id": project_id, "persona_id": persona_id},
            session_id=session_id,
            request_id=req_id,
        ).strip()
        cache_hit = False
        cache_scope = "fresh"
        if cache_allowed and _is_valid_chat_answer(answer):
            if str(project_id or "").strip():
                set_cached_answer_hybrid(
                    DEFAULT_CACHE_PATH,
                    cache_key=cache_key,
                    query=str(query or ""),
                    paper_path=paper_ref.path,
                    model=selected_model,
                    context=cache_context,
                    answer=answer,
                    project_id=project_id,
                    user_id=user_id,
                    source_project_id=project_id,
                    prompt_profile_hash=prompt_profile,
                    retrieval_profile_hash=retrieval_profile,
                    persona_profile_hash=persona_profile,
                    allow_custom_question_sharing=bool(allow_custom_question_sharing),
                )
            else:
                set_cached_answer(
                    DEFAULT_CACHE_PATH,
                    cache_key=cache_key,
                    query=str(query or ""),
                    paper_path=paper_ref.path,
                    model=selected_model,
                    context=cache_context,
                    answer=answer,
                )
    return {
        "answer": answer,
        "context": context,
        "citations": parse_context_chunks(context),
        "cache_hit": cache_hit,
        "cache_hit_layer": cache_hit_layer,
        "cache_scope": cache_scope,
        "cache_miss_reason": cache_miss_reason,
        "request_id": req_id,
        "model": selected_model,
        "retrieval_stats": retrieval_stats if isinstance(retrieval_stats, dict) else {},
        "history_used": bool(history_context),
    }


def stream_chat_turn(
    *,
    paper_ref: PaperRef,
    query: str,
    model: Optional[str] = None,
    top_k: Optional[int] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    history: Optional[List[dict]] = None,
    variation_mode: bool = False,
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    allow_cross_project_answer_reuse: bool = True,
    allow_custom_question_sharing: bool = False,
    user_id: Optional[int] = None,
) -> Iterable[str]:
    """Yield NDJSON records for one streamed chat turn."""
    paper, chunks, chunk_embeddings, settings = load_prepared(paper_ref)
    runtime = build_llm_runtime(settings)
    selected_model = str(model or settings.chat_model)
    retrieved_top_k = int(top_k or settings.top_k)
    retrieval_settings = settings if retrieved_top_k == settings.top_k else settings.__class__(**{**settings.__dict__, "top_k": retrieved_top_k})
    req_id = str(request_id or uuid4().hex)
    history_context = build_chat_history_context(
        list(history or []),
        paper_path=paper_ref.path,
        max_turns=max(1, int(os.getenv("CHAT_HISTORY_TURNS", "6"))),
    )
    context, retrieval_stats = top_k_context(
        chunks,
        chunk_embeddings,
        query=str(query or ""),
        client=runtime,
        settings=retrieval_settings,
        paper_path=paper.path,
        session_id=session_id,
        request_id=req_id,
        return_stats=True,
    )
    openalex_context = format_openalex_context(paper.openalex)
    citec_context = format_citec_context(paper.citec)
    user_input = _chat_user_input(
        query=str(query or ""),
        context=context,
        openalex_context=openalex_context,
        citec_context=citec_context,
        history_context=history_context,
    )
    temperature: Optional[float] = None
    cache_allowed = not variation_mode
    if variation_mode:
        try:
            temperature = float(os.getenv("RAG_VARIATION_TEMPERATURE", "0.7"))
        except Exception:
            temperature = 0.7
    cache_context = context if not history_context else f"Conversation History:\n{history_context}\n\n{context}"
    cache_key = make_cache_key(str(query or ""), paper_ref.path, selected_model, cache_context)
    prompt_profile = profile_hash(RESEARCHER_QA_PROMPT)
    retrieval_profile = profile_hash(f"top_k={int(retrieval_settings.top_k)}")
    persona_profile = profile_hash(str(persona_id or "default"))
    if str(project_id or "").strip():
        hybrid = get_cached_answer_hybrid(
            DEFAULT_CACHE_PATH,
            cache_key=cache_key,
            query=str(query or ""),
            paper_path=paper_ref.path,
            model=selected_model,
            project_id=project_id,
            prompt_profile_hash=prompt_profile,
            retrieval_profile_hash=retrieval_profile,
            persona_profile_hash=persona_profile,
            allow_cross_project_answer_reuse=bool(allow_cross_project_answer_reuse),
            variation_mode=bool(variation_mode),
            has_history=bool(history_context),
            validate_answer=_is_valid_chat_answer,
        )
    else:
        hybrid = {"cache_hit": False, "cache_scope": "shared", "cache_hit_layer": "none", "cache_miss_reason": ""}
        cached = get_cached_answer(DEFAULT_CACHE_PATH, cache_key) if cache_allowed else None
        if cached is not None and _is_valid_chat_answer(cached):
            hybrid = {
                "cache_hit": True,
                "answer": str(cached).strip(),
                "cache_scope": "shared",
                "cache_hit_layer": "strict",
                "cache_miss_reason": "",
            }
        elif cache_allowed:
            fallback_cached = get_cached_answer_by_normalized_query(
                DEFAULT_CACHE_PATH,
                query=str(query or ""),
                paper_path=paper_ref.path,
                model=selected_model,
            )
            if fallback_cached is not None and _is_valid_chat_answer(fallback_cached):
                hybrid = {
                    "cache_hit": True,
                    "answer": str(fallback_cached).strip(),
                    "cache_scope": "shared",
                    "cache_hit_layer": "fallback",
                    "cache_miss_reason": "",
                }
            else:
                hybrid["cache_miss_reason"] = "strict_and_normalized_miss"
        else:
            hybrid["cache_miss_reason"] = "variation_mode_bypass"

    if bool(hybrid.get("cache_hit")):
        answer = str(hybrid.get("answer") or "").strip()
        yield json.dumps({"event": "delta", "text": answer}, ensure_ascii=False) + "\n"
        payload = {
            "event": "done",
            "answer": answer,
            "cache_hit": True,
            "cache_scope": str(hybrid.get("cache_scope") or "project"),
            "cache_hit_layer": str(hybrid.get("cache_hit_layer") or "strict"),
            "cache_miss_reason": str(hybrid.get("cache_miss_reason") or ""),
            "request_id": req_id,
            "model": selected_model,
            "citations": parse_context_chunks(context),
            "retrieval_stats": retrieval_stats if isinstance(retrieval_stats, dict) else {},
        }
        yield json.dumps(payload, ensure_ascii=False) + "\n"
        return

    latest_text = ""
    stream_done = object()
    delta_queue: queue.Queue[object] = queue.Queue()
    stream_result: Dict[str, Any] = {"answer": "", "error": None}

    def _on_delta(text: str) -> None:
        delta_queue.put(str(text or ""))

    def _run_stream() -> None:
        try:
            stream_result["answer"] = stream_llm_answer(
                client=runtime,
                model=selected_model,
                instructions=RESEARCHER_QA_PROMPT,
                user_input=user_input,
                temperature=temperature,
                usage_context="answer",
                session_id=session_id,
                request_id=req_id,
                project_id=project_id,
                persona_id=persona_id,
                on_delta=_on_delta,
            )
        except Exception as exc:
            stream_result["error"] = exc
        finally:
            delta_queue.put(stream_done)

    worker = threading.Thread(target=_run_stream, daemon=True)
    worker.start()

    while True:
        item = delta_queue.get()
        if item is stream_done:
            break
        latest_text = str(item or latest_text)
        if latest_text:
            yield json.dumps({"event": "delta", "text": latest_text}, ensure_ascii=False) + "\n"

    worker.join(timeout=0.0)
    stream_error = stream_result.get("error")
    if stream_error is not None:
        payload = {
            "event": "error",
            "code": "chat_failed",
            "message": str(stream_error),
            "request_id": req_id,
            "model": selected_model,
        }
        yield json.dumps(payload, ensure_ascii=False) + "\n"
        return

    answer = str(stream_result.get("answer") or latest_text or "").strip()
    if not _is_valid_chat_answer(answer):
        payload = {
            "event": "error",
            "code": "chat_invalid_output",
            "message": "Model returned an invalid text payload. Please retry.",
            "request_id": req_id,
            "model": selected_model,
        }
        yield json.dumps(payload, ensure_ascii=False) + "\n"
        return
    if cache_allowed and _is_valid_chat_answer(answer):
        if str(project_id or "").strip():
            set_cached_answer_hybrid(
                DEFAULT_CACHE_PATH,
                cache_key=cache_key,
                query=str(query or ""),
                paper_path=paper_ref.path,
                model=selected_model,
                context=cache_context,
                answer=answer,
                project_id=project_id,
                user_id=user_id,
                source_project_id=project_id,
                prompt_profile_hash=prompt_profile,
                retrieval_profile_hash=retrieval_profile,
                persona_profile_hash=persona_profile,
                allow_custom_question_sharing=bool(allow_custom_question_sharing),
            )
        else:
            set_cached_answer(
                DEFAULT_CACHE_PATH,
                cache_key=cache_key,
                query=str(query or ""),
                paper_path=paper_ref.path,
                model=selected_model,
                context=cache_context,
                answer=answer,
            )
    payload = {
        "event": "done",
        "answer": answer,
        "cache_hit": False,
        "cache_scope": "fresh",
        "cache_hit_layer": "none",
        "cache_miss_reason": str(hybrid.get("cache_miss_reason") or ("variation_mode_bypass" if variation_mode else "guardrail_miss")),
        "request_id": req_id,
        "model": selected_model,
        "citations": parse_context_chunks(context),
        "retrieval_stats": retrieval_stats if isinstance(retrieval_stats, dict) else {},
    }
    yield json.dumps(payload, ensure_ascii=False) + "\n"
