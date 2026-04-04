import { create } from 'zustand'
import type { TraceStep, QueryTrace } from '../types'

const MAX_TRACES = 20

interface TraceStore {
  traces: QueryTrace[]
  activeTraceId: string | null
  pendingSteps: TraceStep[]      // live steps accumulating during a query

  startQuery: (query: string) => string  // returns new trace id
  addLiveStep: (step: TraceStep) => void
  finalizeTrace: (traceId: string, allSteps: TraceStep[]) => void
  setActiveTrace: (id: string) => void
  clearTraces: () => void
}

function makeId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 7)
}

export const useTraceStore = create<TraceStore>((set, get) => ({
  traces: [],
  activeTraceId: null,
  pendingSteps: [],

  startQuery: (query: string) => {
    const id = makeId()
    set((s) => ({
      pendingSteps: [],
      // Insert a pending trace at top
      traces: [
        { id, query, timestamp: new Date(), steps: [] },
        ...s.traces.slice(0, MAX_TRACES - 1),
      ],
      activeTraceId: id,
    }))
    return id
  },

  addLiveStep: (step: TraceStep) => {
    set((s) => ({ pendingSteps: [...s.pendingSteps, step] }))
  },

  finalizeTrace: (traceId: string, allSteps: TraceStep[]) => {
    set((s) => ({
      traces: s.traces.map((t) =>
        t.id === traceId ? { ...t, steps: allSteps } : t,
      ),
      pendingSteps: [],
    }))
  },

  setActiveTrace: (id: string) => set({ activeTraceId: id }),

  clearTraces: () => set({ traces: [], activeTraceId: null, pendingSteps: [] }),
}))
