import React, { useMemo, useState, useCallback, useRef } from 'react'
import type { GraphNode, GraphEdge } from '../../types'

const GROUP_COLORS: Record<string, string> = {
  core: '#3b82f6',
  reference: '#6366f1',
  audit: '#6b7280',
  utility: '#9ca3af',
  unknown: '#9ca3af',
}

function groupColor(group: string) {
  return GROUP_COLORS[group] ?? GROUP_COLORS.unknown
}

function nodeRadius(node: GraphNode): number {
  if (!node.importance_rank) return 12
  // rank 1 = biggest (24), higher rank = smaller, min 8
  return Math.max(8, 24 - (node.importance_rank - 1) * 1.5)
}

interface NodePosition {
  id: string
  x: number
  y: number
}

function runForceLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  width: number,
  height: number,
): NodePosition[] {
  if (nodes.length === 0) return []

  const cx = width / 2
  const cy = height / 2
  const r = Math.min(width, height) * 0.38

  // Initialize on a circle
  const positions: Array<{ id: string; x: number; y: number; vx: number; vy: number }> =
    nodes.map((n, i) => {
      const angle = (2 * Math.PI * i) / nodes.length
      return {
        id: n.id,
        x: cx + r * Math.cos(angle),
        y: cy + r * Math.sin(angle),
        vx: 0,
        vy: 0,
      }
    })

  const posMap = new Map(positions.map((p) => [p.id, p]))
  const k = Math.sqrt((width * height) / Math.max(nodes.length, 1))
  const ITERATIONS = nodes.length > 100 ? 80 : 150

  for (let iter = 0; iter < ITERATIONS; iter++) {
    const cooling = 1 - iter / ITERATIONS

    // Repulsion between all node pairs
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
        pi.vx += fx
        pi.vy += fy
        pj.vx -= fx
        pj.vy -= fy
      }
    }

    // Spring attraction along edges
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
      a.vx += fx
      a.vy += fy
      b.vx -= fx
      b.vy -= fy
    }

    // Apply velocities with cooling + boundary clamping
    const maxSpeed = Math.max(2, 15 * cooling)
    for (const p of positions) {
      const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy)
      if (speed > maxSpeed) {
        p.vx = (p.vx / speed) * maxSpeed
        p.vy = (p.vy / speed) * maxSpeed
      }
      p.x = Math.max(30, Math.min(width - 30, p.x + p.vx))
      p.y = Math.max(30, Math.min(height - 30, p.y + p.vy))
      p.vx *= 0.75
      p.vy *= 0.75
    }
  }

  return positions.map((p) => ({ id: p.id, x: p.x, y: p.y }))
}

