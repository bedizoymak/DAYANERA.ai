export type Role = 'owner_admin' | 'member';

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  is_active?: boolean;
}

export type AnswerMode = 'general' | 'verified_source' | 'calculation' | 'draft_extraction' | 'unverified';

export interface Conversation {
  id: string;
  title: string;
  status: 'active' | 'archived';
  owner_id: string;
  created_at: string;
  updated_at: string;
}

export interface Source {
  rank: number;
  document_id: string;
  version_id: string;
  version_number: number;
  document_title: string;
  standard_code: string | null;
  page_number: number | null;
  locator: string;
  excerpt: string;
  score: number;
  confidence_status: string;
  cited: boolean;
}

export interface AttachmentRef {
  document_id: string;
  filename: string;
  status: string;
  ingestion_status: string | null;
  mime_type: string | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  answer_mode: AnswerMode | null;
  answer_mode_label: string | null;
  status: string;
  error_code: string | null;
  show_sources: boolean;
  sources: Source[];
  attachments: AttachmentRef[];
  metadata: Record<string, unknown>;
  created_at: string | null;
  model?: string | null;
  latency_ms?: number | null;
}

export interface VersionInfo {
  id: string;
  version_number: number;
  sha256: string;
  size_bytes: number;
  mime_type: string;
  file_extension: string | null;
  state: string;
  is_active: boolean;
  ingestion_status: string;
  ingestion_error: string | null;
  page_count: number | null;
  category: string | null;
  extraction_summary: {
    counts?: Record<string, number>;
    methods?: Record<string, number>;
    warnings?: string[];
    stored_only_reason?: string | null;
  };
  metadata: Record<string, unknown>;
  created_at: string;
  ingested_at: string | null;
  superseded_at: string | null;
  source_mtime: string | null;
}

export interface DocumentInfo {
  id: string;
  title: string;
  standard_code: string | null;
  source_kind: string;
  source_relpath: string | null;
  original_filename: string;
  status: string;
  knowledge_area: string | null;
  is_verified_corpus: boolean;
  current_version: VersionInfo | null;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
  delete_reason?: string | null;
}

export interface ExtractedValue {
  id: string;
  document_id: string;
  document_title: string;
  version_id: string;
  version_number: number;
  version_active: boolean;
  page_number: number | null;
  locator: string;
  label: string;
  raw_text: string;
  context: string | null;
  value: number | null;
  unit: string | null;
  quantity_kind: string | null;
  extraction_method: string;
  status: string;
  original: Record<string, unknown>;
  confirmed_value: number | null;
  confirmed_unit: string | null;
  confirmed_at: string | null;
  review_note: string | null;
}

export interface DraftPage {
  id: string;
  document_id: string;
  document_title: string;
  version_number: number;
  page_number: number;
  locator: string;
  extraction_method: string;
  confidence_status: string;
  ocr_mean_confidence: number | null;
  text: string;
  chars: number;
}

export interface CalcInputSpec {
  key: string;
  label: string;
  kind: 'length' | 'angle' | 'dimensionless' | 'integer' | 'grade';
  required: boolean;
  min: number | null;
  max: number | null;
  description: string;
}

export interface CalcType {
  calc_type: string;
  title: string;
  description: string;
  inputs: CalcInputSpec[];
  evidence: string[];
  evidence_available: Record<string, boolean>;
  available: boolean;
}

export interface CalcOutputValue {
  key: string;
  label: string;
  value: number;
  unit: string;
  formula_id: string;
  expression: string;
  display: string;
  unrounded: number | null;
}

export interface CalcResult {
  calc_type: string;
  status: 'ok' | 'refused' | 'invalid_input' | 'error';
  engine_version: string;
  message: string;
  outputs: CalcOutputValue[];
  inputs: Array<Record<string, unknown>>;
  constants: Array<Record<string, unknown>>;
  assumptions: string[];
  evidence: Array<{
    requirement_id: string;
    description: string;
    document_id: string;
    version_id: string;
    version_number: number;
    document_title: string;
    standard_code: string | null;
    page_number: number | null;
    locator: string;
    excerpt: string;
    confidence_status: string;
  }>;
  diagnostics: Array<{ level: string; code: string; message: string }>;
  trace: string[];
}

export interface Calculation {
  id: string;
  calc_type: string;
  status: string;
  engine_version: string;
  result: CalcResult;
  inputs: Record<string, unknown>;
  llm_draft: Record<string, unknown> | null;
  comparison: {
    performed?: boolean;
    mismatch?: boolean;
    reason?: string;
    items?: Array<{ key: string; engine: number; llm: number | null; status: string; abs_diff?: number; tolerance?: number }>;
  } | null;
  mismatch: boolean;
  created_at: string;
  message_id?: string | null;
}

export interface AuditEvent {
  id: number;
  occurred_at: string;
  actor_user_id: string | null;
  actor_username: string | null;
  event_type: string;
  target_type: string | null;
  target_id: string | null;
  outcome: string;
  client_addr: string | null;
  details: Record<string, unknown>;
}

export interface MemoryItem {
  id: string;
  owner_id: string;
  visibility: string;
  kind: string;
  title: string;
  content: string;
  structured: Record<string, unknown>;
  status: string;
  supersedes_id: string | null;
  superseded_by_id: string | null;
  created_at: string;
  updated_at: string;
  source_document_id: string | null;
  source_extracted_value_id: string | null;
}

export interface NoteSummary {
  filename: string;
  title: string;
  status: string;
  author: string;
  date: string;
  indexed: boolean;
}

export interface KnowledgeArea {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  is_verified_corpus: boolean;
}

export interface Readiness {
  ready: boolean;
  database: { ok: boolean; error: string | null; migration_revision: string | null };
  ollama: { reachable: boolean; model_available: boolean; model: string | null };
  messages: string[];
}
