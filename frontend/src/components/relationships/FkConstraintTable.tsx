import React, { useRef, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef } from 'ag-grid-community'
import { useForeignKeys } from '../../hooks/useForeignKeys'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-alpine.css'

const COL_DEFS: ColDef[] = [
  { field: 'from_table', headerName: 'From Table', flex: 1, sortable: true, filter: true, resizable: true },
  { field: 'from_col', headerName: 'From Column', flex: 1, sortable: true, filter: true, resizable: true },
  { field: 'to_table', headerName: 'To Table', flex: 1, sortable: true, filter: true, resizable: true },
  { field: 'to_col', headerName: 'To Column', flex: 1, sortable: true, filter: true, resizable: true },
  { field: 'constraint_name', headerName: 'Constraint', flex: 1.5, sortable: true, filter: true, resizable: true },
]

export const FkConstraintTable: React.FC = () => {
  const { data: fkeys, isLoading, error } = useForeignKeys()
  const gridRef = useRef<AgGridReact>(null)

  const handleExportCsv = useCallback(() => {
    gridRef.current?.api?.exportDataAsCsv()
  }, [])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        border: '1px solid #3a3a5c',
        borderRadius: 8,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 14px',
          borderBottom: '1px solid #3a3a5c',
          background: '#2a2a3e',
          flexShrink: 0,
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 13, color: '#e0e0f0' }}>
          Foreign Key Constraints
          {fkeys && (
            <span style={{ fontSize: 11, color: '#9090a8', fontWeight: 400, marginLeft: 8 }}>
              ({fkeys.length} total)
            </span>
          )}
        </span>
        <button
          onClick={handleExportCsv}
          disabled={!fkeys?.length}
          style={{
            fontSize: 11,
            padding: '3px 10px',
            background: 'none',
            border: '1px solid #3a3a5c',
            borderRadius: 4,
            color: '#9090a8',
            cursor: fkeys?.length ? 'pointer' : 'not-allowed',
          }}
        >
          Export CSV
        </button>
      </div>

      {/* Grid */}
      <div style={{ height: 300 }}>
        {isLoading ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: '#9090a8',
              fontSize: 13,
            }}
          >
            Loading foreign keys…
          </div>
        ) : error ? (
          <div
            style={{
              padding: 16,
              color: '#f87171',
              fontSize: 13,
            }}
          >
            Failed to load foreign keys
          </div>
        ) : (
          <div className="ag-theme-alpine-dark" style={{ height: '100%', width: '100%' }}>
            <AgGridReact
              ref={gridRef}
              columnDefs={COL_DEFS}
              rowData={fkeys ?? []}
              pagination={true}
              paginationPageSize={50}
              enableCellTextSelection={true}
            />
          </div>
        )}
      </div>
    </div>
  )
}
