import React, { useState, useMemo } from 'react'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef } from 'ag-grid-community'
import { useTables, useTableDetail } from '../../hooks/useTables'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-alpine.css'

const COL_DEFS: ColDef[] = [
  { field: 'name', headerName: 'Column', flex: 1, sortable: true, filter: true },
  { field: 'data_type', headerName: 'Type', flex: 1, sortable: true, filter: true },
  { field: 'nullable', headerName: 'Nullable', width: 90, sortable: true,
    valueFormatter: (p) => (p.value === 'Y' ? 'Yes' : 'No') },
  { field: 'is_pk', headerName: 'PK', width: 60, sortable: true,
    valueFormatter: (p) => (p.value ? 'Y' : '') },
  { field: 'is_fk', headerName: 'FK', width: 60, sortable: true,
    valueFormatter: (p) => (p.value ? 'Y' : '') },
  { field: 'comments', headerName: 'Description', flex: 2, filter: true },
]

export const ColumnBrowser: React.FC = () => {
  const { data: tables } = useTables()
  const [search, setSearch] = useState('')
  const [selectedFqn, setSelectedFqn] = useState('')

  const { data: detail, isLoading } = useTableDetail(selectedFqn, !!selectedFqn)

  const tableList = useMemo(() => {
    if (!tables) return []
    const q = search.trim().toLowerCase()
    if (!q) return tables.slice(0, 200)
    return tables
      .filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          t.schema_name.toLowerCase().includes(q),
      )
      .slice(0, 200)
  }, [tables, search])

  return (
    <div
      style={{
        border: '1px solid #3a3a5c',
        borderRadius: 8,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '10px 14px',
          borderBottom: '1px solid #3a3a5c',
          background: '#2a2a3e',
          fontWeight: 600,
          fontSize: 13,
          color: '#e0e0f0',
        }}
      >
        Column Browser
      </div>

      <div style={{ padding: 14 }}>
        {/* Table selector */}
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 11, color: '#9090a8', display: 'block', marginBottom: 4 }}>
            Select a table to inspect columns
          </label>
          <input
            type="text"
            placeholder="Search tables…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: '100%',
              background: '#1e1e2e',
              border: '1px solid #3a3a5c',
              borderRadius: 4,
              padding: '5px 10px',
              color: '#e0e0f0',
              fontSize: 12,
              outline: 'none',
              marginBottom: 4,
              boxSizing: 'border-box',
            }}
            onFocus={(e) => (e.target.style.borderColor = '#7c6af7')}
            onBlur={(e) => (e.target.style.borderColor = '#3a3a5c')}
          />
          <select
            value={selectedFqn}
            onChange={(e) => setSelectedFqn(e.target.value)}
            size={5}
            style={{
              width: '100%',
              background: '#1e1e2e',
              border: '1px solid #3a3a5c',
              borderRadius: 4,
              color: '#e0e0f0',
              fontSize: 12,
              padding: '2px',
            }}
          >
            <option value="">— select a table —</option>
            {tableList.map((t) => (
              <option key={t.fqn} value={t.fqn}>
                {t.schema_name}.{t.name}
              </option>
            ))}
          </select>
        </div>

        {/* Columns grid */}
        {selectedFqn && (
          <div>
            {isLoading ? (
              <div style={{ color: '#9090a8', fontSize: 13, padding: '8px 0' }}>
                Loading columns…
              </div>
            ) : detail ? (
              <>
                <div style={{ fontSize: 11, color: '#9090a8', marginBottom: 6 }}>
                  {detail.columns.length} columns
                  {detail.comments && (
                    <span style={{ marginLeft: 8, color: '#6a6a8a', fontStyle: 'italic' }}>
                      — {detail.comments}
                    </span>
                  )}
                </div>
                <div style={{ height: 280 }}>
                  <div className="ag-theme-alpine-dark" style={{ height: '100%', width: '100%' }}>
                    <AgGridReact
                      columnDefs={COL_DEFS}
                      rowData={detail.columns}
                      enableCellTextSelection={true}
                      suppressMovableColumns={true}
                    />
                  </div>
                </div>
              </>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
