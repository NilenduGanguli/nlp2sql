import React, { useMemo, useState, useCallback, useRef, useEffect } from 'react'
import type { GraphNode, GraphEdge } from '../../types'

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

const NODE_COLOR = '#5b7cf7'  // single colour — no more group colours

function nodeRadius(node: GraphNode, totalNodes: number): number {
  const scaleFactor = Math.max(0.35, Math.min(1.0, 10 / Math.sqrt(totalNodes)))
  const base = node.importance_rank
    ? Math.max(5, 22 - (node.importance_rank - 1) * 1.3)
    : 10
  return base * scaleFactor
}

function labelFontSize(totalNodes: number): number {
  // from 11px at 10 nodes down to 7px at 150+ nodes
  return Math.max(7, Math.min(11, 200 / totalNodes + 5))
}

// Expand virtual canvas so nodes have more breathing room for large graphs
function virtualCanvasSize(nodeCount: number): { w: number; h: number } {
  const side = Math.max(900, Math.round(Math.sqrt(nodeCount) * 125))
  return { w: side, h: Math.round(side * 0.65) }
}

interface NodePos { id: string; x: number; y: number }

function runForceLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  width: number,
  height: number,
): NodePos[] {
  if (nodes.length === 0) return []

  const cx = width / 2
  const cy = height / 2
  const r = Math.min(width, height) * 0.40

  const positions = nodes.map((n, i) => {
    const angle = (2 * Math.PI * i) / nodes.length
    return { id: n.id, x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle), vx: 0, vy: 0 }
  })

  const posMap = new Map(positions.map((p) => [p.id, p]))
  const k = Math.sqrt((width * height) / Math.max(nodes.length, 1))
  const ITERATIONS = nodes.length > 80 ? 80 : 150

  for (let iter = 0; iter < ITERATIONS; iter++) {
    const cooling = 1 - iter / ITERATIONS

    // Repulsion
    for (let i = 0; i < positions.length; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        const pi = positions[i]
        const pj = positions[j]
        const dx = pi.x - pj.x
        const dy = pi.y - pj.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01
        const force = (k * k) / dist
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        pi.vx += fx; pi.vy += fy; pj.vx -= fx; pj.vy -= fy
      }
    }

    // Spring attraction
    for (const edge of edges) {
      const a = posMap.get(edge.from_id)
      const b = posMap.get(edge.to_id)
      if (!a || !b) continue
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01
      const force = (dist * dist) / k
      const fx = (dx / dist) * force * 0.5
      const fy = (dy / dist) * force * 0.5
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
    }

    // Apply with cooling
    const maxSpeed = Math.max(2, 15 * cooling)
    for (const p of positions) {
      const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy)
      if (speed > maxSpeed) { p.vx = (p.vx / speed) * maxSpeed; p.vy = (p.vy / speed) * maxSpeed }
      p.x = Math.max(50, Math.min(width - 50, p.x + p.vx))
      p.y = Math.max(50, Math.min(height - 50, p.y + p.vy))
      p.vx *= 0.75; p.vy *= 0.75
    }
  }

  return positions.map((p) => ({ id: p.id, x: p.x, y: p.y }))
}

// ---------------------------------------------------------------------------
// Tooltip component (fixed-position HTML, never inside SVG)
// ---------------------------------------------------------------------------

type TooltipData =
  | {
      kind: 'node'
      node: GraphNode
      degree: number
    }
  | {
      kind: 'edge'
      edge: GraphEdge
      fromName: string
      toName: string
    }

function relTypeLabel(rel: string) {
  if (rel === 'JOIN_PATH') return 'Join path'
  if (rel === 'HAS_FOREIGN_KEY') return 'Foreign key'
  if (rel === 'SIMILAR_TO') return 'Similar to'
  return rel
}

function sourceLabel(src: string) {
  if (src === 'fk_constraint') return 'FK constraint'
  if (src === 'llm_inferred') return 'LLM-inferred'
  return src
}

