import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { UserMode } from '../types'

export const USER_MODE_STORAGE_KEY = 'nlp2sql.userMode'

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
    { name: USER_MODE_STORAGE_KEY },
  ),
)
