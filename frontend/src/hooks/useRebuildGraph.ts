import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'

export function useRebuildGraph() {
  const queryClient = useQueryClient()
  return useMutation<{ status: string }, Error, void>({
    mutationFn: () => apiFetch<{ status: string }>('/admin/rebuild', { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['health'] })
      void queryClient.invalidateQueries({ queryKey: ['tables'] })
      void queryClient.invalidateQueries({ queryKey: ['schema'] })
      void queryClient.invalidateQueries({ queryKey: ['graph'] })
      void queryClient.invalidateQueries({ queryKey: ['fkeys'] })
    },
  })
}
