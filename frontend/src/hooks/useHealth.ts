import { useQuery } from '@tanstack/react-query'
import { fetchHealth } from '../api/schema'
import type { HealthStatus } from '../types'

export function useHealth() {
  return useQuery<HealthStatus>({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
    retry: 2,
  })
}
