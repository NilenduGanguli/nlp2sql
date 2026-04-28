import React, { useState, useEffect, useCallback, useRef } from 'react'
import type {
  KnowledgeEntry,
  LearnedPattern,
  Metrics,
} from '../api/kycAgent'
import {
  fetchKnowledge,
  createKnowledge,
  updateKnowledge,
  deleteKnowledge,
  fetchPatterns,
  updatePattern,
  deletePattern,
  fetchMetrics,
  testAgent,
  exportStore,
  importStore,
} from '../api/kycAgent'
import { PatternsTab } from '../components/kyc/PatternsTab'

// ── Theme ────────────────────────────────────────────────────────────────────

const C = {
  bg: '#1a1a2e',
  panel: '#1e1e2e',
  panel2: '#2a2a3e',
  border: '#2a2a3e',
  borderLight: '#3a3a5c',
  text: '#e0e0f0',
  muted: '#6a6a8a',
  accent: '#7c6af7',
  accentDim: '#4e45a4',
  success: '#34d399',
  warning: '#fbbf24',
  error: '#f87171',
  code: '#1a1a2e',
}

// ── Helpers ──────────────────────────────────────────────────────────────────

type LeftTab = 'knowledge' | 'patterns' | 'verified' | 'metrics'

function formatTs(epoch: number): string {
  if (!epoch) return '--'
  const d = new Date(epoch * 1000)
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function truncate(s: string, max: number): string {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + '...' : s
}

function confidenceColor(c: number): string {
  if (c >= 0.8) return C.success
  if (c >= 0.5) return C.warning
  return C.error
}

const CATEGORIES = [
  'business_rule',
  'table_description',
  'column_mapping',
  'join_hint',
  'query_pattern',
  'domain_knowledge',
  'acronym',
  'other',
]

// ── Shared inline styles ─────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  background: C.bg,
  border: `1px solid ${C.borderLight}`,
  borderRadius: 6,
  color: C.text,
  fontSize: 13,
  outline: 'none',
  boxSizing: 'border-box',
}

const btnPrimary: React.CSSProperties = {
  padding: '7px 16px',
  background: C.accent,
  border: 'none',
  borderRadius: 6,
  color: '#fff',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
}

const btnDanger: React.CSSProperties = {
  padding: '7px 16px',
  background: C.error,
  border: 'none',
  borderRadius: 6,
  color: '#fff',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
}

const btnGhost: React.CSSProperties = {
  padding: '7px 14px',
  background: 'transparent',
  border: `1px solid ${C.borderLight}`,
  borderRadius: 6,
  color: C.muted,
  fontSize: 12,
  cursor: 'pointer',
}

const badge = (bg: string): React.CSSProperties => ({
  display: 'inline-block',
  padding: '2px 8px',
  borderRadius: 10,
  fontSize: 11,
  fontWeight: 600,
  background: bg + '22',
  color: bg,
  lineHeight: '18px',
})

// ── Confidence Bar ───────────────────────────────────────────────────────────

const ConfidenceBar: React.FC<{ value: number; width?: number }> = ({ value, width = 60 }) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
    }}
  >
    <div
      style={{
        width,
        height: 6,
        borderRadius: 3,
        background: C.bg,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          width: `${Math.round(value * 100)}%`,
          height: '100%',
          borderRadius: 3,
          background: confidenceColor(value),
          transition: 'width 0.3s',
        }}
      />
    </div>
    <span style={{ fontSize: 11, color: confidenceColor(value), fontWeight: 600 }}>
      {Math.round(value * 100)}%
    </span>
  </div>
)

// ══════════════════════════════════════════════════════════════════════════════
// ██  KYCAgentPage
// ══════════════════════════════════════════════════════════════════════════════

