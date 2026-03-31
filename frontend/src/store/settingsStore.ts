import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export const DEFAULT_MODELS: Record<string, string> = {
  openai: 'gpt-4o',
  anthropic: 'claude-opus-4-6',
  vertex: 'gemini-2.5-flash',
}

interface SettingsStore {
  llmProvider: string
  llmModel: string
  llmApiKey: string
  isSaving: boolean
  saveError: string | null
  setProvider(p: string): void
  setModel(m: string): void
  setApiKey(k: string): void
  /** POST /api/admin/config — returns error string or null on success */
  applySettings(): Promise<string | null>
  /** GET /api/admin/config — load current backend config into store */
  syncFromBackend(): Promise<void>
}

export const useSettingsStore = create<SettingsStore>()(
  persist(
    (set, get) => ({
      llmProvider: 'openai',
      llmModel: 'gpt-4o',
      llmApiKey: '',
      isSaving: false,
      saveError: null,

      setProvider: (p) =>
        set({ llmProvider: p, llmModel: DEFAULT_MODELS[p] ?? get().llmModel }),
      setModel: (m) => set({ llmModel: m }),
      setApiKey: (k) => set({ llmApiKey: k }),

      applySettings: async () => {
        const { llmProvider, llmModel, llmApiKey } = get()
        set({ isSaving: true, saveError: null })
        try {
          const res = await fetch('/api/admin/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              llm_provider: llmProvider,
              llm_model: llmModel,
              llm_api_key: llmApiKey,
            }),
          })
          if (!res.ok) {
            const body = (await res.json().catch(() => ({}))) as { detail?: string }
            const msg = body.detail ?? `Server error ${res.status}`
            set({ isSaving: false, saveError: msg })
            return msg
          }
          set({ isSaving: false, saveError: null })
          return null
        } catch (err) {
          const msg = (err as Error).message ?? 'Network error'
          set({ isSaving: false, saveError: msg })
          return msg
        }
      },

      syncFromBackend: async () => {
        try {
          const res = await fetch('/api/admin/config')
          if (!res.ok) return
          const cfg = (await res.json()) as {
            llm_provider: string
            llm_model: string
            has_api_key: boolean
          }
          set({
            llmProvider: cfg.llm_provider,
            llmModel: cfg.llm_model,
            // Only overwrite apiKey if it's currently empty (don't expose the real key)
            llmApiKey: get().llmApiKey || (cfg.has_api_key ? '••••••••' : ''),
          })
        } catch {
          // silently ignore — config is an optional enhancement
        }
      },
    }),
    {
      name: 'knowledgeql-settings',
      // Don't persist the API key to localStorage for security
      partialize: (s) => ({ llmProvider: s.llmProvider, llmModel: s.llmModel }),
    },
  ),
)
