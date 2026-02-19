export type ApiEnvelope<T> = {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string };
  request_id?: string;
};

export type PaperRow = {
  paper_id: string;
  name: string;
  path: string;
  display_title?: string;
  title?: string;
  display_authors?: string;
  authors?: string[];
  author_items?: Array<{ name: string; id?: string; openalex_url?: string }>;
  display_abstract?: string;
  abstract?: string;
  openalex_url?: string;
  title_source?: string;
  doi?: string;
  publication_year?: number | null;
  venue?: string;
  landing_url?: string;
};

export type ProjectRow = {
  project_id: string;
  name: string;
  slug?: string;
  role?: string;
  is_active?: boolean;
  allow_cross_project_answer_reuse?: boolean;
  allow_custom_question_sharing?: boolean;
  default_persona_id?: string;
};

export type PersonaRow = {
  persona_id: string;
  slug?: string;
  name: string;
  is_default?: boolean;
  is_active?: boolean;
  default_model?: string;
};

export type ProjectContextRow = {
  project_id: string;
  project_name: string;
  role?: string;
  persona_id: string;
  persona_name: string;
  allow_cross_project_answer_reuse?: boolean;
  allow_custom_question_sharing?: boolean;
};

export type TabKey = "chat" | "structured" | "openalex" | "network" | "usage" | "viewer" | "workflow" | "cache" | "compare";

export type ChatHistoryItem = {
  query: string;
  answer: string;
  paper_path: string;
  model?: string;
  citations?: CitationChunk[];
  retrieval_stats?: Record<string, unknown>;
  cache_hit?: boolean | null;
  cache_hit_layer?: string;
  cache_miss_reason?: string;
  variation_mode?: boolean;
  created_at?: string;
};

export type CitationChunk = {
  page?: number | null;
  start_word?: number | null;
  end_word?: number | null;
  section?: string | null;
  text?: string;
};

export type ProvenanceWarning = {
  code: string;
  message: string;
};

export type ProvenanceScore = {
  paper_id: string;
  paper_path?: string;
  question: string;
  score: number;
  status: "high" | "medium" | "low";
  warnings: ProvenanceWarning[];
  metrics?: Record<string, unknown>;
};

export type PaperNote = {
  id: number;
  paper_id: string;
  page_number?: number | null;
  highlight_text?: string;
  highlight_terms?: string[];
  note_text: string;
  color?: string;
  created_at?: string;
  updated_at?: string;
};

export type ChatSuggestionPayload = {
  paper_id: string;
  paper_title_hint: string;
  questions: string[];
};

export type StructuredQuestion = { id: string; category: string; question: string };

export type StructuredExportRow = {
  id: string;
  category: string;
  question: string;
  answer: string;
  cached: boolean;
  source: string;
  model: string;
  cached_at: string;
  run_id?: string;
  question_id?: string;
  has_answer?: boolean;
  structured_fields?: Record<string, unknown>;
  workflow_record?: Record<string, unknown>;
};

export type StructuredExportBundle = {
  export_type: string;
  export_format: "compact" | "full";
  exported_at: string;
  paper: { path: string; title: string; author: string };
  model_scope: { selected_model: string; cache_scope: string };
  summary: { total_questions: number; cached_questions: number; uncached_questions: number };
  questions: StructuredExportRow[];
  full_summary?: Record<string, unknown>;
};

export type WorkflowRunRow = {
  run_id: string;
  status: string;
  papers_dir: string;
  workstream_id: string;
  arm: string;
  trigger_source: string;
  question: string;
  report_question_set: string;
  report_path: string;
  started_at: string;
  finished_at: string;
  created_at: string;
  updated_at: string;
  matched_by?: string;
};

export type WorkflowStepRow = {
  step: string;
  status: string;
  started_at: string;
  finished_at: string;
  created_at: string;
  updated_at: string;
  output?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  idempotency_key?: string;
  input_hash?: string;
  reuse_source_run_id?: string;
  reuse_source_record_key?: string;
};

export type WorkflowInternalRow = {
  internal_step: string;
  label: string;
  status: string;
  detail: string;
  summary?: Record<string, unknown>;
  usage?: Record<string, unknown>;
  sample?: unknown[];
};

export type ChatCacheInspectPayload = {
  paper_id: string;
  paper_path: string;
  question: string;
  query_normalized: string;
  model: string;
  top_k: number;
  cache_key: string;
  cache_context_hash: string;
  selected_layer: string;
  cache_miss_reason: string;
  strict_hit: boolean;
  fallback_hit: boolean;
  strict_row?: Record<string, unknown>;
  fallback_row?: Record<string, unknown>;
  retrieval_stats?: Record<string, unknown>;
  context_preview?: string;
};

export type StructuredCacheInspectRow = {
  id: string;
  category: string;
  question: string;
  cached: boolean;
  run_id?: string;
  question_id?: string;
  source?: string;
  model?: string;
  cached_at?: string;
  answer_preview?: string;
  record_status?: string;
  record_step?: string;
  record_key?: string;
};

export type StructuredCacheInspectPayload = {
  paper_id: string;
  paper_path: string;
  model: string;
  total_questions: number;
  cached_questions: number;
  missing_questions: number;
  coverage_ratio: number;
  missing_question_ids: string[];
  rows: StructuredCacheInspectRow[];
};

export type SimilarPaperSuggestion = {
  paper_id: string;
  name: string;
  path: string;
  title: string;
  authors: string;
  openalex_url: string;
  score: number;
  score_breakdown?: {
    topic_similarity: number;
    keyword_overlap: number;
  };
  overlap_topics?: string[];
  overlap_concepts?: string[];
};

export type CompareQuestion = {
  id: string;
  text: string;
  normalized: string;
};

export type CompareCell = {
  paper_id: string;
  paper_path: string;
  question_id: string;
  question_text: string;
  question_normalized: string;
  model: string;
  cell_status: "cached" | "missing" | "generated" | "failed";
  answer: string;
  answer_source?: string;
  cache_hit_layer?: string;
  cache_key?: string;
  context_hash?: string;
  structured_fields?: Record<string, unknown>;
  error_text?: string;
};

export type CompareMatrixRow = {
  question_id: string;
  question_text: string;
  question_normalized: string;
  cells: CompareCell[];
};

export type CompareRun = {
  comparison_id: string;
  name: string;
  created_by_user_id?: number | null;
  created_by_username: string;
  model: string;
  compute_mode: string;
  visibility: string;
  status: string;
  paper_ids: string[];
  questions: CompareQuestion[];
  summary: {
    total_cells: number;
    cached_cells: number;
    missing_cells: number;
    generated_cells: number;
    failed_cells: number;
    ready_cells: number;
  };
  seed_paper_id?: string;
  created_at: string;
  updated_at: string;
  papers?: PaperRow[];
  cells?: CompareCell[];
  matrix?: CompareMatrixRow[];
};
