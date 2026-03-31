import { useQuery } from '@tanstack/react-query'
import { fetchForeignKeys } from '../api/graph'
import type { ForeignKey } from '../types'

export function useForeignKeys() {
  return useQuery<ForeignKey[]>({
    queryKey: ['fkeys'],
    queryFn: fetchForeignKeys,
    staleTime: Infinity,
  })
}
