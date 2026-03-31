import { useQuery } from '@tanstack/react-query'
import { fetchJoinPath } from '../api/graph'
import type { JoinPath } from '../types'

export function useJoinPath(from: string, to: string) {
  return useQuery<JoinPath>({
    queryKey: ['join-path', from, to],
    queryFn: () => fetchJoinPath(from, to),
    enabled: !!from && !!to && from !== to,
    staleTime: Infinity,
  })
}
