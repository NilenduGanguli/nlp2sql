const BASE = '/api/kyc-agent'

export interface KnowledgeEntry {
  id: string
  source: string
  content: string
  category: string
  metadata: Record<string, unknown>
}

export interface LearnedPattern {
  id: string
  question_pattern: string
  answer: string
  original_user_query: string
  resulting_sql: string
  user_confirmed: boolean
  confidence: number
  category: string
  created_at: number
  last_used_at: number
  use_count: number
  tags: string[]
}

export interface Metrics {
  total_learned_patterns: number
  total_static_entries: number
  avg_confidence: number
  auto_answer_eligible: number
  pattern_categories: Record<string, number>
  entry_sources: Record<string, number>
}

export async function fetchKnowledge(
  params?: { category?: string; source?: string; search?: string },
): Promise<{ entries: KnowledgeEntry[]; total: number }> {
  const qs = new URLSearchParams()
  if (params?.category) qs.set('category', params.category)
  if (params?.source) qs.set('source', params.source)
  if (params?.search) qs.set('search', params.search)
  const res = await fetch(`${BASE}/knowledge?${qs}`)
  return res.json()
}

export async function createKnowledge(
  content: string,
  category: string,
): Promise<KnowledgeEntry> {
  const res = await fetch(`${BASE}/knowledge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, category }),
  })
  return res.json()
}

export async function updateKnowledge(
  id: string,
  content: string,
  category: string,
): Promise<void> {
  await fetch(`${BASE}/knowledge/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, category }),
  })
}

export async function deleteKnowledge(id: string): Promise<void> {
  await fetch(`${BASE}/knowledge/${id}`, { method: 'DELETE' })
}

export async function fetchPatterns(
  params?: { category?: string; min_confidence?: number; sort?: string },
): Promise<{ patterns: LearnedPattern[]; total: number }> {
  const qs = new URLSearchParams()
  if (params?.category) qs.set('category', params.category)
  if (params?.min_confidence !== undefined)
    qs.set('min_confidence', String(params.min_confidence))
  if (params?.sort) qs.set('sort', params.sort)
  const res = await fetch(`${BASE}/patterns?${qs}`)
  return res.json()
}

export async function updatePattern(
  id: string,
  updates: Partial<LearnedPattern>,
): Promise<void> {
  await fetch(`${BASE}/patterns/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
}

export async function deletePattern(id: string): Promise<void> {
  await fetch(`${BASE}/patterns/${id}`, { method: 'DELETE' })
}

export async function fetchMetrics(): Promise<Metrics> {
  const res = await fetch(`${BASE}/metrics`)
  return res.json()
}

export async function testAgent(
  question: string,
  userQuery?: string,
): Promise<{ auto_answered: boolean; answer: string; trace: unknown }> {
  const res = await fetch(`${BASE}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, user_query: userQuery ?? '' }),
  })
  return res.json()
}

export async function exportStore(): Promise<unknown> {
  const res = await fetch(`${BASE}/export`)
  return res.json()
}

export async function importStore(
  data: unknown,
  mode: string = 'merge',
): Promise<{ status: string; entries_added: number; patterns_added: number }> {
  const res = await fetch(`${BASE}/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data, mode }),
  })
  return res.json()
}
