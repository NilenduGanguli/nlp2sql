import React, { useState, useMemo } from 'react'
import { useGraph } from '../hooks/useGraph'
import { GraphCanvas } from '../components/graph/GraphCanvas'
import { GraphControls } from '../components/graph/GraphControls'

export const GraphPage: React.FC = () => {
  const [limit, setLimit] = useState(200)
  const [tableSearch, setTableSearch] = useState('')
  const [resetKey, setResetKey] = useState(0)

  const { data, isLoading, error } = useGraph(limit)

  const filteredNodes = useMemo(() => {
    if (!data) return []
    const q = tableSearch.trim().toLowerCase()
    if (!q) return data.nodes
    return data.nodes.filter((n) => n.name.toLowerCase().includes(q))
  }, [data, tableSearch])

  const filteredEdges = useMemo(() => {
    if (!data) return []
    const nodeIds = new Set(filteredNodes.map((n) => n.id))
    return data.edges.filter((e) => nodeIds.has(e.from_id) && nodeIds.has(e.to_id))
  }, [data, filteredNodes])

  const handleReset = () => {
    setTableSearch('')
    setResetKey((k) => k + 1)
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <GraphControls
        limit={limit}
        onLimitChange={setLimit}
        tableSearch={tableSearch}
        onTableSearchChange={setTableSearch}
        onReset={handleReset}
      />

      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {isLoading ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: '#9090a8',
              fontSize: 14,
            }}
          >
            Loading knowledge graph…
          </div>
        ) : error ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: '#f87171',
              fontSize: 14,
            }}
          >
            Failed to load graph data
          </div>
        ) : (
          <GraphCanvas key={resetKey} nodes={filteredNodes} edges={filteredEdges} />
        )}
      </div>
    </div>
  )
}