const TooltipBox: React.FC<{ data: TooltipData; x: number; y: number }> = ({ data, x, y }) => {
  const adjustedX = x + 260 > window.innerWidth ? x - 270 : x + 14
  const adjustedY = y + 180 > window.innerHeight ? y - 160 : y - 10

  return (
    <div
      style={{
        position: 'fixed',
        left: adjustedX,
        top: adjustedY,
        background: '#1e1e35',
        border: '1px solid #4a4a6c',
        borderRadius: 10,
        padding: '12px 15px',
        fontSize: 12,
        color: '#e0e0f0',
        boxShadow: '0 6px 24px rgba(0,0,0,0.55)',
        pointerEvents: 'none',
        zIndex: 200,
        maxWidth: 320,
        lineHeight: 1.55,
      }}
    >
      {data.kind === 'node' ? (
        <>
          <div style={{ fontWeight: 700, fontSize: 13, color: '#d0d8ff', marginBottom: 6 }}>
            {data.node.name}
          </div>
          <div style={{ color: '#8888aa', marginBottom: 4 }}>
            Schema: <span style={{ color: '#c0c0d8' }}>{data.node.schema_name}</span>
          </div>
          {data.node.importance_rank != null && (
            <div style={{ color: '#8888aa', marginBottom: 4 }}>
              Rank:{' '}
              <span style={{ color: '#a5b4fc' }}>#{data.node.importance_rank}</span>
            </div>
          )}
          {data.node.row_count != null && (
            <div style={{ color: '#8888aa', marginBottom: 4 }}>
              Rows:{' '}
              <span style={{ color: '#c0c0d8' }}>{data.node.row_count.toLocaleString()}</span>
            </div>
          )}
          <div style={{ color: '#8888aa', marginBottom: 4 }}>
            Connections: <span style={{ color: '#c0c0d8' }}>{data.degree}</span>
          </div>
          {data.node.comments && (
            <div
              style={{
                marginTop: 8,
                paddingTop: 8,
                borderTop: '1px solid #3a3a5c',
                color: '#9090a8',
                fontStyle: 'italic',
                fontSize: 11,
                wordBreak: 'break-word',
              }}
            >
              {data.node.comments.length > 140
                ? data.node.comments.slice(0, 137) + '…'
                : data.node.comments}
            </div>
          )}
        </>
      ) : (
        <>
          <div
            style={{ fontWeight: 700, fontSize: 12, color: '#a5b4fc', marginBottom: 8 }}
          >
            {relTypeLabel(data.edge.rel_type)}
          </div>

          {/* Table pair */}
          <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ color: '#c0c0d8', fontWeight: 500, fontSize: 11 }}>{data.fromName}</span>
            <span style={{ color: '#5a5a8a', fontSize: 13 }}>→</span>
            <span style={{ color: '#c0c0d8', fontWeight: 500, fontSize: 11 }}>{data.toName}</span>
          </div>

          {/* Join column pairs */}
          {data.edge.join_columns.length > 0 && (
            <div
              style={{
                marginBottom: 8,
                paddingBottom: 8,
                borderBottom: '1px solid #2a2a4a',
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              {data.edge.join_columns.map((jc, i) => (
                <div key={i}>
                  {/* Column pair row */}
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 5, flexWrap: 'wrap', marginBottom: 2 }}>
                    <span style={{ color: '#7dd3fc', fontFamily: 'monospace', fontSize: 11, fontWeight: 600 }}>
                      {jc.from_col}
                    </span>
                    {jc.from_col_type && (
                      <span style={{ color: '#4a4a7a', fontSize: 10 }}>({jc.from_col_type})</span>
                    )}
                    <span style={{ color: '#5a5a8a', fontSize: 11 }}>=</span>
                    <span style={{ color: '#7dd3fc', fontFamily: 'monospace', fontSize: 11, fontWeight: 600 }}>
                      {jc.to_col}
                    </span>
                    {jc.to_col_type && (
                      <span style={{ color: '#4a4a7a', fontSize: 10 }}>({jc.to_col_type})</span>
                    )}
                  </div>

                  {/* Constraint name */}
                  {jc.constraint_name && (
                    <div style={{ color: '#6a6aaa', fontSize: 10, marginBottom: 2 }}>
                      FK: <span style={{ color: '#9090c8', fontFamily: 'monospace' }}>{jc.constraint_name}</span>
                      {jc.on_delete_action && jc.on_delete_action !== 'NO ACTION' && (
                        <span style={{ marginLeft: 6, color: '#f9a84d' }}>
                          ON DELETE {jc.on_delete_action}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Source column comment */}
                  {jc.from_col_comments && (
                    <div style={{ color: '#7a7a98', fontSize: 10, fontStyle: 'italic', marginBottom: 1 }}>
                      ↑ {jc.from_col_comments.length > 80 ? jc.from_col_comments.slice(0, 77) + '…' : jc.from_col_comments}
                    </div>
                  )}
                  {/* Target column comment */}
                  {jc.to_col_comments && jc.to_col_comments !== jc.from_col_comments && (
                    <div style={{ color: '#7a7a98', fontSize: 10, fontStyle: 'italic' }}>
                      ↓ {jc.to_col_comments.length > 80 ? jc.to_col_comments.slice(0, 77) + '…' : jc.to_col_comments}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Metadata row */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {data.edge.join_type && (
              <span style={{ fontSize: 10, color: '#8888aa' }}>
                Join: <span style={{ color: '#c0c0d8' }}>{data.edge.join_type}</span>
              </span>
            )}
            {data.edge.cardinality && (
              <span style={{ fontSize: 10, color: '#8888aa' }}>
                Cardinality: <span style={{ color: '#c0c0d8' }}>{data.edge.cardinality}</span>
              </span>
            )}
            <span style={{ fontSize: 10, color: '#8888aa' }}>
              Source:{' '}
              <span
                style={{
                  color: data.edge.source === 'llm_inferred' ? '#f9a84d'
                    : data.edge.source === 'precomputed' ? '#4ade80'
                    : '#a0a0c0',
                }}
              >
                {sourceLabel(data.edge.source)}
              </span>
            </span>
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface GraphCanvasProps {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

interface Transform { x: number; y: number; scale: number }

export const GraphCanvas: React.FC<GraphCanvasProps> = ({ nodes, edges }) => {
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, scale: 1 })
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null)
  const [mousePos, setMousePos] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  const [panning, setPanning] = useState(false)
  const isPanning = useRef(false)
  const lastPan = useRef({ x: 0, y: 0 })
  const containerRef = useRef<HTMLDivElement>(null)

  // Synchronously compute canvas dims + layout together so positions use correct dims
  const { svgW, svgH, positions } = useMemo(() => {
    const { w, h } = virtualCanvasSize(nodes.length)
    return { svgW: w, svgH: h, positions: runForceLayout(nodes, edges, w, h) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes.map((n) => n.id).join(','), edges.map((e) => e.id).join(',')])

  // Reset view to fit graph when nodes change
  useEffect(() => {
    setTransform({ x: 0, y: 0, scale: 1 })
  }, [nodes.map((n) => n.id).join(',')])

  const posMap = useMemo(() => new Map(positions.map((p) => [p.id, p])), [positions])
  const nodeMap = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])

  // Degree map — used in node tooltip
  const degreeMap = useMemo(() => {
    const m = new Map<string, number>()
    for (const e of edges) {
      m.set(e.from_id, (m.get(e.from_id) ?? 0) + 1)
      m.set(e.to_id, (m.get(e.to_id) ?? 0) + 1)
    }
    return m
  }, [edges])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    setMousePos({ x: e.clientX, y: e.clientY })
    if (!isPanning.current) return
    const dx = e.clientX - lastPan.current.x
    const dy = e.clientY - lastPan.current.y
    lastPan.current = { x: e.clientX, y: e.clientY }
    setTransform((t) => ({ ...t, x: t.x + dx, y: t.y + dy }))
  }, [])

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.12 : 0.9
    setTransform((t) => ({ ...t, scale: Math.max(0.15, Math.min(8, t.scale * factor)) }))
  }, [])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    isPanning.current = true
    setPanning(true)
    lastPan.current = { x: e.clientX, y: e.clientY }
  }, [])

  const handleMouseUp = useCallback(() => {
    isPanning.current = false
    setPanning(false)
  }, [])

  const handleReset = useCallback(() => setTransform({ x: 0, y: 0, scale: 1 }), [])

  if (nodes.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#5a5a7a', fontSize: 14 }}>
        No graph data available. Build the knowledge graph first.
      </div>
    )
  }

  const n = nodes.length
  const fontSize = labelFontSize(n)
  // Show all labels when ≤ 40 nodes; otherwise only hovered + neighbors
  const showAllLabels = n <= 40
  const hoveredNeighborIds = hoveredNode
    ? new Set(
        edges
          .filter((e) => e.from_id === hoveredNode || e.to_id === hoveredNode)
          .map((e) => (e.from_id === hoveredNode ? e.to_id : e.from_id)),
      )
    : null
  const hoveredEdgeSet = hoveredNode
    ? new Set(
        edges.filter((e) => e.from_id === hoveredNode || e.to_id === hoveredNode).map((e) => e.id),
      )
    : null

  // Build tooltip data
  let tooltipData: TooltipData | null = null
  if (hoveredNode) {
    const n = nodeMap.get(hoveredNode)
    if (n) tooltipData = { kind: 'node', node: n, degree: degreeMap.get(hoveredNode) ?? 0 }
  } else if (hoveredEdge) {
    const edge = edges.find((e) => e.id === hoveredEdge)
    if (edge) {
      const fromNode = nodeMap.get(edge.from_id)
      const toNode = nodeMap.get(edge.to_id)
      if (fromNode && toNode) {
        tooltipData = {
          kind: 'edge',
          edge,
          fromName: fromNode.name,
          toName: toNode.name,
        }
      }
    }
  }

  return (
    <div
      ref={containerRef}
      style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }}
      onMouseMove={handleMouseMove}
    >
      {/* HUD */}
      <div style={{ position: 'absolute', top: 12, left: 12, zIndex: 10, fontSize: 11, color: '#5a5a7a', pointerEvents: 'none' }}>
        {n} nodes · {edges.length} edges
        {n > 40 && (
          <span style={{ marginLeft: 6, color: '#4a4a6a' }}>· hover to see labels</span>
        )}
      </div>

      {/* Controls overlay */}
      <div style={{ position: 'absolute', top: 10, right: 12, zIndex: 10, display: 'flex', gap: 6 }}>
        <button
          onClick={() => setTransform((t) => ({ ...t, scale: Math.min(8, t.scale * 1.3) }))}
          title="Zoom in"
          style={btnStyle}
        >+</button>
        <button
          onClick={() => setTransform((t) => ({ ...t, scale: Math.max(0.15, t.scale * 0.77) }))}
          title="Zoom out"
          style={btnStyle}
        >−</button>
        <button onClick={handleReset} title="Reset view" style={btnStyle}>⊡</button>
      </div>

      <svg
        width="100%"
        height="100%"
        style={{ cursor: panning ? 'grabbing' : 'grab', background: '#12121f' }}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={(e) => {
          if (isPanning.current) {
            const dx = e.clientX - lastPan.current.x
            const dy = e.clientY - lastPan.current.y
            lastPan.current = { x: e.clientX, y: e.clientY }
            setTransform((t) => ({ ...t, x: t.x + dx, y: t.y + dy }))
          }
        }}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        viewBox={`0 0 ${svgW} ${svgH}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <marker
            id="arrow"
            markerWidth="8"
            markerHeight="8"
            refX="6"
            refY="3"
            orient="auto"
            markerUnits="userSpaceOnUse"
          >
            <path d="M0,0 L0,6 L8,3 z" fill="#4a4a7c" />
          </marker>
        </defs>

        <g transform={`translate(${transform.x}, ${transform.y}) scale(${transform.scale})`}>
          {/* ── Edges ── */}
          {edges.map((edge) => {
            const a = posMap.get(edge.from_id)
            const b = posMap.get(edge.to_id)
            if (!a || !b) return null
            const highlighted = hoveredEdgeSet ? hoveredEdgeSet.has(edge.id) : (hoveredEdge === edge.id)
            const dimmed = (hoveredNode && !highlighted) || (hoveredEdge && hoveredEdge !== edge.id)

            return (
              <g key={edge.id}>
                {/* Visible line */}
                <line
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={highlighted ? '#7c6af7' : hoveredEdge === edge.id ? '#a5b4fc' : '#383868'}
                  strokeWidth={highlighted || hoveredEdge === edge.id ? 2.5 : 1}
                  strokeOpacity={dimmed ? 0.15 : 0.75}
                  style={{ pointerEvents: 'none' }}
                />
                {/* Wide invisible hit area */}
                <line
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke="transparent"
                  strokeWidth={Math.max(12, 12 / transform.scale)}
                  style={{ cursor: 'crosshair' }}
                  onMouseEnter={() => { if (!isPanning.current) { setHoveredEdge(edge.id); setHoveredNode(null) } }}
                  onMouseLeave={() => setHoveredEdge(null)}
                />
              </g>
            )
          })}

          {/* ── Nodes ── */}
          {positions.map((pos) => {
            const node = nodeMap.get(pos.id)
            if (!node) return null
            const rad = nodeRadius(node, n)
            const isHovered = hoveredNode === node.id
            const isNeighbor = hoveredNeighborIds?.has(node.id) ?? false
            const dimmed = hoveredNode !== null && !isHovered && !isNeighbor
            const showLabel = showAllLabels || isHovered || isNeighbor

            return (
              <g
                key={node.id}
                transform={`translate(${pos.x}, ${pos.y})`}
                onMouseEnter={() => { if (!isPanning.current) { setHoveredNode(node.id); setHoveredEdge(null) } }}
                onMouseLeave={() => setHoveredNode(null)}
                style={{ cursor: 'pointer' }}
              >
                {/* Glow ring on hover */}
                {isHovered && (
                  <circle
                    r={rad + 5}
                    fill="none"
                    stroke="#7c6af7"
                    strokeWidth={2}
                    strokeOpacity={0.5}
                  />
                )}
                {isNeighbor && !isHovered && (
                  <circle r={rad + 3} fill="none" stroke="#4a4aaa" strokeWidth={1} strokeOpacity={0.55} />
                )}
                <circle
                  r={isHovered ? rad + 2 : rad}
                  fill={isHovered ? '#7c6af7' : isNeighbor ? '#6070d8' : NODE_COLOR}
                  fillOpacity={dimmed ? 0.2 : isHovered ? 1 : 0.82}
                  stroke={isHovered ? '#b0a8ff' : '#2a2a4e'}
                  strokeWidth={isHovered ? 1.5 : 0.8}
                />
                {showLabel && (
                  <text
                    textAnchor="middle"
                    dy={rad + fontSize + 3}
                    fontSize={fontSize}
                    fontWeight={isHovered ? 600 : 400}
                    fill={isHovered ? '#e0e0f8' : isNeighbor ? '#b0b0d0' : '#8888a8'}
                    fillOpacity={dimmed ? 0.25 : 1}
                    style={{ pointerEvents: 'none', userSelect: 'none' }}
                  >
                    {node.name.length > 16 ? node.name.slice(0, 14) + '…' : node.name}
                  </text>
                )}
              </g>
            )
          })}
        </g>
      </svg>

      {/* HTML Tooltip */}
      {tooltipData && !panning && (
        <TooltipBox data={tooltipData} x={mousePos.x} y={mousePos.y} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Reusable overlay button style
// ---------------------------------------------------------------------------
const btnStyle: React.CSSProperties = {
  width: 28,
  height: 28,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'rgba(30,30,48,0.9)',
  border: '1px solid #3a3a5c',
  borderRadius: 6,
  color: '#9090c8',
  fontSize: 16,
  cursor: 'pointer',
  lineHeight: 1,
  padding: 0,
}
