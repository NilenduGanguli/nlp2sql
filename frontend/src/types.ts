// ──────────────────────────────────────────────────────────
// Health / System
// ──────────────────────────────────────────────────────────
export interface HealthStatus {
  status: string
  graph_loaded: boolean
  graph_tables: number
  graph_columns: number
  llm_ready: boolean
  llm_enhanced: boolean
  oracle_connected: boolean
  knowledge_file_ready: boolean
}

// ──────────────────────────────────────────────────────────
// Schema
// ──────────────────────────────────────────────────────────
export interface SchemaStats {
  table_count: number
  column_count: number
  fk_count: number
  join_path_count: number
  schemas: string[]
  llm_enhanced: boolean
}

export interface TableSummary {
  fqn: string
  name: string
  schema_name: string
  row_count: number | null
  table_type: string
  comments: string | null
  importance_tier: string | null
  importance_rank: number | null
  llm_description: string | null
  column_count: number
  partitioned?: string
}

export interface ColumnDetail {
  name: string
  data_type: string
  nullable: string | null   // Oracle returns "Y" or "N"
  comments: string | null
  is_pk: boolean
  is_fk: boolean
}

export interface ForeignKeyRef {
  constraint_name: string
  fk_col: string      // column in this table
  ref_table: string   // referenced table FQN
  ref_col: string     // referenced column
}

export interface TableDetail extends TableSummary {
  columns: ColumnDetail[]
  foreign_keys: ForeignKeyRef[]
  constraints: unknown[]
}

export interface TableListResponse {
  items: TableSummary[]
  total: number
  page: number
  pages: number
  page_size: number
}

export interface SearchResult {
  fqn: string
  name: string
  schema_name: string
  label: string           // was match_type — backend sends "label"
  match_score: number     // was score
  description: string | null  // was comments
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
}

// ──────────────────────────────────────────────────────────
// User Mode
// ──────────────────────────────────────────────────────────
export type UserMode = 'curator' | 'consumer'

// ──────────────────────────────────────────────────────────
// Query / Chat
// ──────────────────────────────────────────────────────────
export interface ConversationMessage {
  role: 'user' | 'assistant'
  content: string
}

export type QueryStep =
  | 'enriching'
  | 'classifying'
  | 'extracting'
  | 'retrieving'
  | 'checking_session_memory'
  | 'generating'
  | 'validating'
  | 'optimizing'
  | 'executing'
  | 'formatting'
  | 'auto_clarifying'
  | 'presenting'

export interface QueryResult {
  type: string
  summary: string
  sql: string
  explanation: string
  columns: string[]
  rows: unknown[][]
  total_rows: number
  execution_time_ms: number
  data_source: string
  schema_context_tables: string[]
  validation_errors: string[]
}

export type ChatMessageType = 'user' | 'result' | 'error' | 'clarification' | 'sql_preview' | 'sql_candidates' | 'kyc_auto_answer'

export interface ChatMessage {
  id: string
  type: ChatMessageType
  content: string
  result?: QueryResult
  question?: string      // clarification question text
  options?: string[]     // clarification answer options
  context?: string       // agent's understanding summary (shown above the question)
  multiSelect?: boolean  // true when multiple options can be selected (AND logic)
  answered?: boolean     // true once the user has responded
  sqlPreview?: { sql: string; explanation: string; validationPassed: boolean; validationErrors: string[] }
  sqlCandidates?: Array<{ id: string; interpretation: string; sql: string; explanation: string }>
  /** True when these candidates were short-circuited from a saved query_session entry. */
  reusedFromSession?: boolean
  kycAutoAnswer?: { question: string; autoAnswer: string; source: string }
  timestamp: Date
}

export interface ChatSession {
  id: string
  title: string           // first user message, truncated
  createdAt: string       // ISO string (serializable for localStorage)
  messages: ChatMessage[]
  history: ConversationMessage[]
}

// ──────────────────────────────────────────────────────────
// SQL Execution
// ──────────────────────────────────────────────────────────
export interface ExecuteResult {
  columns: string[]
  rows: unknown[][]
  total_rows: number
  execution_time_ms: number
  error?: string
}

export interface FormatResult {
  formatted_sql: string
}

// ──────────────────────────────────────────────────────────
// Graph
// ──────────────────────────────────────────────────────────
export interface GraphNode {
  id: string
  label: string
  group: string
  name: string
  schema_name: string
  importance_rank: number | null
  row_count: number | null
  comments: string | null
}

export interface JoinColumnDetail {
  from_col: string
  to_col: string
  from_col_fqn: string
  to_col_fqn: string
  from_col_type: string | null
  to_col_type: string | null
  from_col_comments: string | null
  to_col_comments: string | null
  constraint_name: string
  on_delete_action: string
}

export interface GraphEdge {
  id: string
  from_id: string
  to_id: string
  rel_type: string
  weight: number
  source: string
  join_columns: JoinColumnDetail[]
  join_type: string | null
  cardinality: string | null
}

export interface GraphVisualization {
  nodes: GraphNode[]
  edges: GraphEdge[]
  total_tables: number
  shown_tables: number
}

export interface JoinPath {
  found: boolean
  from_table: string
  to_table: string
  join_columns: Array<{ src: string; tgt: string; constraint: string }>
  join_type: string | null
  hops: number
  source: string
  sql_snippet: string | null
}

export interface ForeignKey {
  from_table: string
  to_table: string
  from_col: string
  to_col: string
  constraint_name: string
}

// ──────────────────────────────────────────────────────────
// Query Trace (Investigate tab)
// ──────────────────────────────────────────────────────────
export interface TraceLlmCall {
  system_prompt: string
  user_prompt: string
  raw_response: string
  parsed_output: unknown
}

export interface TraceGraphOp {
  op: string
  params: Record<string, unknown>
  result_count: number
  result_sample: unknown[]
}

export interface TraceStep {
  node: string
  step_label: string
  duration_ms: number
  llm_call: TraceLlmCall | null
  graph_ops: TraceGraphOp[]
  output_summary: Record<string, unknown>
  error: string | null
}

export interface QueryTrace {
  id: string
  query: string
  timestamp: Date
  steps: TraceStep[]
}

// ──────────────────────────────────────────────────────────
// Prompts (Investigate tab)
// ──────────────────────────────────────────────────────────
export interface PromptFile {
  name: string
  content: string
}
