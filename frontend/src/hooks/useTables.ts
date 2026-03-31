import { useQuery } from '@tanstack/react-query'
import { fetchTables, fetchTableDetail } from '../api/schema'
import type { TableSummary, TableDetail } from '../types'

export function useTables() {
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

export function useTableDetail(fqn: string, enabled: boolean) {
  return useQuery<TableDetail>({
    queryKey: ['table', fqn],
    queryFn: () => fetchTableDetail(fqn),
    enabled: enabled && !!fqn,
    staleTime: Infinity,
  })
}
