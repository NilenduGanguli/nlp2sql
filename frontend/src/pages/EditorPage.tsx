import React, { useState, useCallback, useRef } from 'react'
import { SqlEditor } from '../components/editor/SqlEditor'
import { ResultGrid } from '../components/editor/ResultGrid'
import { useSqlExecute } from '../hooks/useSqlExecute'
import { useSqlFormat } from '../hooks/useSqlFormat'

const C = {
  bg: '#1e1e2e',
  panel: '#2a2a3e',
  border: '#3a3a5c',
  accent: '#7c6af7',
  text: '#e0e0f0',
  muted: '#9090a8',
  success: '#4ade80',
  error: '#f87171',
  warn: '#fbbf24',
}

interface HistoryEntry {
  id: string
  sql: string
  executedAt: Date
  rowCount?: number
  error?: string
}

function formatTime(d: Date) {
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function truncateSql(sql: string, max = 80) {
  const single = sql.replace(/\s+/g, ' ').trim()
  return single.length > max ? single.slice(0, max) + '…' : single
}

// ── History sidebar ──────────────────────────────────────────────────────────

interface HistoryPanelProps {
  entries: HistoryEntry[]
  onLoad: (sql: string) => void
  onClear: () => void
}

function HistoryPanel({ entries, onLoad, onClear }: HistoryPanelProps) {
  return (
    <div
      style={{
        width: 240,
        flexShrink: 0,
        borderRight: `1px solid ${C.border}`,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        background: C.panel,
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '8px 12px',
          borderBottom: `1px solid ${C.border}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: C.text }}>History</span>
        {entries.length > 0 && (
          <button
            onClick={onClear}
            style={{
              background: 'none',
              border: 'none',
              color: C.muted,
              fontSize: 11,
              cursor: 'pointer',
              padding: '2px 4px',
            }}
          >
            Clear
          </button>
        )}
      </div>

      {/* List */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {entries.length === 0 ? (
          <div style={{ padding: 16, color: C.muted, fontSize: 12, textAlign: 'center' }}>
            No queries yet
          </div>
        ) : (
          entries.map((entry) => (
            <div
              key={entry.id}
              onClick={() => onLoad(entry.sql)}
              style={{
                padding: '8px 12px',
                borderBottom: `1px solid ${C.border}`,
                cursor: 'pointer',
                background: 'transparent',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#32324a')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={{ fontSize: 11, color: C.muted, marginBottom: 3 }}>
                {formatTime(entry.executedAt)}
                {entry.rowCount != null && (
                  <span style={{ color: C.success, marginLeft: 6 }}>
                    {entry.rowCount} row{entry.rowCount !== 1 ? 's' : ''}
                  </span>
                )}
                {entry.error && (
                  <span style={{ color: C.error, marginLeft: 6 }}>error</span>
                )}
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: entry.error ? C.error : C.text,
                  fontFamily: 'monospace',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {truncateSql(entry.sql)}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

interface EditorPageProps {
  initialSql?: string
  onSqlChange?: (sql: string) => void
}

export const EditorPage: React.FC<EditorPageProps> = ({ initialSql = '', onSqlChange }) => {
  const [sql, setSql] = useState(initialSql)
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const sqlRef = useRef(sql)

  const { mutate: executeSql, data: execResult, isPending: executing, error: execError } =
    useSqlExecute()
  const { mutate: formatSql, isPending: formatting } = useSqlFormat()

  // Sync if parent passes new initialSql (from "Open in Editor")
  React.useEffect(() => {
    if (initialSql && initialSql !== sql) {
      setSql(initialSql)
      sqlRef.current = initialSql
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSql])

  const handleSqlChange = useCallback(
    (value: string) => {
      setSql(value)
      sqlRef.current = value
      onSqlChange?.(value)
    },
    [onSqlChange],
  )

  const handleRun = useCallback(() => {
    const current = sqlRef.current.trim()
    if (!current) return
    const snapshot = sqlRef.current

    executeSql(snapshot, {
      onSuccess: (result) => {
        setHistory((prev) => [
          {
            id: `${Date.now()}`,
            sql: snapshot,
            executedAt: new Date(),
            rowCount: result.rows?.length ?? 0,
            error: result.error ?? undefined,
          },
          ...prev.slice(0, 49),
        ])
        if (result.error) return // keep in history but don't clear
      },
      onError: (err) => {
        setHistory((prev) => [
          {
            id: `${Date.now()}`,
            sql: snapshot,
            executedAt: new Date(),
            error: err.message,
          },
          ...prev.slice(0, 49),
        ])
      },
    })
  }, [executeSql])

  const handleFormat = useCallback(() => {
    if (sql.trim()) {
      formatSql(sql, {
        onSuccess: (result) => {
          setSql(result.formatted_sql)
          sqlRef.current = result.formatted_sql
        },
      })
    }
  }, [sql, formatSql])

  const handleSave = useCallback(() => {
    const blob = new Blob([sql], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `query_${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.sql`
    a.click()
    URL.revokeObjectURL(url)
  }, [sql])

  const handleLoadFromHistory = useCallback((historySql: string) => {
    setSql(historySql)
    sqlRef.current = historySql
    onSqlChange?.(historySql)
  }, [onSqlChange])

  const execErrMsg =
    execError?.message ?? (execResult?.error ? execResult.error : undefined)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 16px',
          borderBottom: `1px solid ${C.border}`,
          background: C.panel,
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 13, color: C.muted, fontWeight: 500 }}>SQL Editor</span>
        <button
          onClick={() => setShowHistory((v) => !v)}
          style={{
            padding: '5px 12px',
            background: showHistory ? '#4e45a4' : 'none',
            border: `1px solid ${showHistory ? C.accent : C.border}`,
            borderRadius: 5,
            color: showHistory ? '#fff' : C.muted,
            fontSize: 12,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 5,
          }}
        >
          History
          {history.length > 0 && (
            <span
              style={{
                background: '#7c6af740',
                borderRadius: 10,
                padding: '0 6px',
                fontSize: 11,
                color: C.accent,
              }}
            >
              {history.length}
            </span>
          )}
        </button>
        <div style={{ flex: 1 }} />
        <button
          onClick={handleSave}
          disabled={!sql.trim()}
          title="Save current script as .sql file"
          style={{
            padding: '5px 14px',
            background: 'none',
            border: `1px solid ${C.border}`,
            borderRadius: 5,
            color: sql.trim() ? C.muted : '#4a4a6a',
            fontSize: 12,
            cursor: sql.trim() ? 'pointer' : 'not-allowed',
          }}
        >
          Save .sql
        </button>
        <button
          onClick={handleFormat}
          disabled={formatting || !sql.trim()}
          style={{
            padding: '5px 14px',
            background: 'none',
            border: `1px solid ${C.border}`,
            borderRadius: 5,
            color: C.muted,
            fontSize: 12,
            cursor: sql.trim() ? 'pointer' : 'not-allowed',
          }}
        >
          {formatting ? 'Formatting…' : 'Format'}
        </button>
        <button
          onClick={handleRun}
          disabled={executing || !sql.trim()}
          style={{
            padding: '5px 18px',
            background: sql.trim() && !executing ? C.accent : C.border,
            border: 'none',
            borderRadius: 5,
            color: sql.trim() && !executing ? '#fff' : '#6a6a8a',
            fontSize: 13,
            fontWeight: 600,
            cursor: sql.trim() && !executing ? 'pointer' : 'not-allowed',
          }}
        >
          {executing ? 'Running…' : 'Run  ⌘↵'}
        </button>
      </div>

      {/* Content row: history sidebar (optional) + editor/results */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {showHistory && (
          <HistoryPanel
            entries={history}
            onLoad={handleLoadFromHistory}
            onClear={() => setHistory([])}
          />
        )}

        {/* Editor + Results (stacked) */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ flex: '0 0 45%', borderBottom: `1px solid ${C.border}`, overflow: 'hidden' }}>
            <SqlEditor value={sql} onChange={handleSqlChange} onRun={handleRun} />
          </div>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <ResultGrid
              columns={execResult?.columns ?? []}
              rows={execResult?.rows ?? []}
              loading={executing}
              error={execErrMsg}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
