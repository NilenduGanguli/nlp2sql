import React, { useState, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import type { QueryResult } from '../../types'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-alpine.css'

interface ResultMessageProps {
  result: QueryResult
  onOpenInEditor: (sql: string) => void
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text).catch(() => {
    const el = document.createElement('textarea')
    el.value = text
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  })
}

export const ResultMessage: React.FC<ResultMessageProps> = ({ result, onOpenInEditor }) => {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(() => {
    copyToClipboard(result.sql)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [result.sql])

  const columnDefs = result.columns.map((col) => ({
    headerName: col,
    field: col,
    sortable: true,
    filter: true,
    resizable: true,
    minWidth: 80,
  }))

  const rowData = result.rows.map((row) => {
    const obj: Record<string, unknown> = {}
    result.columns.forEach((col, i) => {
      obj[col] = row[i]
    })
    return obj
  })

  return (
    <div
      style={{
        background: '#2a2a3e',
        borderRadius: 10,
        border: '1px solid #3a3a5c',
        overflow: 'hidden',
        maxWidth: 900,
      }}
    >
      {/* Summary */}
      {result.summary && (
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #3a3a5c' }}>
          <p style={{ color: '#e0e0f0', margin: 0, lineHeight: 1.5 }}>{result.summary}</p>
          {result.explanation && result.explanation !== result.summary && (
            <p style={{ color: '#9090a8', margin: '4px 0 0', fontSize: 12, lineHeight: 1.5 }}>
              {result.explanation}
            </p>
          )}
        </div>
      )}

      {/* SQL block */}
      {result.sql && (
        <div style={{ position: 'relative', borderBottom: '1px solid #3a3a5c' }}>
          <pre
            style={{
              margin: 0,
              padding: '12px 16px',
              paddingRight: 80,
              background: '#1e1e2e',
              color: '#a0c4ff',
              fontSize: 12,
              lineHeight: 1.6,
              overflowX: 'auto',
              fontFamily: "'Fira Code', 'Cascadia Code', 'Consolas', monospace",
            }}
          >
            {result.sql}
          </pre>
          <div
            style={{
              position: 'absolute',
              top: 8,
              right: 8,
              display: 'flex',
              gap: 4,
            }}
          >
            <button
              onClick={handleCopy}
              style={{
                background: copied ? 'rgba(74,222,128,0.15)' : 'rgba(124,106,247,0.15)',
                border: '1px solid',
                borderColor: copied ? '#4ade80' : '#7c6af7',
                color: copied ? '#4ade80' : '#7c6af7',
                borderRadius: 4,
                padding: '2px 8px',
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
            <button
              onClick={() => onOpenInEditor(result.sql)}
              style={{
                background: 'rgba(124,106,247,0.15)',
                border: '1px solid #7c6af7',
                color: '#7c6af7',
                borderRadius: 4,
                padding: '2px 8px',
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              Open in Editor
            </button>
          </div>
        </div>
      )}

      {/* Results grid */}
      {result.columns.length > 0 && (
        <div>
          <div
            style={{
              padding: '8px 16px',
              background: '#232336',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span style={{ color: '#9090a8', fontSize: 12 }}>
              {result.total_rows.toLocaleString()} row{result.total_rows !== 1 ? 's' : ''}
              {result.execution_time_ms > 0 && (
                <> &middot; {result.execution_time_ms.toFixed(0)}ms</>
              )}
              {result.data_source && (
                <> &middot; <span style={{ color: '#7c6af7' }}>{result.data_source}</span></>
              )}
            </span>
            {result.schema_context_tables?.length > 0 && (
              <span style={{ color: '#9090a8', fontSize: 11 }}>
                Context: {result.schema_context_tables.slice(0, 5).join(', ')}
                {result.schema_context_tables.length > 5 &&
                  ` +${result.schema_context_tables.length - 5}`}
              </span>
            )}
          </div>

          <div
            className="ag-theme-alpine-dark"
            style={{ height: Math.min(400, rowData.length * 42 + 60) }}
          >
            <AgGridReact
              columnDefs={columnDefs}
              rowData={rowData}
              pagination={rowData.length > 50}
              paginationPageSize={50}
              enableCellTextSelection
              suppressMovableColumns={false}
              defaultColDef={{
                sortable: true,
                filter: true,
                resizable: true,
              }}
            />
          </div>
        </div>
      )}

      {/* Validation errors */}
      {result.validation_errors?.length > 0 && (
        <div
          style={{
            padding: '8px 16px',
            background: 'rgba(248,113,113,0.1)',
            borderTop: '1px solid rgba(248,113,113,0.3)',
          }}
        >
          <div style={{ color: '#f87171', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
            Validation warnings:
          </div>
          {result.validation_errors.map((e, i) => (
            <div key={i} style={{ color: '#f87171', fontSize: 12 }}>
              • {e}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
