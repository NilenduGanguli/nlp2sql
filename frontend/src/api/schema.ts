import { apiFetch } from './client'
import type {
  HealthStatus,
  SchemaStats,
  TableSummary,
  TableDetail,
  TableListResponse,
  SearchResponse,
} from '../types'

export function fetchHealth(): Promise<HealthStatus> {
  return apiFetch<HealthStatus>('/health')
}

export function fetchSchemaStats(): Promise<SchemaStats> {
  return apiFetch<SchemaStats>('/schema/stats')
}

export function fetchTables(page = 1, pageSize = 500, q = ''): Promise<TableListResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  if (q) params.set('q', q)
  return apiFetch<TableListResponse>(`/schema/tables?${params}`)
}

export function fetchTableDetail(fqn: string): Promise<TableDetail> {
  return apiFetch<TableDetail>(`/schema/tables/${encodeURIComponent(fqn)}`)
}

export function searchSchema(q: string): Promise<SearchResponse> {
  return apiFetch<SearchResponse>(`/schema/search?q=${encodeURIComponent(q)}`)
}
