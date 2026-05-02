/**
 * Teach API — backend endpoints for the Teaching tab (Phase 2).
 *
 *   POST /api/teach/analyze   → calls the LLM analyzer over (query, expected_sql)
 *                                and returns the structured knowledge fields.
 *   POST /api/teach/save      → atomic write: session entry + LearnedPatterns
 *                                from anticipated_clarifications + sibling
 *                                KnowledgeEntry attachments.
 */
import { apiFetch } from './client'

export interface TeachClarification {
  question: string
  answer: string
}

export interface TeachAnalysis {
  title: string
  description: string
  why_this_sql: string
  key_concepts: string[]
  tags: string[]
  anticipated_clarifications: TeachClarification[]
  key_filter_values: Record<string, string[]>
}

export interface TeachSibling {
  content: string
  category: string  // 'business_rule' | 'glossary' | 'column_values' | 'manual'
}

export interface TeachSavePayload {
  user_input: string
  expected_sql: string
  tables_used?: string[]
  analysis: TeachAnalysis
  curator_notes?: string
  siblings?: TeachSibling[]
  explanation?: string
}

export interface TeachSaveResponse {
  status: string
  session_entry_id: string
  learned_pattern_ids: string[]
  sibling_entry_ids: string[]
}

/**
 * Run the backend analyzer over (user_input, expected_sql).
 * Always resolves with a TeachAnalysis (empty fields when LLM is unavailable).
 */
export async function analyzeTeach(
  user_input: string,
  expected_sql: string,
): Promise<TeachAnalysis> {
  return apiFetch<TeachAnalysis>('/teach/analyze', {
    method: 'POST',
    body: JSON.stringify({ user_input, expected_sql }),
  })
}

/**
 * Atomically persist a teaching session: session entry + Q&A patterns + siblings.
 */
export async function saveTeach(payload: TeachSavePayload): Promise<TeachSaveResponse> {
  return apiFetch<TeachSaveResponse>('/teach/save', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