interface GraphCanvasProps {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

interface Transform {
  x: number
  y: number
  scale: number
}

const SVG_WIDTH = 900
const SVG_HEIGHT = 600

export const GraphCanvas: React.FC<GraphCanvasProps> = ({ nodes, edges }) => {
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, scale: 1 })
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [panning, setPanning] = useState(false)
  const isPanning = useRef(false)
  const lastPan = useRef({ x: 0, y: 0 })
  const containerRef = useRef<SVGSVGElement>(null)

  const positions = useMemo(
    () => runForceLayout(nodes, edges, SVG_WIDTH, SVG_HEIGHT),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nodes.map((n) => n.id).join(','), edges.map((e) => e.id).join(',')],
  )

  const posMap = useMemo(() => new Map(positions.map((p) => [p.id, p])), [positions])

  const nodeMap = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.1 : 0.9
    setTransform((t) => ({
      ...t,
      scale: Math.max(0.2, Math.min(5, t.scale * factor)),
    }))
  }, [])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    isPanning.current = true
    setPanning(true)
    lastPan.current = { x: e.clientX, y: e.clientY }
  }, [])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isPanning.current) return
    const dx = e.clientX - lastPan.current.x
    const dy = e.clientY - lastPan.current.y
    lastPan.current = { x: e.clientX, y: e.clientY }
    setTransform((t) => ({ ...t, x: t.x + dx, y: t.y + dy }))
  }, [])

  const handleMouseUp = useCallback(() => {
    isPanning.current = false
    setPanning(false)
  }, [])

  const handleReset = useCallback(() => {
    setTransform({ x: 0, y: 0, scale: 1 })
  }, [])

  if (nodes.length === 0) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          color: '#5a5a7a',
          fontSize: 14,
        }}
      >
        No graph data available. Build the knowledge graph first.
      </div>
    )
  }

  const hoveredEdges = hoveredNode
    ? new Set(
        edges
          .filter((e) => e.from_id === hoveredNode || e.to_id === hoveredNode)
          .map((e) => e.id),
      )
    : null

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }}>
      {/* Reset button */}
      <button
        onClick={handleReset}
        style={{
          position: 'absolute',
          top: 12,
          right: 12,
          zIndex: 10,
          padding: '5px 12px',
          background: 'rgba(42,42,62,0.9)',
          border: '1px solid #3a3a5c',
          borderRadius: 6,
          color: '#9090a8',
          fontSize: 12,
          cursor: 'pointer',
        }}
      >
        Reset View
      </button>

      {/* Node count */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 12,
          zIndex: 10,
          fontSize: 11,
          color: '#6a6a8a',
        }}
      >
        {nodes.length} nodes · {edges.length} edges · scroll to zoom · drag to pan
      </div>

      <svg
        ref={containerRef}
        width="100%"
        height="100%"
        style={{ cursor: panning ? 'grabbing' : 'grab', background: '#1a1a2e' }}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <g transform={`translate(${transform.x}, ${transform.y}) scale(${transform.scale})`}>
          {/* Edges */}
          {edges.map((edge) => {
            const a = posMap.get(edge.from_id)
            const b = posMap.get(edge.to_id)
            if (!a || !b) return null
            const highlighted = hoveredEdges ? hoveredEdges.has(edge.id) : false
            return (
              <line
                key={edge.id}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={highlighted ? '#7c6af7' : '#3a3a5c'}
                strokeWidth={highlighted ? 2 : 1}
                strokeOpacity={hoveredEdges && !highlighted ? 0.2 : 0.7}
              />
            )
          })}

          {/* Nodes */}
          {positions.map((pos) => {
            const node = nodeMap.get(pos.id)
            if (!node) return null
            const rad = nodeRadius(node)
            const color = groupColor(node.group)
            const isHovered = hoveredNode === node.id
            const isDimmed = hoveredNode && !isHovered

            return (
              <g
                key={node.id}
                transform={`translate(${pos.x}, ${pos.y})`}
                onMouseEnter={() => setHoveredNode(node.id)}
                onMouseLeave={() => setHoveredNode(null)}
                style={{ cursor: 'pointer' }}
              >
                <circle
                  r={isHovered ? rad + 3 : rad}
                  fill={color}
                  fillOpacity={isDimmed ? 0.25 : 0.85}
                  stroke={isHovered ? '#fff' : color}
                  strokeWidth={isHovered ? 2 : 1}
                  strokeOpacity={isDimmed ? 0.3 : 1}
                />
                <text
                  textAnchor="middle"
                  dy={rad + 12}
                  fontSize={10}
                  fill="#c0c0d8"
                  fillOpacity={isDimmed ? 0.3 : 1}
                  style={{ pointerEvents: 'none', userSelect: 'none' }}
                >
                  {node.name.length > 14 ? node.name.slice(0, 13) + '…' : node.name}
                </text>

                {/* Tooltip on hover */}
                {isHovered && (
                  <g>
                    <rect
                      x={rad + 4}
                      y={-28}
                      width={Math.max(node.name.length * 7 + 12, 100)}
                      height={52}
                      rx={4}
                      fill="#2a2a3e"
                      stroke="#3a3a5c"
                      strokeWidth={1}
                    />
                    <text x={rad + 10} y={-14} fontSize={11} fill="#e0e0f0" fontWeight="600">
                      {node.name}
                    </text>
                    <text x={rad + 10} y={0} fontSize={10} fill="#9090a8">
                      {node.schema_name} · {node.group}
                    </text>
                    {node.row_count != null && (
                      <text x={rad + 10} y={14} fontSize={10} fill="#9090a8">
                        {node.row_count.toLocaleString()} rows
                      </text>
                    )}
                  </g>
                )}
              </g>
            )
          })}
        </g>
      </svg>
    </div>
  )
}
