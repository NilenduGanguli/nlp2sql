import { useQuery } from '@tanstack/react-query'
import { fetchSchemaStats, fetchTables, fetchTableDetail, searchSchema } from '../api/schema'
import type { SchemaStats, TableSummary, TableDetail, SearchResponse } from '../types'

export function useSchemaStats() {
  return useQuery<SchemaStats>({
    queryKey: ['schema', 'stats'],
    queryFn: fetchSchemaStats,
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * Loads ALL tables (all pages) for client-side virtual list.
 * Cached with staleTime: Infinity since the graph rarely changes mid-session.
 */
export function useAllTables() {
  return useQuery<TableSummary[]>({
    queryKey: ['tables', 'all'],
    queryFn: async () => {
      const first = await fetchTables(1, 500)
      if (first.total <= 500) return first.items

      const remaining = first.pages - 1
      const promises = Array.from({ length: remaining }, (_, i) =>
        fetchTables(i + 2, 500).then((r) => r.items),
      )
      const rest = await Promise.all(promises)
      return [...first.items, ...rest.flat()]
    },
    staleTime: Infinity,
  })
}

export function useTableDetail(fqn: string | null) {
  return useQuery<TableDetail>({
    queryKey: ['table', fqn],
    queryFn: () => fetchTableDetail(fqn!),
    enabled: !!fqn,
    staleTime: 5 * 60 * 1000,
  })
}

export function useSearch(q: string) {
  return useQuery<SearchResponse>({
    queryKey: ['search', q],
    queryFn: () => searchSchema(q),
    enabled: q.trim().length >= 2,
    staleTime: 30_000,
  })
}
