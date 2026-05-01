import React, { useRef, useCallback, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef } from 'ag-grid-community'
import type { QueryResult, ValueMapping } from '../../types'
import { useChatStore } from '../../store/chatStore'
import { RefineButton } from './RefineButton'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-alpine.css'


// ──────────────────────────────────────────────────────────────────────────
// Value-mappings panel (Phase 3 polish)
// ──────────────────────────────────────────────────────────────────────────
//
// Surfaces every WHERE-clause literal the SQL validator silently rewrote
// from a near-miss to the real DB value (e.g. 'active' → 'A' on STATUS).
// Defaults to collapsed; the chip on the chevron is the count.
const ValueMappingsPanel: React.FC<{ mappings: ValueMapping[] }> = ({ mappings }) => {
  const [open, setOpen] = useState(false)
  if (!mappings || mappings.length === 0) return null

  return (
    <div
      style={{
        background: 'rgba(34, 211, 238, 0.04)',
        borderTop: '1px solid #3a3a5c',
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          padding: '6px 14px',
          background: 'none',
          border: 'none',
          color: '#22d3ee',
          fontSize: 11,
          fontWeight: 500,
          cursor: 'pointer',
          textAlign: 'left',
        }}
        title="Literal auto-fixes applied by the validator before execution"
      >
        <span style={{ display: 'inline-block', transition: 'transform 0.15s', transform: open ? 'rotate(90deg)' : 'rotate(0)' }}>
          ›
        </span>
        <span>
          Value mappings — {mappings.length} literal{mappings.length === 1 ? '' : 's'} auto-fixed
        </span>
      </button>
      {open && (
        <div style={{ padding: '6px 14px 10px 28px' }}>
          <div style={{ fontSize: 10, color: '#9090a8', marginBottom: 6 }}>
            These literals didn&rsquo;t exactly match cached DB values, so the validator
            rewrote them to the closest match before execution.
          </div>
          {mappings.map((m, i) => (
            <div
              key={`${m.table}-${m.column}-${m.original}-${i}`}
              style={{
                display: 'flex',
                alignItems: 'baseline',
                gap: 8,
                fontSize: 11,
                fontFamily: 'ui-monospace, Consolas, monospace',
                marginTop: 4,
                color: '#c0c0d8',
              }}
            >
              <span style={{ color: '#9090a8' }}>{m.table}.{m.column}:</span>
              <span style={{ color: '#f87171' }}>'{m.original}'</span>
              <span style={{ color: '#9090a8' }}>→</span>
              <span style={{ color: '#4ade80' }}>'{m.mapped}'</span>
              {m.reason && (
                <span style={{ color: '#9090a8', fontStyle: 'italic', fontSize: 10 }}>
                  ({m.reason})
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface SqlResultCardProps {
  result: QueryResult
  onOpenInEditor?: (sql: string) => void
}

export const SqlResultCard: React.FC<SqlResultCardProps> = ({ result, onOpenInEditor }) => {
  const gridRef = useRef<AgGridReact>(null)
  const [copied, setCopied] = React.useState(false)
  const [savedAsPattern, setSavedAsPattern] = React.useState(false)
  const emitSignal = useChatStore((s) => s.emitSignal)
  const branchConversation = useChatStore((s) => s.branchConversation)

  const colDefs: ColDef[] = result.columns.map((col) => ({
    field: col,
    headerName: col,
    sortable: true,
    filter: true,
    resizable: true,
    minWidth: 80,
    flex: 1,
  }))

  const rowData = result.rows.map((row) => {
    const obj: Record<string, unknown> = {}
    result.columns.forEach((col, i) => {
      obj[col] = (row as unknown[])[i]
    })
    return obj
  })

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(result.sql)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
    void emitSignal('copied_sql', result.sql, {})
  }, [result.sql, emitSignal])

  const handleOpenInEditor = useCallback(() => {
    if (!onOpenInEditor) return
    onOpenInEditor(result.sql)
    void emitSignal('opened_in_editor', result.sql, {})
  }, [onOpenInEditor, result.sql, emitSignal])

  const handleExportCsv = useCallback(() => {
    gridRef.current?.api?.exportDataAsCsv()
  }, [])

  const handleRefine = useCallback(() => {
    window.dispatchEvent(new CustomEvent('chat-prefill-input', { detail: { query: 'refine: ' } }))
  }, [])

  const handleBranch = useCallback(() => {
    branchConversation()
  }, [branchConversation])

  const handleSaveAsPattern = useCallback(async () => {
    try {
      const res = await fetch('/api/patterns/manual-promote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql: result.sql, user_input: result.summary || '' }),
      })
      if (res.ok) setSavedAsPattern(true)
    } catch (err) {
      console.warn('manual-promote failed:', err)
    }
  }, [result.sql, result.summary])

  return (
    <div
      style={{
        background: '#2a2a3e',
        border: '1px solid #3a3a5c',
        borderRadius: 8,
        overflow: 'hidden',
        maxWidth: '100%',
      }}
    >
      {/* Summary */}
      {result.summary && (
        <div
          style={{
            padding: '10px 14px 8px',
            fontSize: 13,
            color: '#c0c0d8',
            borderBottom: '1px solid #3a3a5c',
          }}
        >
          {result.summary}
        </div>
      )}

      {/* SQL block */}
      <div style={{ position: 'relative', background: '#1a1a2e' }}>
        <pre
          style={{
            margin: 0,
            padding: '10px 40px 10px 14px',
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: 12,
            color: '#a5b4fc',
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
            maxHeight: 160,
          }}
        >
          {result.sql}
        </pre>
        <button
          onClick={handleCopy}
          title="Copy SQL"
          style={{
            position: 'absolute',
            top: 6,
            right: 8,
            background: 'rgba(60,60,80,0.8)',
            border: '1px solid #4a4a6c',
            borderRadius: 4,
            color: copied ? '#4ade80' : '#9090a8',
            fontSize: 11,
            padding: '2px 8px',
            cursor: 'pointer',
          }}
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>

      {/* Value mappings — auto-fixes applied by the validator (Phase 2/3) */}
      <ValueMappingsPanel mappings={result.value_mappings ?? []} />

      {/* Metrics row */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '6px 14px',
          borderBottom: result.rows.length > 0 ? '1px solid #3a3a5c' : 'none',
          borderTop: '1px solid #3a3a5c',
        }}
      >
        <span style={{ fontSize: 11, color: '#9090a8' }}>
          {result.total_rows.toLocaleString()} row{result.total_rows !== 1 ? 's' : ''}
        </span>
        <span style={{ fontSize: 11, color: '#9090a8' }}>
          {result.execution_time_ms.toFixed(0)} ms
        </span>
        <span
          style={{
            fontSize: 10,
            padding: '1px 6px',
            borderRadius: 999,
            background: 'rgba(99,102,241,0.15)',
            color: '#818cf8',
            fontWeight: 600,
          }}
        >
          {result.data_source}
        </span>
        {result.validation_errors.length > 0 && (
          <span
            style={{
              fontSize: 10,
              padding: '1px 6px',
              borderRadius: 999,
              background: 'rgba(248,113,113,0.15)',
              color: '#f87171',
            }}
          >
            {result.validation_errors.length} warning{result.validation_errors.length > 1 ? 's' : ''}
          </span>
        )}
        {result.value_mappings && result.value_mappings.length > 0 && (
          <span
            style={{
              fontSize: 10,
              padding: '1px 6px',
              borderRadius: 999,
              background: 'rgba(34,211,238,0.15)',
              color: '#22d3ee',
            }}
            title="Literals auto-fixed to match real DB values — see panel below SQL"
          >
            {result.value_mappings.length} value{result.value_mappings.length > 1 ? 's' : ''} fixed
          </span>
        )}
        <div style={{ flex: 1 }} />
        <RefineButton
          onRefine={handleRefine}
          onBranch={handleBranch}
          onSaveAsPattern={handleSaveAsPattern}
          saved={savedAsPattern}
        />
        {onOpenInEditor && (
          <button
            onClick={handleOpenInEditor}
            style={{
              fontSize: 11,
              padding: '3px 10px',
              background: 'rgba(124,106,247,0.15)',
              border: '1px solid rgba(124,106,247,0.4)',
              borderRadius: 4,
              color: '#7c6af7',
              cursor: 'pointer',
              fontWeight: 500,
            }}
          >
            Open in Editor
          </button>
        )}
      </div>

      {/* AG Grid results */}
      {result.rows.length > 0 && (
        <div>
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              padding: '4px 14px',
              borderBottom: '1px solid #3a3a5c',
            }}
          >
            <button
              onClick={handleExportCsv}
              style={{
                fontSize: 11,
                padding: '2px 8px',
                background: 'none',
                border: '1px solid #3a3a5c',
                borderRadius: 4,
                color: '#9090a8',
                cursor: 'pointer',
              }}
            >
              Export CSV
            </button>
          </div>
          <div style={{ height: Math.max(200, Math.min(500, 48 + result.rows.length * 40)), width: '100%' }}>
            <div className="ag-theme-alpine-dark" style={{ height: '100%', width: '100%' }}>
              <AgGridReact
                ref={gridRef}
                columnDefs={colDefs}
                rowData={rowData}
                pagination={true}
                paginationPageSize={100}
                enableCellTextSelection={true}
                suppressMovableColumns={true}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
