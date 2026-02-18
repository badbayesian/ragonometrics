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
  display_abstract?: string;
  abstract?: string;
  openalex_url?: string;
  title_source?: string;
  doi?: string;
  publication_year?: number | null;
  venue?: string;
  landing_url?: string;
};
export type TabKey = "chat" | "structured" | "openalex" | "network" | "usage" | "viewer" | "workflow";

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
