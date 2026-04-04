import { useQuery } from '@tanstack/react-query'
import { fetchGraphVisualization, fetchForeignKeys } from '../api/graph'
import type { GraphVisualization, ForeignKey } from '../types'

export function useGraph(limit = 10000) {
  return useQuery<GraphVisualization>({
    queryKey: ['graph', 'visualization', limit],
    queryFn: () => fetchGraphVisualization(limit),
    staleTime: Infinity,
  })
}

export function useForeignKeys() {
  return useQuery<ForeignKey[]>({
    queryKey: ['graph', 'foreign-keys'],
    queryFn: fetchForeignKeys,
    staleTime: 5 * 60 * 1000,
  })
}
