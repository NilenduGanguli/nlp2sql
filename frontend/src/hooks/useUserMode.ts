import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { UserMode } from '../types'

interface UserModeState {
  mode: UserMode
  setMode: (mode: UserMode) => void
}

export const useUserMode = create<UserModeState>()(
  persist(
    (set) => ({
      mode: 'curator',
      setMode: (mode) => set({ mode }),
    }),
    { name: 'nlp2sql.userMode' },
  ),
)
