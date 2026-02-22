"""Pydantic request schemas for Flask API endpoints."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    identifier: str = Field(default="", max_length=256)
    username: str = Field(default="", max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    email: str = Field(default="", max_length=256)
    password: str = Field(min_length=8, max_length=1024)


class ChatHistoryItem(BaseModel):
    query: str = ""
    answer: str = ""
    paper_path: str = ""


class ChatTurnRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=4000)
    model: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=30)
    variation_mode: bool = False
    history: List[ChatHistoryItem] = Field(default_factory=list)


class MultiChatHistoryItem(BaseModel):
    query: str = ""
    answer: str = ""
    paper_ids: List[str] = Field(default_factory=list)


class MultiChatTurnRequest(BaseModel):
    paper_ids: List[str] = Field(default_factory=list)
    question: str = Field(min_length=1, max_length=4000)
    model: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=30)
    variation_mode: bool = False
    history: List[MultiChatHistoryItem] = Field(default_factory=list)
    conversation_id: str = Field(default="", max_length=128)
    seed_paper_id: str = Field(default="", max_length=64)


class ChatProvenanceScoreRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64)
    question: str = Field(default="", max_length=4000)
    answer: str = Field(min_length=1, max_length=16000)
    citations: List[dict] = Field(default_factory=list)


class StructuredGenerateRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    category: str = ""
    question: str = Field(min_length=1, max_length=1000)
    model: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=30)
    idempotency_key: str = ""


class StructuredGenerateMissingRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64)
    model: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=30)
    question_ids: List[str] = Field(default_factory=list)


class StructuredExportRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64)
    model: Optional[str] = None
    cache_scope: str = "Selected model only"
    export_format: str = "compact"
    output: str = "json"
    question_ids: List[str] = Field(default_factory=list)


class CompareCreateRequest(BaseModel):
    seed_paper_id: str = Field(default="", max_length=64)
    paper_ids: List[str] = Field(default_factory=list)
    questions: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    name: str = Field(default="", max_length=200)


class CompareFillMissingRequest(BaseModel):
    paper_ids: List[str] = Field(default_factory=list)
    question_ids: List[str] = Field(default_factory=list)


class CompareExportRequest(BaseModel):
    format: str = Field(default="json", max_length=16)


class MultiChatNetworkRequest(BaseModel):
    paper_ids: List[str] = Field(default_factory=list)
    include_topic_edges: bool = True
    include_author_edges: bool = True
    include_citation_edges: bool = True
    min_similarity: float = Field(default=0.15, ge=0.0, le=1.0)


class ForgotPasswordRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=256)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=8, max_length=1024)


class OpenAlexManualLinkRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64)
    openalex_api_url: str = Field(min_length=1, max_length=1024)


class PaperNoteCreateRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=64)
    page_number: Optional[int] = Field(default=None, ge=1, le=5000)
    highlight_text: str = ""
    highlight_terms: List[str] = Field(default_factory=list)
    note_text: str = Field(min_length=1, max_length=4000)
    color: str = ""


class PaperNoteUpdateRequest(BaseModel):
    note_text: Optional[str] = Field(default=None, max_length=4000)
    color: Optional[str] = Field(default=None, max_length=32)
    highlight_text: Optional[str] = Field(default=None, max_length=2000)
    highlight_terms: Optional[List[str]] = None


def parse_model(model_cls: Any, payload: Any) -> Any:
    """Parse one request payload into a pydantic model."""
    return model_cls.model_validate(payload or {})
