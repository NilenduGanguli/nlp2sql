import { useQuery } from '@tanstack/react-query'
import { searchSchema } from '../api/schema'
import type { SearchResponse } from '../types'

export function useSearch(q: string) {
  return useQuery<SearchResponse>({
    queryKey: ['search', q],
    queryFn: () => searchSchema(q),
    enabled: q.trim().length >= 2,
    staleTime: 60_000,
  })
}
