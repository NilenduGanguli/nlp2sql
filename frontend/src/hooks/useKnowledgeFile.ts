import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'

interface KnowledgeFile {
  content: string
  path: string
  size_bytes: number
  enricher_enabled: boolean
}

async function fetchKnowledgeFile(): Promise<KnowledgeFile> {
  const res = await fetch('/api/admin/knowledge-file')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<KnowledgeFile>
}

async function postRegenerate(): Promise<void> {
  const res = await fetch('/api/admin/knowledge-file/regenerate', { method: 'POST' })
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail ?? `HTTP ${res.status}`)
  }
}

export function useKnowledgeFile() {
  return useQuery<KnowledgeFile>({
    queryKey: ['knowledge-file'],
    queryFn: fetchKnowledgeFile,
    staleTime: 30_000,
  })
}

export function useRegenerateKnowledge() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: postRegenerate,
    onSuccess: () => {
      // Re-fetch after a short delay to let the background task write the file
      setTimeout(() => void qc.invalidateQueries({ queryKey: ['knowledge-file'] }), 8000)
    },
  })
}