export const KYCAgentPage: React.FC = () => {
  // ── Left panel state ─────────────────────────────────────────────────────
  const [leftTab, setLeftTab] = useState<LeftTab>('knowledge')
  const [search, setSearch] = useState('')
  const [filterCategory, setFilterCategory] = useState('')
  const [filterSource, setFilterSource] = useState('')

  // ── Data ─────────────────────────────────────────────────────────────────
  const [entries, setEntries] = useState<KnowledgeEntry[]>([])
  const [entriesTotal, setEntriesTotal] = useState(0)
  const [entriesLoading, setEntriesLoading] = useState(false)

  const [patterns, setPatterns] = useState<LearnedPattern[]>([])
  const [patternsTotal, setPatternsTotal] = useState(0)
  const [patternsLoading, setPatternsLoading] = useState(false)
  const [patternSort, setPatternSort] = useState('confidence')

  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [metricsLoading, setMetricsLoading] = useState(false)

  // ── Selection ────────────────────────────────────────────────────────────
  const [selectedEntry, setSelectedEntry] = useState<KnowledgeEntry | null>(null)
  const [selectedPattern, setSelectedPattern] = useState<LearnedPattern | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  // ── Editor state (for entry editing) ─────────────────────────────────────
  const [editContent, setEditContent] = useState('')
  const [editCategory, setEditCategory] = useState('')
  const [saving, setSaving] = useState(false)

  // ── Agent tester ─────────────────────────────────────────────────────────
  const [testQuestion, setTestQuestion] = useState('')
  const [testUserQuery, setTestUserQuery] = useState('')
  const [testResult, setTestResult] = useState<{
    auto_answered: boolean
    answer: string
    trace: unknown
  } | null>(null)
  const [testing, setTesting] = useState(false)

  // ── Import/Export ────────────────────────────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [importMsg, setImportMsg] = useState('')

  // ══════════════════════════════════════════════════════════════════════════
  // Data fetching
  // ══════════════════════════════════════════════════════════════════════════

  const loadEntries = useCallback(async () => {
    setEntriesLoading(true)
    try {
      const res = await fetchKnowledge({
        category: filterCategory || undefined,
        source: filterSource || undefined,
        search: search || undefined,
      })
      setEntries(res.entries ?? [])
      setEntriesTotal(res.total ?? 0)
    } catch {
      setEntries([])
      setEntriesTotal(0)
    }
    setEntriesLoading(false)
  }, [filterCategory, filterSource, search])

  const loadPatterns = useCallback(async () => {
    setPatternsLoading(true)
    try {
      const res = await fetchPatterns({
        category: filterCategory || undefined,
        sort: patternSort || undefined,
      })
      setPatterns(res.patterns ?? [])
      setPatternsTotal(res.total ?? 0)
    } catch {
      setPatterns([])
      setPatternsTotal(0)
    }
    setPatternsLoading(false)
  }, [filterCategory, patternSort])

  const loadMetrics = useCallback(async () => {
    setMetricsLoading(true)
    try {
      const res = await fetchMetrics()
      setMetrics(res)
    } catch {
      setMetrics(null)
    }
    setMetricsLoading(false)
  }, [])

  // Load data when left tab or filters change
  useEffect(() => {
    if (leftTab === 'knowledge') loadEntries()
    else if (leftTab === 'patterns') loadPatterns()
    else if (leftTab === 'metrics') loadMetrics()
  }, [leftTab, loadEntries, loadPatterns, loadMetrics])

  // ── Selection helpers ────────────────────────────────────────────────────

  const selectEntry = (e: KnowledgeEntry) => {
    setSelectedEntry(e)
    setSelectedPattern(null)
    setIsCreating(false)
    setEditContent(e.content)
    setEditCategory(e.category)
  }

  const selectPattern = (p: LearnedPattern) => {
    setSelectedPattern(p)
    setSelectedEntry(null)
    setIsCreating(false)
  }

  const clearSelection = () => {
    setSelectedEntry(null)
    setSelectedPattern(null)
    setIsCreating(false)
  }

  const startCreate = () => {
    setSelectedEntry(null)
    setSelectedPattern(null)
    setIsCreating(true)
    setEditContent('')
    setEditCategory(CATEGORIES[0])
  }

  // ── CRUD actions ─────────────────────────────────────────────────────────

  const handleSaveEntry = async () => {
    if (!editContent.trim()) return
    setSaving(true)
    try {
      if (isCreating) {
        const created = await createKnowledge(editContent, editCategory)
        setIsCreating(false)
        setSelectedEntry(created)
        await loadEntries()
      } else if (selectedEntry) {
        await updateKnowledge(selectedEntry.id, editContent, editCategory)
        setSelectedEntry({ ...selectedEntry, content: editContent, category: editCategory })
        await loadEntries()
      }
    } catch {
      // silently fail for now
    }
    setSaving(false)
  }

  const handleDeleteEntry = async () => {
    if (!selectedEntry) return
    if (!confirm('Delete this knowledge entry?')) return
    try {
      await deleteKnowledge(selectedEntry.id)
      clearSelection()
      await loadEntries()
    } catch {
      // ignore
    }
  }

  const handleDeletePattern = async () => {
    if (!selectedPattern) return
    if (!confirm('Delete this learned pattern?')) return
    try {
      await deletePattern(selectedPattern.id)
      clearSelection()
      await loadPatterns()
    } catch {
      // ignore
    }
  }

  const handleConfirmPattern = async () => {
    if (!selectedPattern) return
    try {
      await updatePattern(selectedPattern.id, { user_confirmed: true })
      setSelectedPattern({ ...selectedPattern, user_confirmed: true })
      await loadPatterns()
    } catch {
      // ignore
    }
  }

  // ── Agent tester ─────────────────────────────────────────────────────────

  const handleTest = async () => {
    if (!testQuestion.trim()) return
    setTesting(true)
    setTestResult(null)
    try {
      const res = await testAgent(testQuestion, testUserQuery)
      setTestResult(res)
    } catch {
      setTestResult({ auto_answered: false, answer: 'Error: could not reach the agent.', trace: null })
    }
    setTesting(false)
  }

  // ── Import / Export ──────────────────────────────────────────────────────

  const handleExport = async () => {
    try {
      const data = await exportStore()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `kyc_agent_export_${Date.now()}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      // ignore
    }
  }

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImportMsg('')
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      const res = await importStore(data, 'merge')
      setImportMsg(
        `Imported ${res.entries_added} entries, ${res.patterns_added} patterns.`,
      )
      // refresh whichever tab is active
      if (leftTab === 'knowledge') await loadEntries()
      else if (leftTab === 'patterns') await loadPatterns()
      if (leftTab === 'metrics') await loadMetrics()
    } catch {
      setImportMsg('Import failed -- invalid JSON or server error.')
    }
    // reset the file input so the same file can be re-imported
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // ══════════════════════════════════════════════════════════════════════════
  // Render helpers
  // ══════════════════════════════════════════════════════════════════════════

  const renderLeftTabButton = (id: LeftTab, label: string) => {
    const active = leftTab === id
    return (
      <button
        key={id}
        onClick={() => {
          setLeftTab(id)
          clearSelection()
          setSearch('')
          setFilterCategory('')
          setFilterSource('')
        }}
        style={{
          flex: 1,
          padding: '6px 0',
          background: active ? C.accent : 'transparent',
          border: 'none',
          borderRadius: 5,
          color: active ? '#fff' : C.muted,
          fontSize: 12,
          fontWeight: active ? 700 : 500,
          cursor: 'pointer',
          transition: 'all 0.15s',
        }}
      >
        {label}
      </button>
    )
  }

  // ── Left panel: knowledge list ──

  const renderKnowledgeList = () => {
    if (entriesLoading) {
      return <div style={{ padding: 16, color: C.muted, fontSize: 13 }}>Loading...</div>
    }
    if (entries.length === 0) {
      return (
        <div style={{ padding: 16, color: C.muted, fontSize: 13, textAlign: 'center' }}>
          {search ? 'No entries match your search.' : 'No knowledge entries yet.'}
        </div>
      )
    }
    return entries.map((e) => {
      const isSelected = selectedEntry?.id === e.id
      const isSession = e.source === 'query_session'
      const meta = e.metadata as Record<string, unknown> | undefined
      const originalQuery = meta && typeof meta.original_query === 'string'
        ? (meta.original_query as string)
        : ''
      return (
        <div
          key={e.id}
          onClick={() => selectEntry(e)}
          style={{
            padding: '10px 12px',
            cursor: 'pointer',
            background: isSelected ? C.accent + '18' : 'transparent',
            borderLeft: isSelected ? `3px solid ${C.accent}` : '3px solid transparent',
            borderBottom: `1px solid ${C.border}`,
            transition: 'background 0.1s',
          }}
        >
          <div style={{ fontSize: 13, color: C.text, lineHeight: '18px', marginBottom: 4 }}>
            {truncate(isSession && originalQuery ? originalQuery : e.content, 80)}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={badge(isSession ? C.success : C.accent)}>
              {isSession ? '\u267B session' : e.category}
            </span>
            <span style={{ fontSize: 11, color: C.muted }}>{e.source}</span>
          </div>
        </div>
      )
    })
  }

  // ── Left panel: patterns list ──

  const renderPatternsList = () => {
    if (patternsLoading) {
      return <div style={{ padding: 16, color: C.muted, fontSize: 13 }}>Loading...</div>
    }
    if (patterns.length === 0) {
      return (
        <div style={{ padding: 16, color: C.muted, fontSize: 13, textAlign: 'center' }}>
          No learned patterns yet.
        </div>
      )
    }
    return patterns.map((p) => {
      const isSelected = selectedPattern?.id === p.id
      return (
        <div
          key={p.id}
          onClick={() => selectPattern(p)}
          style={{
            padding: '10px 12px',
            cursor: 'pointer',
            background: isSelected ? C.accent + '18' : 'transparent',
            borderLeft: isSelected ? `3px solid ${C.accent}` : '3px solid transparent',
            borderBottom: `1px solid ${C.border}`,
            transition: 'background 0.1s',
          }}
        >
          <div style={{ fontSize: 13, color: C.text, lineHeight: '18px', marginBottom: 4 }}>
            {truncate(p.question_pattern, 70)}
          </div>
          <div
            style={{
              display: 'flex',
              gap: 8,
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            <ConfidenceBar value={p.confidence} width={50} />
            <span style={{ fontSize: 11, color: C.muted }}>used {p.use_count}x</span>
            <span style={badge(p.user_confirmed ? C.success : C.warning)}>
              {p.user_confirmed ? 'confirmed' : 'unconfirmed'}
            </span>
          </div>
        </div>
      )
    })
  }

  // ── Left panel: metrics ──

  const renderMetrics = () => {
    if (metricsLoading) {
      return <div style={{ padding: 16, color: C.muted, fontSize: 13 }}>Loading...</div>
    }
    if (!metrics) {
      return (
        <div style={{ padding: 16, color: C.muted, fontSize: 13, textAlign: 'center' }}>
          Could not load metrics.
        </div>
      )
    }

    const StatCard: React.FC<{ label: string; value: string | number; color?: string }> = ({
      label,
      value,
      color,
    }) => (
      <div
        style={{
          padding: '12px',
          background: C.bg,
          borderRadius: 8,
          border: `1px solid ${C.border}`,
        }}
      >
        <div style={{ fontSize: 20, fontWeight: 700, color: color ?? C.text }}>{value}</div>
        <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{label}</div>
      </div>
    )

    return (
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <StatCard
            label="Static Entries"
            value={metrics.total_static_entries}
            color={C.accent}
          />
          <StatCard
            label="Learned Patterns"
            value={metrics.total_learned_patterns}
            color={C.success}
          />
          <StatCard
            label="Avg Confidence"
            value={`${Math.round(metrics.avg_confidence * 100)}%`}
            color={confidenceColor(metrics.avg_confidence)}
          />
          <StatCard
            label="Auto-answer Ready"
            value={metrics.auto_answer_eligible}
            color={C.warning}
          />
        </div>

        {/* Category breakdown */}
        {Object.keys(metrics.pattern_categories).length > 0 && (
          <div>
            <div
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: C.muted,
                marginBottom: 6,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              Pattern Categories
            </div>
            {Object.entries(metrics.pattern_categories).map(([cat, count]) => (
              <div
                key={cat}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '4px 0',
                  fontSize: 13,
                  color: C.text,
                  borderBottom: `1px solid ${C.border}`,
                }}
              >
                <span>{cat}</span>
                <span style={{ fontWeight: 600 }}>{count}</span>
              </div>
            ))}
          </div>
        )}

        {Object.keys(metrics.entry_sources).length > 0 && (
          <div>
            <div
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: C.muted,
                marginBottom: 6,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              Entry Sources
            </div>
            {Object.entries(metrics.entry_sources).map(([src, count]) => (
              <div
                key={src}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '4px 0',
                  fontSize: 13,
                  color: C.text,
                  borderBottom: `1px solid ${C.border}`,
                }}
              >
                <span>{src}</span>
                <span style={{ fontWeight: 600 }}>{count}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // ── Right panel: query_session entry (read-only, rich) ──

  const renderSessionEntry = () => {
    if (!selectedEntry) return null
    const meta = (selectedEntry.metadata ?? {}) as Record<string, unknown>
    const originalQuery = typeof meta.original_query === 'string' ? (meta.original_query as string) : ''
    const enrichedQuery = typeof meta.enriched_query === 'string' ? (meta.enriched_query as string) : ''
    const tablesUsed = Array.isArray(meta.tables_used) ? (meta.tables_used as string[]) : []
    const accepted = Array.isArray(meta.accepted_candidates)
      ? (meta.accepted_candidates as Array<Record<string, unknown>>)
      : []
    const rejected = Array.isArray(meta.rejected_candidates)
      ? (meta.rejected_candidates as Array<Record<string, unknown>>)
      : []
    const clarifications = Array.isArray(meta.clarifications)
      ? (meta.clarifications as Array<Record<string, unknown>>)
      : []
    const createdAt = typeof meta.created_at === 'number' ? (meta.created_at as number) : 0

    const handleRerun = () => {
      if (!originalQuery) return
      window.dispatchEvent(
        new CustomEvent('rerun-query-from-session', { detail: { query: originalQuery } }),
      )
    }

    const sectionLabel: React.CSSProperties = {
      fontSize: 11,
      fontWeight: 600,
      color: C.muted,
      textTransform: 'uppercase',
      letterSpacing: '0.5px',
      marginBottom: 6,
    }
    const sectionCard: React.CSSProperties = {
      background: C.bg,
      padding: '10px 12px',
      borderRadius: 6,
      border: `1px solid ${C.border}`,
      lineHeight: '1.5',
    }

    return (
      <div
        style={{
          padding: 24,
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          height: '100%',
          overflowY: 'auto',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 16, color: C.text }}>
            <span style={{ marginRight: 8 }}>{'\u267B'}</span>Learned Query Session
          </h3>
          <div style={{ display: 'flex', gap: 8 }}>
            {originalQuery && (
              <button
                onClick={handleRerun}
                style={{
                  padding: '7px 14px',
                  background: C.accent,
                  border: 'none',
                  borderRadius: 6,
                  color: '#fff',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                {'\u21BB Re-run in Chat'}
              </button>
            )}
            <button onClick={clearSelection} style={btnGhost}>
              Close
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, fontSize: 11, color: C.muted, flexWrap: 'wrap' }}>
          <span>ID: {selectedEntry.id}</span>
          <span>Source: {selectedEntry.source}</span>
          {createdAt > 0 && <span>Saved: {formatTs(createdAt)}</span>}
        </div>

        {originalQuery && (
          <div>
            <div style={sectionLabel}>Original Query</div>
            <div style={sectionCard}>{originalQuery}</div>
          </div>
        )}

        {enrichedQuery && enrichedQuery !== originalQuery && (
          <div>
            <div style={sectionLabel}>Enriched Query</div>
            <div style={{ ...sectionCard, fontStyle: 'italic', color: C.muted }}>
              {enrichedQuery}
            </div>
          </div>
        )}

        {tablesUsed.length > 0 && (
          <div>
            <div style={sectionLabel}>Tables Used</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {tablesUsed.map((t) => (
                <span key={t} style={badge(C.accent)}>{t}</span>
              ))}
            </div>
          </div>
        )}

        {clarifications.length > 0 && (
          <div>
            <div style={sectionLabel}>Clarifications</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {clarifications.map((q, i) => (
                <div key={i} style={sectionCard}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: C.text, marginBottom: 4 }}>
                    Q: {String(q.question ?? '')}
                  </div>
                  <div style={{ fontSize: 12, color: C.muted }}>
                    A: {String(q.answer ?? '')}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {accepted.length > 0 && (
          <div>
            <div style={sectionLabel}>
              Accepted Candidates ({accepted.length})
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {accepted.map((c, i) => (
                <details
                  key={i}
                  style={{
                    background: C.bg,
                    padding: '8px 12px',
                    borderRadius: 6,
                    border: `1px solid ${C.success}55`,
                  }}
                >
                  <summary
                    style={{
                      cursor: 'pointer',
                      fontSize: 13,
                      color: C.text,
                      fontWeight: 600,
                    }}
                  >
                    {String(c.interpretation ?? `Candidate ${i + 1}`)}
                  </summary>
                  {typeof c.explanation === 'string' && c.explanation && (
                    <div style={{ fontSize: 12, color: C.muted, marginTop: 6, fontStyle: 'italic' }}>
                      {c.explanation as string}
                    </div>
                  )}
                  <pre
                    style={{
                      margin: '8px 0 0',
                      padding: '8px 10px',
                      background: C.code,
                      borderRadius: 4,
                      fontSize: 11,
                      fontFamily: 'monospace',
                      color: '#a5b4fc',
                      whiteSpace: 'pre-wrap',
                      overflowX: 'auto',
                    }}
                  >
                    {String(c.sql ?? '')}
                  </pre>
                </details>
              ))}
            </div>
          </div>
        )}

        {rejected.length > 0 && (
          <div>
            <div style={sectionLabel}>
              Rejected Candidates ({rejected.length})
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {rejected.map((c, i) => (
                <details
                  key={i}
                  style={{
                    background: C.bg,
                    padding: '8px 12px',
                    borderRadius: 6,
                    border: `1px solid ${C.error}55`,
                  }}
                >
                  <summary
                    style={{
                      cursor: 'pointer',
                      fontSize: 13,
                      color: C.text,
                      fontWeight: 600,
                    }}
                  >
                    {String(c.interpretation ?? `Candidate ${i + 1}`)}
                  </summary>
                  {typeof c.rejection_reason === 'string' && c.rejection_reason && (
                    <div style={{ fontSize: 12, color: C.error, marginTop: 6 }}>
                      Reason: {c.rejection_reason as string}
                    </div>
                  )}
                  <pre
                    style={{
                      margin: '8px 0 0',
                      padding: '8px 10px',
                      background: C.code,
                      borderRadius: 4,
                      fontSize: 11,
                      fontFamily: 'monospace',
                      color: '#a5b4fc',
                      whiteSpace: 'pre-wrap',
                      overflowX: 'auto',
                    }}
                  >
                    {String(c.sql ?? '')}
                  </pre>
                </details>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  // ── Right panel: entry editor ──

  const renderEntryEditor = () => {
    const isNew = isCreating
    return (
      <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16, height: '100%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 16, color: C.text }}>
            {isNew ? 'New Knowledge Entry' : 'Edit Knowledge Entry'}
          </h3>
          <button onClick={clearSelection} style={btnGhost}>
            Close
          </button>
        </div>

        {!isNew && selectedEntry && (
          <div style={{ display: 'flex', gap: 8, fontSize: 12, color: C.muted }}>
            <span>ID: {selectedEntry.id}</span>
            <span>Source: {selectedEntry.source}</span>
          </div>
        )}

        <div>
          <label style={{ fontSize: 12, color: C.muted, display: 'block', marginBottom: 4 }}>
            Category
          </label>
          <select
            value={editCategory}
            onChange={(e) => setEditCategory(e.target.value)}
            style={{
              ...inputStyle,
              cursor: 'pointer',
            }}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <label style={{ fontSize: 12, color: C.muted, display: 'block', marginBottom: 4 }}>
            Content
          </label>
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            placeholder="Enter knowledge content..."
            style={{
              ...inputStyle,
              flex: 1,
              minHeight: 200,
              resize: 'vertical',
              fontFamily: 'monospace',
              lineHeight: '1.5',
            }}
          />
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          {!isNew && selectedEntry && (
            <button onClick={handleDeleteEntry} style={btnDanger}>
              Delete
            </button>
          )}
          <button
            onClick={handleSaveEntry}
            disabled={saving || !editContent.trim()}
            style={{
              ...btnPrimary,
              opacity: saving || !editContent.trim() ? 0.5 : 1,
            }}
          >
            {saving ? 'Saving...' : isNew ? 'Create' : 'Save Changes'}
          </button>
        </div>
      </div>
    )
  }

  // ── Right panel: pattern detail ──

  const renderPatternDetail = () => {
    if (!selectedPattern) return null
    const p = selectedPattern

    const DetailRow: React.FC<{ label: string; children: React.ReactNode }> = ({
      label,
      children,
    }) => (
      <div style={{ marginBottom: 14 }}>
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: C.muted,
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
            marginBottom: 4,
          }}
        >
          {label}
        </div>
        <div style={{ color: C.text, fontSize: 13 }}>{children}</div>
      </div>
    )

    return (
      <div
        style={{
          padding: 24,
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          height: '100%',
          overflowY: 'auto',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <h3 style={{ margin: 0, fontSize: 16, color: C.text }}>Learned Pattern</h3>
          <button onClick={clearSelection} style={btnGhost}>
            Close
          </button>
        </div>

        <DetailRow label="Question Pattern">
          <div
            style={{
              background: C.bg,
              padding: '10px 12px',
              borderRadius: 6,
              border: `1px solid ${C.border}`,
              lineHeight: '1.5',
            }}
          >
            {p.question_pattern}
          </div>
        </DetailRow>

        <DetailRow label="Answer">
          <div
            style={{
              background: C.bg,
              padding: '10px 12px',
              borderRadius: 6,
              border: `1px solid ${C.border}`,
              lineHeight: '1.5',
              whiteSpace: 'pre-wrap',
            }}
          >
            {p.answer}
          </div>
        </DetailRow>

        {p.original_user_query && (
          <DetailRow label="Original User Query">
            <span style={{ fontStyle: 'italic', color: C.muted }}>{p.original_user_query}</span>
          </DetailRow>
        )}

        {p.resulting_sql && (
          <DetailRow label="Resulting SQL">
            <pre
              style={{
                background: C.bg,
                padding: '10px 12px',
                borderRadius: 6,
                border: `1px solid ${C.border}`,
                fontSize: 12,
                fontFamily: 'monospace',
                whiteSpace: 'pre-wrap',
                margin: 0,
                overflowX: 'auto',
              }}
            >
              {p.resulting_sql}
            </pre>
          </DetailRow>
        )}

        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
          <DetailRow label="Confidence">
            <ConfidenceBar value={p.confidence} width={100} />
          </DetailRow>
          <DetailRow label="Status">
            <span style={badge(p.user_confirmed ? C.success : C.warning)}>
              {p.user_confirmed ? 'Confirmed' : 'Unconfirmed'}
            </span>
          </DetailRow>
          <DetailRow label="Category">
            <span style={badge(C.accent)}>{p.category}</span>
          </DetailRow>
          <DetailRow label="Use Count">
            <span style={{ fontWeight: 700 }}>{p.use_count}</span>
          </DetailRow>
        </div>

        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
          <DetailRow label="Created">{formatTs(p.created_at)}</DetailRow>
          <DetailRow label="Last Used">{formatTs(p.last_used_at)}</DetailRow>
        </div>

        {p.tags.length > 0 && (
          <DetailRow label="Tags">
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {p.tags.map((t) => (
                <span key={t} style={badge(C.muted)}>
                  {t}
                </span>
              ))}
            </div>
          </DetailRow>
        )}

        <div
          style={{
            display: 'flex',
            gap: 8,
            justifyContent: 'flex-end',
            marginTop: 'auto',
            paddingTop: 16,
          }}
        >
          {!p.user_confirmed && (
            <button onClick={handleConfirmPattern} style={{ ...btnPrimary, background: C.success }}>
              Confirm Pattern
            </button>
          )}
          <button onClick={handleDeletePattern} style={btnDanger}>
            Delete
          </button>
        </div>
      </div>
    )
  }

  // ── Right panel: agent tester ──

  const renderAgentTester = () => (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16, height: '100%' }}>
      <h3 style={{ margin: 0, fontSize: 16, color: C.text }}>Agent Tester</h3>
      <p style={{ margin: 0, fontSize: 13, color: C.muted, lineHeight: '1.5' }}>
        Test how the KYC Business Agent resolves a question using its knowledge store and learned
        patterns. The agent will indicate whether it could auto-answer from existing knowledge.
      </p>

      <div>
        <label style={{ fontSize: 12, color: C.muted, display: 'block', marginBottom: 4 }}>
          Question (what the agent needs to clarify)
        </label>
        <textarea
          value={testQuestion}
          onChange={(e) => setTestQuestion(e.target.value)}
          placeholder='e.g. "What does KYC status PENDING mean?"'
          rows={3}
          style={{
            ...inputStyle,
            resize: 'vertical',
            fontFamily: 'monospace',
          }}
        />
      </div>

      <div>
        <label style={{ fontSize: 12, color: C.muted, display: 'block', marginBottom: 4 }}>
          Original User Query (optional context)
        </label>
        <textarea
          value={testUserQuery}
          onChange={(e) => setTestUserQuery(e.target.value)}
          placeholder='e.g. "Show me all pending KYC applications"'
          rows={2}
          style={{
            ...inputStyle,
            resize: 'vertical',
            fontFamily: 'monospace',
          }}
        />
      </div>

      <div>
        <button
          onClick={handleTest}
          disabled={testing || !testQuestion.trim()}
          style={{
            ...btnPrimary,
            opacity: testing || !testQuestion.trim() ? 0.5 : 1,
          }}
        >
          {testing ? 'Running...' : 'Test Agent'}
        </button>
      </div>

      {testResult && (
        <div
          style={{
            flex: 1,
            background: C.bg,
            border: `1px solid ${C.borderLight}`,
            borderRadius: 8,
            padding: 16,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span
              style={badge(testResult.auto_answered ? C.success : C.warning)}
            >
              {testResult.auto_answered ? 'Auto-answered' : 'Not auto-answered'}
            </span>
          </div>

          <div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: C.muted,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
                marginBottom: 4,
              }}
            >
              Answer
            </div>
            <div
              style={{
                fontSize: 13,
                color: C.text,
                lineHeight: '1.6',
                whiteSpace: 'pre-wrap',
                background: C.panel,
                padding: '10px 12px',
                borderRadius: 6,
                border: `1px solid ${C.border}`,
              }}
            >
              {testResult.answer}
            </div>
          </div>

          {testResult.trace != null && (
            <div>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: C.muted,
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                  marginBottom: 4,
                }}
              >
                Trace
              </div>
              <pre
                style={{
                  fontSize: 11,
                  color: C.muted,
                  fontFamily: 'monospace',
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                  maxHeight: 200,
                  overflowY: 'auto',
                  background: C.panel,
                  padding: '10px 12px',
                  borderRadius: 6,
                  border: `1px solid ${C.border}`,
                }}
              >
                {JSON.stringify(testResult.trace, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )

  // ══════════════════════════════════════════════════════════════════════════
  // Main render
  // ══════════════════════════════════════════════════════════════════════════

  const showSessionEntry =
    !isCreating && selectedEntry !== null && selectedEntry.source === 'query_session'
  const showEditor = isCreating || (selectedEntry !== null && !showSessionEntry)
  const showPatternDetail = selectedPattern !== null

  return (
    <div
      style={{
        display: 'flex',
        height: '100%',
        background: C.bg,
        overflow: 'hidden',
      }}
    >
      {/* ── Left Panel ─────────────────────────────────────────────────────── */}
      <div
        style={{
          width: 280,
          minWidth: 280,
          display: 'flex',
          flexDirection: 'column',
          borderRight: `1px solid ${C.borderLight}`,
          background: C.panel,
          overflow: 'hidden',
        }}
      >
        {/* Sub-tab switcher */}
        <div
          style={{
            display: 'flex',
            gap: 4,
            padding: '10px 10px 8px',
            background: C.panel2,
            borderBottom: `1px solid ${C.border}`,
          }}
        >
          {renderLeftTabButton('knowledge', 'Static')}
          {renderLeftTabButton('patterns', 'Patterns')}
          {renderLeftTabButton('verified', 'Verified')}
          {renderLeftTabButton('metrics', 'Metrics')}
        </div>

        {/* Search + filter (for knowledge and patterns tabs) */}
        {leftTab !== 'metrics' && leftTab !== 'verified' && (
          <div
            style={{
              padding: '10px',
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              borderBottom: `1px solid ${C.border}`,
            }}
          >
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search..."
              style={inputStyle}
            />
            <select
              value={filterCategory}
              onChange={(e) => setFilterCategory(e.target.value)}
              style={{ ...inputStyle, cursor: 'pointer' }}
            >
              <option value="">All categories</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            {leftTab === 'knowledge' && (
              <select
                value={filterSource}
                onChange={(e) => setFilterSource(e.target.value)}
                style={{ ...inputStyle, cursor: 'pointer' }}
              >
                <option value="">All sources</option>
                <option value="query_session">Query sessions</option>
                <option value="user">User</option>
                <option value="auto_learned">Auto-learned</option>
                <option value="default">Default</option>
              </select>
            )}
            {leftTab === 'patterns' && (
              <select
                value={patternSort}
                onChange={(e) => setPatternSort(e.target.value)}
                style={{ ...inputStyle, cursor: 'pointer' }}
              >
                <option value="confidence">Sort: Confidence</option>
                <option value="use_count">Sort: Use Count</option>
                <option value="recent">Sort: Recent</option>
              </select>
            )}
          </div>
        )}

        {/* List area */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {leftTab === 'knowledge' && renderKnowledgeList()}
          {leftTab === 'patterns' && renderPatternsList()}
          {leftTab === 'verified' && <PatternsTab />}
          {leftTab === 'metrics' && renderMetrics()}
        </div>

        {/* Bottom actions */}
        <div
          style={{
            padding: '10px',
            borderTop: `1px solid ${C.border}`,
            display: 'flex',
            gap: 6,
            flexWrap: 'wrap',
            alignItems: 'center',
          }}
        >
          {leftTab === 'knowledge' && (
            <button onClick={startCreate} style={btnPrimary}>
              + Add
            </button>
          )}
          <button onClick={handleExport} style={btnGhost}>
            Export
          </button>
          <button onClick={() => fileInputRef.current?.click()} style={btnGhost}>
            Import
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json"
            style={{ display: 'none' }}
            onChange={handleImportFile}
          />
          {leftTab !== 'metrics' && leftTab !== 'verified' && (
            <span style={{ fontSize: 11, color: C.muted, marginLeft: 'auto' }}>
              {leftTab === 'knowledge' ? `${entriesTotal} entries` : `${patternsTotal} patterns`}
            </span>
          )}
        </div>

        {importMsg && (
          <div
            style={{
              padding: '6px 10px',
              fontSize: 12,
              color: importMsg.includes('failed') ? C.error : C.success,
              borderTop: `1px solid ${C.border}`,
            }}
          >
            {importMsg}
          </div>
        )}
      </div>

      {/* ── Right Panel ────────────────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          background: C.panel,
        }}
      >
        {showSessionEntry
          ? renderSessionEntry()
          : showEditor
            ? renderEntryEditor()
            : showPatternDetail
              ? renderPatternDetail()
              : renderAgentTester()}
      </div>
    </div>
  )
}
