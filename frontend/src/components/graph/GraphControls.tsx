import React from 'react'

interface GraphControlsProps {
  limit: number
  onLimitChange: (n: number) => void
  tableSearch: string
  onTableSearchChange: (s: string) => void
  onReset: () => void
}

export const GraphControls: React.FC<GraphControlsProps> = ({
  limit,
  onLimitChange,
  tableSearch,
  onTableSearchChange,
  onReset,
}) => {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 12,
        padding: '8px 16px',
        borderBottom: '1px solid #3a3a5c',
        background: '#2a2a3e',
        flexShrink: 0,
      }}
    >
      {/* Table search */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11, color: '#9090a8', fontWeight: 500 }}>Tables:</span>
        <input
          type="text"
          value={tableSearch}
          onChange={(e) => onTableSearchChange(e.target.value)}
          placeholder="Filter by name…"
          style={{
            padding: '3px 8px',
            background: '#1e1e2e',
            border: '1px solid #3a3a5c',
            borderRadius: 4,
            color: '#e0e0f0',
            fontSize: 11,
            width: 140,
            outline: 'none',
          }}
        />
        {tableSearch && (
          <button
            onClick={() => onTableSearchChange('')}
            style={{
              background: 'none',
              border: 'none',
              color: '#9090a8',
              fontSize: 13,
              cursor: 'pointer',
              padding: '0 2px',
              lineHeight: 1,
            }}
            title="Clear filter"
          >
            ×
          </button>
        )}
      </div>

      {/* Limit slider */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 11, color: '#9090a8', fontWeight: 500 }}>Max nodes:</span>
        <input
          type="range"
          min={50}
          max={200}
          step={10}
          value={limit}
          onChange={(e) => onLimitChange(Number(e.target.value))}
          style={{ width: 100, accentColor: '#7c6af7' }}
        />
        <span style={{ fontSize: 11, color: '#c0c0d8', minWidth: 28 }}>{limit}</span>
      </div>

      {/* Reset button */}
      <button
        onClick={onReset}
        style={{
          padding: '3px 10px',
          background: 'none',
          border: '1px solid #3a3a5c',
          borderRadius: 4,
          color: '#9090a8',
          fontSize: 11,
          cursor: 'pointer',
          marginLeft: 'auto',
        }}
      >
        Reset View
      </button>
    </div>
  )
}
