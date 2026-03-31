import React, { useRef, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef } from 'ag-grid-community'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-alpine.css'

interface ResultGridProps {
  columns: string[]
  rows: unknown[][]
  loading?: boolean
  error?: string
}

export const ResultGrid: React.FC<ResultGridProps> = ({ columns, rows, loading, error }) => {
  const gridRef = useRef<AgGridReact>(null)

  const colDefs: ColDef[] = columns.map((col) => ({
    field: col,
    headerName: col,
    sortable: true,
    filter: true,
    resizable: true,
    minWidth: 80,
    flex: 1,
  }))

  const rowData = rows.map((row) => {
    const obj: Record<string, unknown> = {}
    columns.forEach((col, i) => {
      obj[col] = (row as unknown[])[i]
    })
    return obj
  })

  const handleExportCsv = useCallback(() => {
    gridRef.current?.api?.exportDataAsCsv()
  }, [])

  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flex: 1,
          color: '#9090a8',
          fontSize: 14,
        }}
      >
        Executing query…
      </div>
    )
  }

  if (error) {
    return (
      <div
        style={{
          padding: '16px',
          color: '#f87171',
          background: 'rgba(248,113,113,0.08)',
          borderRadius: 6,
          fontSize: 13,
          margin: '12px 16px',
          border: '1px solid rgba(248,113,113,0.25)',
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Execution Error</div>
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, monospace' }}>
          {error}
        </pre>
      </div>
    )
  }

  if (columns.length === 0) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flex: 1,
          color: '#5a5a7a',
          fontSize: 13,
        }}
      >
        Run a query to see results
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '6px 16px',
          borderBottom: '1px solid #3a3a5c',
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 12, color: '#9090a8' }}>
          {rows.length.toLocaleString()} row{rows.length !== 1 ? 's' : ''}
        </span>
        <button
          onClick={handleExportCsv}
          style={{
            fontSize: 11,
            padding: '3px 10px',
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

      {/* Grid */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <div className="ag-theme-alpine-dark" style={{ height: '100%', width: '100%' }}>
          <AgGridReact
            ref={gridRef}
            columnDefs={colDefs}
            rowData={rowData}
            pagination={true}
            paginationPageSize={100}
            enableCellTextSelection={true}
            suppressMovableColumns={false}
          />
        </div>
      </div>
    </div>
  )
}
