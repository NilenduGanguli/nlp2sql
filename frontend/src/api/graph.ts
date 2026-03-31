import { apiFetch } from './client'
import type { GraphVisualization, JoinPath, ForeignKey } from '../types'

export function fetchGraphVisualization(limit = 200): Promise<GraphVisualization> {
  return apiFetch<GraphVisualization>(`/graph/visualization?limit=${limit}`)
}

export function fetchJoinPath(from: string, to: string): Promise<JoinPath> {
  const params = new URLSearchParams({ from, to })
  return apiFetch<JoinPath>(`/graph/join-path?${params}`)
}

export function fetchForeignKeys(): Promise<ForeignKey[]> {
  return apiFetch<ForeignKey[]>('/graph/foreign-keys')
}
