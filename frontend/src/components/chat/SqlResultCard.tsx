import React, { useRef, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef } from 'ag-grid-community'
import type { QueryResult } from '../../types'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-alpine.css'

interface SqlResultCardProps {
  result: QueryResult
  onOpenInEditor?: (sql: string) => void
}

export const SqlResultCard: React.FC<SqlResultCardProps> = ({ result, onOpenInEditor }) => {
  const gridRef = useRef<AgGridReact>(null)
  const [copied, setCopied] = React.useState(false)

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
  }, [result.sql])

  const handleExportCsv = useCallback(() => {
    gridRef.current?.api?.exportDataAsCsv()
  }, [])

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
        <div style={{ flex: 1 }} />
        {onOpenInEditor && (
          <button
            onClick={() => onOpenInEditor(result.sql)}
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
          <div style={{ height: Math.min(200, 38 + result.rows.length * 36), width: '100%' }}>
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
