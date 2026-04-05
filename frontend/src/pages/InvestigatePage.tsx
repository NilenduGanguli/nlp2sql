import React, { useState } from 'react'
import { useTraceStore } from '../store/traceStore'
import type { QueryTrace, TraceStep } from '../types'

// ── Colours consistent with app theme ──────────────────────────────────────
const C = {
  bg: '#1e1e2e',
  panel: '#2a2a3e',
  panel2: '#242438',
  border: '#3a3a5c',
  accent: '#7c6af7',
  text: '#e0e0f0',
  muted: '#9090a8',
  success: '#4ade80',
  warn: '#fbbf24',
  error: '#f87171',
  code: '#1a1a2e',
}

// ── Node labels ──────────────────────────────────────────────────────────────
const NODE_LABELS: Record<string, string> = {
  enrich_query:        'Query Enricher',
  classify_intent:     'Intent Classifier',
  extract_entities:    'Entity Extractor (Agentic)',
  retrieve_schema:     'Schema Retrieval',
  check_clarification: 'Clarification Check',
  generate_sql:        'SQL Generator',
  validate_sql:        'SQL Validator',
  optimize_sql:        'SQL Optimizer',
  execute_query:       'Query Executor',
  format_result:       'Result Formatter',
}

// ── Node → prompt label mapping (display only) ───────────────────────────────
const NODE_PROMPT_LABELS: Record<string, {system?: string, human?: string}> = {
  enrich_query:        { system: 'Query Enricher — System Prompt', human: 'Query Enricher — Human Message' },
  classify_intent:     { system: 'Intent Classifier — System Prompt' },
  extract_entities:    { system: 'Entity Extractor — System Prompt (with full schema tree)' },
  check_clarification: { system: 'Clarification Agent — System Prompt' },
  generate_sql:        { system: 'SQL Generator — System Prompt' },
}

// ── Op type colours ──────────────────────────────────────────────────────────
const OP_COLORS: Record<string, string> = {
  search_schema:         '#38bdf8',
  get_table_detail:      '#a78bfa',
  find_join_path:        '#fb923c',
  resolve_business_term: '#34d399',
  list_related_tables:   '#60a5fa',
  query_oracle:          '#f472b6',
  get_column_values:     '#fb923c',
  submit_entities:       '#4ade80',
  use_preresolved_fqns:  '#4ade80',
  expand_fk_neighbors:   '#fb923c',
}

const OP_HINTS: Record<string, string> = {
  search_schema:         'Text search across tables and columns',
  get_table_detail:      'Full column list + FK refs for one table',
  find_join_path:        'FK-based join path between two tables',
  resolve_business_term: 'Mapped business term → schema object',
  list_related_tables:   'FK-reachable tables from a seed table',
  query_oracle:          'Live Oracle SELECT — actual data from the database',
  get_column_values:     'Distinct values for a specific column (enum lookup, cached)',
  submit_entities:       'Final extracted entities + confirmed FQNs',
  use_preresolved_fqns:  'Used FQNs pre-resolved by entity agent (resolution skipped)',
  expand_fk_neighbors:   '1-hop FK neighbour expansion',
}

// ── Agent-loop iteration parser ───────────────────────────────────────────────

interface AgentIteration {
  label: string
  thought: string
  action: string
  args: Record<string, unknown>
  isFinal: boolean
  rawText: string
}

function parseAgentIterations(raw: string): AgentIteration[] {
  const results: AgentIteration[] = []
  const delimRe = /\[(Iteration \d+|Force submit)\]\s*/g
  const positions: Array<{ label: string; end: number }> = []
  let m: RegExpExecArray | null
  while ((m = delimRe.exec(raw)) !== null) {
    positions.push({ label: m[1], end: m.index + m[0].length })
  }
  for (let i = 0; i < positions.length; i++) {
    const { label, end } = positions[i]
    const nextEnd = i + 1 < positions.length
      ? positions[i + 1].end - positions[i + 1].label.length - 2
      : raw.length
    const text = raw.slice(end, nextEnd).trim()
    let thought = '', action = '', args: Record<string, unknown> = {}
    try {
      const jm = text.match(/\{[\s\S]*\}/)
      if (jm) {
        const parsed = JSON.parse(jm[0])
        thought = String(parsed.thought || '')
        action  = String(parsed.action || '')
        args    = (parsed.args as Record<string, unknown>) || {}
      }
    } catch { /* show raw text */ }
    results.push({ label, thought, action, args, isFinal: action === 'submit_entities', rawText: text })
  }
  return results
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtMs(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

// ── Sub-components ──────────────────────────────────────────────────────────

function Pre({ text, maxH = 260 }: { text: string; maxH?: number }) {
  return (
    <pre
      style={{
        background: C.code,
        border: `1px solid ${C.border}`,
        borderRadius: 6,
        padding: '10px 14px',
        maxHeight: maxH,
        overflowY: 'auto',
        fontSize: 12,
        lineHeight: 1.55,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        color: '#cfe0ff',
        margin: 0,
      }}
    >
      {text || '(empty)'}
    </pre>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6, marginTop: 14 }}>
      {children}
    </div>
  )
}

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{ background: color + '22', color, border: `1px solid ${color}44`, borderRadius: 4, padding: '1px 7px', fontSize: 11, fontWeight: 600 }}>
      {label}
    </span>
  )
}

/** Read-only prompt display with expand/collapse for long content */
function PromptView({ label, content }: { label: string; content: string }) {
  const [expanded, setExpanded] = useState(false)
  const isLong = content.length > 800
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <SectionLabel>{label}</SectionLabel>
        <div style={{ flex: 1 }} />
        {isLong && (
          <button
            onClick={() => setExpanded((v) => !v)}
            style={{
              padding: '2px 8px', borderRadius: 4, border: `1px solid ${C.border}`,
              background: C.panel, color: C.muted, fontSize: 11, cursor: 'pointer',
            }}
          >
            {expanded ? 'collapse' : `expand (${content.length.toLocaleString()} chars)`}
          </button>
        )}
      </div>
      <Pre text={content} maxH={expanded ? 1600 : isLong ? 160 : 260} />
    </div>
  )
}

// ── Agent loop viewer (for extract_entities) ──────────────────────────────────

function AgentLoopViewer({ rawResponse }: { rawResponse: string }) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)
  const iterations = parseAgentIterations(rawResponse)
  if (iterations.length === 0) {
    return <Pre text={rawResponse} maxH={240} />
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {iterations.map((it, idx) => {
        const color = it.isFinal ? C.success
          : OP_COLORS[it.action] || C.muted
        const isOpen = expandedIdx === idx
        return (
          <div
            key={idx}
            style={{
              border: `1px solid ${color}33`,
              borderRadius: 6,
              overflow: 'hidden',
              background: it.isFinal ? C.success + '08' : C.code,
            }}
          >
            <div
              onClick={() => setExpandedIdx(isOpen ? null : idx)}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 10,
                padding: '7px 12px', cursor: 'pointer', userSelect: 'none',
              }}
            >
              <span style={{
                flexShrink: 0, marginTop: 1,
                fontSize: 10, fontWeight: 700, color, background: color + '18',
                border: `1px solid ${color}44`, borderRadius: 4,
                padding: '1px 6px', whiteSpace: 'nowrap',
              }}>
                {it.isFinal ? '✓ FINAL' : it.label}
              </span>
              {it.action && (
                <span style={{
                  flexShrink: 0, marginTop: 1,
                  fontSize: 10, fontWeight: 700,
                  color: OP_COLORS[it.action] || C.muted,
                  background: (OP_COLORS[it.action] || C.muted) + '18',
                  border: `1px solid ${(OP_COLORS[it.action] || C.muted)}44`,
                  borderRadius: 4, padding: '1px 6px', fontFamily: 'monospace',
                }}>
                  {it.action}
                </span>
              )}
              {/* oracle_query: show SQL preview inline */}
              {it.action === 'query_oracle' && (it.args.sql as string) && (
                <span style={{ fontSize: 10, color: '#f472b6', fontFamily: 'monospace', fontStyle: 'italic', marginTop: 1, maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {String(it.args.sql).slice(0, 120)}
                </span>
              )}
              <span style={{
                flex: 1, fontSize: 12, color: it.isFinal ? C.success : C.text,
                overflow: 'hidden', display: '-webkit-box',
                WebkitLineClamp: isOpen ? undefined : 2,
                WebkitBoxOrient: 'vertical',
                lineHeight: 1.45,
              }}>
                {it.thought || it.rawText.slice(0, 200)}
              </span>
              <span style={{ color: C.muted, fontSize: 12, flexShrink: 0, marginTop: 1 }}>
                {isOpen ? '▾' : '▸'}
              </span>
            </div>

            {isOpen && (
              <div style={{ borderTop: `1px solid ${color}22`, padding: '8px 12px' }}>
                {it.isFinal ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {Array.isArray(it.args.tables) && (it.args.tables as string[]).length > 0 && (
                      <div>
                        <span style={{ fontSize: 11, color: C.muted, marginRight: 8 }}>Tables:</span>
                        {(it.args.tables as string[]).map((t) => (
                          <span key={t} style={{ fontSize: 11, color: C.text, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 4, padding: '1px 6px', marginRight: 4 }}>{t}</span>
                        ))}
                      </div>
                    )}
                    {Array.isArray(it.args.table_fqns) && (it.args.table_fqns as string[]).length > 0 && (
                      <div>
                        <span style={{ fontSize: 11, color: C.muted, marginRight: 8 }}>Confirmed FQNs:</span>
                        {(it.args.table_fqns as string[]).map((fqn) => (
                          <span key={fqn} style={{ fontSize: 11, color: C.success, background: C.success + '10', border: `1px solid ${C.success}33`, borderRadius: 4, padding: '1px 6px', marginRight: 4 }}>{fqn}</span>
                        ))}
                      </div>
                    )}
                    {Array.isArray(it.args.conditions) && (it.args.conditions as string[]).length > 0 && (
                      <div>
                        <span style={{ fontSize: 11, color: C.muted, marginRight: 8 }}>Conditions:</span>
                        <code style={{ fontSize: 11, color: '#cfe0ff' }}>{(it.args.conditions as string[]).join(', ')}</code>
                      </div>
                    )}
                  </div>
                ) : (
                  <pre style={{ margin: 0, fontSize: 11, color: '#cfe0ff', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {JSON.stringify(it.args, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Confirmed FQN list ────────────────────────────────────────────────────────

function ConfirmedFqnList({ fqns }: { fqns: string[] }) {
  if (!fqns.length) return null
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
      {fqns.map((fqn) => (
        <span
          key={fqn}
          style={{
            fontSize: 12, fontFamily: 'monospace', fontWeight: 600,
            color: C.success, background: C.success + '12',
            border: `1px solid ${C.success}44`,
            borderRadius: 5, padding: '3px 10px',
          }}
        >
          {fqn}
        </span>
      ))}
    </div>
  )
}

function GraphOpsTable({ ops }: { ops: import('../types').TraceGraphOp[] }) {
  if (!ops.length) return null
  return (
    <div>
      <SectionLabel>Graph / Tool Operations</SectionLabel>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['Operation', 'Params', 'Results'].map((h) => (
                <th key={h} style={{ textAlign: 'left', padding: '5px 10px', background: C.panel, color: C.muted, borderBottom: `1px solid ${C.border}`, fontWeight: 600 }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ops.map((op, i) => {
              const opColor = OP_COLORS[op.op] || C.accent
              const opHint  = OP_HINTS[op.op]
              return (
                <tr key={i} style={{ borderBottom: `1px solid ${C.border}22` }}>
                  <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>
                    <span style={{
                      fontFamily: 'monospace', fontWeight: 700, fontSize: 11,
                      color: opColor, background: opColor + '15',
                      border: `1px solid ${opColor}33`, borderRadius: 4,
                      padding: '2px 7px',
                    }}>
                      {op.op}
                    </span>
                    {opHint && (
                      <div style={{ fontSize: 10, color: C.muted, marginTop: 2, fontStyle: 'italic' }}>{opHint}</div>
                    )}
                  </td>
                  <td style={{ padding: '6px 10px', color: C.text, fontFamily: 'monospace', maxWidth: 300, wordBreak: 'break-word', fontSize: 11 }}>
                    {JSON.stringify(op.params)}
                  </td>
                  <td style={{ padding: '6px 10px', color: op.result_count > 0 ? C.success : C.muted }}>
                    {op.result_count} result{op.result_count !== 1 ? 's' : ''}
                    {op.result_sample.length > 0 && (
                      <details style={{ display: 'inline', marginLeft: 8 }}>
                        <summary style={{ cursor: 'pointer', color: C.muted, fontSize: 11 }}>sample</summary>
                        <pre style={{ margin: '4px 0 0', fontSize: 11, color: '#cfe0ff', background: C.code, padding: 6, borderRadius: 4, maxHeight: 120, overflowY: 'auto' }}>
                          {JSON.stringify(op.result_sample, null, 2)}
                        </pre>
                      </details>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Step card (read-only) ────────────────────────────────────────────────────

interface StepCardProps {
  step: TraceStep
  defaultOpen?: boolean
}

function StepCard({ step, defaultOpen = false }: StepCardProps) {
  const [open, setOpen] = useState(defaultOpen)
  const nodeLabel = NODE_LABELS[step.node] ?? step.node
  const hasError  = !!step.error
  const hasLlm    = !!step.llm_call
  const promptLabels = NODE_PROMPT_LABELS[step.node]

  const isAgentExtractor  = step.node === 'extract_entities'
  const isSchemaRetrieval = step.node === 'retrieve_schema'

  const iterations  = isAgentExtractor ? (step.output_summary?.iterations as number | undefined) : undefined
  const confirmedFqns: string[] = isAgentExtractor
    ? ((step.output_summary?.entity_table_fqns as string[]) || [])
    : []

  const usedPreresolved = isSchemaRetrieval &&
    step.graph_ops.some((op) => op.op === 'use_preresolved_fqns')

  return (
    <div style={{ border: `1px solid ${hasError ? C.error + '55' : isAgentExtractor ? C.accent + '44' : C.border}`, borderRadius: 8, marginBottom: 8, overflow: 'hidden' }}>
      {/* Header */}
      <div
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
          background: isAgentExtractor ? C.accent + '0a' : C.panel2,
          cursor: 'pointer', userSelect: 'none',
        }}
      >
        <span style={{ color: open ? C.accent : C.muted, fontSize: 14, width: 16 }}>{open ? '▾' : '▸'}</span>
        <span style={{ fontWeight: 600, color: C.text, fontSize: 13 }}>{nodeLabel}</span>
        <Badge label={step.step_label} color={hasError ? C.error : C.accent} />
        <span style={{ color: C.muted, fontSize: 12 }}>{fmtMs(step.duration_ms)}</span>
        {hasError && <Badge label="ERROR" color={C.error} />}
        {hasLlm && !isAgentExtractor && <Badge label="LLM" color={C.warn} />}
        {isAgentExtractor && iterations != null && (
          <Badge label={`${iterations} iteration${iterations !== 1 ? 's' : ''}`} color={C.warn} />
        )}
        {isAgentExtractor && confirmedFqns.length > 0 && (
          <Badge label={`${confirmedFqns.length} table${confirmedFqns.length !== 1 ? 's' : ''} confirmed`} color={C.success} />
        )}
        {!isAgentExtractor && step.graph_ops.length > 0 && (
          <Badge label={`${step.graph_ops.length} graph ops`} color="#38bdf8" />
        )}
        {isAgentExtractor && step.graph_ops.length > 0 && (
          <Badge label={`${step.graph_ops.length} tool calls`} color="#38bdf8" />
        )}
        {usedPreresolved && <Badge label="fast path" color={C.success} />}
        <div style={{ flex: 1 }} />
        {isAgentExtractor && confirmedFqns.length > 0 ? (
          <span style={{ color: C.success, fontSize: 11, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {confirmedFqns.join(', ')}
          </span>
        ) : (
          <span style={{ color: C.muted, fontSize: 11, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {Object.entries(step.output_summary ?? {})
              .filter(([k]) => !['entity_table_fqns', 'iterations'].includes(k))
              .slice(0, 3)
              .map(([k, v]) => `${k}: ${Array.isArray(v) ? (v as unknown[]).join(', ') : String(v)}`)
              .join(' · ')}
          </span>
        )}
      </div>

      {open && (
        <div style={{ padding: '14px 16px', background: C.panel }}>

          {/* Error banner */}
          {hasError && (
            <div style={{ background: C.error + '18', border: `1px solid ${C.error}55`, borderRadius: 6, padding: '8px 12px', color: C.error, fontSize: 12, marginBottom: 12 }}>
              <strong>Error:</strong> {step.error}
            </div>
          )}

          {/* ── ENTITY EXTRACTOR ─────────────────────────────────────────── */}
          {isAgentExtractor ? (
            <>
              {/* Actual system prompt as sent (includes full rendered schema tree) */}
              {hasLlm && step.llm_call!.system_prompt && (
                <PromptView
                  label={promptLabels?.system ?? 'Entity Extractor — System Prompt (as sent)'}
                  content={step.llm_call!.system_prompt}
                />
              )}

              {/* Initial query sent to agent */}
              {hasLlm && (
                <>
                  <SectionLabel>Query Sent to Agent</SectionLabel>
                  <Pre text={step.llm_call!.user_prompt} maxH={80} />
                </>
              )}

              {/* Agent investigation loop */}
              {hasLlm && step.llm_call!.raw_response && (
                <>
                  <SectionLabel>
                    Investigation Loop
                    {iterations != null && (
                      <span style={{ marginLeft: 8, color: C.warn, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
                        ({iterations} iteration{iterations !== 1 ? 's' : ''})
                      </span>
                    )}
                  </SectionLabel>
                  <AgentLoopViewer rawResponse={step.llm_call!.raw_response} />
                </>
              )}

              {/* Confirmed FQNs */}
              {confirmedFqns.length > 0 && (
                <>
                  <SectionLabel>Confirmed Table FQNs</SectionLabel>
                  <ConfirmedFqnList fqns={confirmedFqns} />
                </>
              )}

              {/* Tool calls table */}
              {step.graph_ops.length > 0 && (
                <>
                  <SectionLabel>Tool Call Summary</SectionLabel>
                  <GraphOpsTable ops={step.graph_ops} />
                </>
              )}

              {/* Parsed entity output */}
              {hasLlm && step.llm_call!.parsed_output != null && (
                <>
                  <SectionLabel>Extracted Entity Dict</SectionLabel>
                  <Pre
                    text={typeof step.llm_call!.parsed_output === 'string'
                      ? step.llm_call!.parsed_output
                      : JSON.stringify(step.llm_call!.parsed_output, null, 2)}
                    maxH={160}
                  />
                </>
              )}
            </>
          ) : (
            <>
              {/* ── RETRIEVE SCHEMA: fast-path note ──────────────────────── */}
              {usedPreresolved && (
                <div style={{
                  background: C.success + '10', border: `1px solid ${C.success}44`,
                  borderRadius: 6, padding: '8px 14px', fontSize: 12,
                  color: C.success, marginBottom: 12,
                  display: 'flex', alignItems: 'flex-start', gap: 8,
                }}>
                  <span style={{ fontWeight: 700 }}>✓ Fast path</span>
                  <span style={{ color: C.text }}>
                    Table FQNs were pre-resolved by the entity extraction agent.
                    Name resolution steps skipped — schema context built directly from confirmed FQNs.
                  </span>
                </div>
              )}

              {/* Actual system prompt (rendered — not template) */}
              {hasLlm && step.llm_call!.system_prompt && promptLabels?.system && (
                <PromptView label={promptLabels.system} content={step.llm_call!.system_prompt} />
              )}

              {/* User prompt */}
              {hasLlm && step.llm_call!.user_prompt && (
                <PromptView
                  label={promptLabels?.human ?? 'User Message (as sent)'}
                  content={step.llm_call!.user_prompt}
                />
              )}

              {/* For nodes with LLM but no mapped prompt labels — show both raw */}
              {hasLlm && !promptLabels && (
                <>
                  {step.llm_call!.system_prompt && (
                    <PromptView label="System Prompt (as sent)" content={step.llm_call!.system_prompt} />
                  )}
                  {step.llm_call!.user_prompt && (
                    <PromptView label="User Message (as sent)" content={step.llm_call!.user_prompt} />
                  )}
                </>
              )}

              {/* LLM raw response */}
              {hasLlm && step.llm_call!.raw_response && (
                <>
                  <SectionLabel>LLM Raw Response</SectionLabel>
                  <Pre text={step.llm_call!.raw_response} maxH={240} />
                </>
              )}

              {/* Parsed output */}
              {hasLlm && step.llm_call!.parsed_output != null && (
                <>
                  <SectionLabel>Parsed Output</SectionLabel>
                  <Pre
                    text={typeof step.llm_call!.parsed_output === 'string'
                      ? step.llm_call!.parsed_output
                      : JSON.stringify(step.llm_call!.parsed_output, null, 2)}
                    maxH={160}
                  />
                </>
              )}

              {/* Graph ops */}
              <GraphOpsTable ops={step.graph_ops} />

              {/* Output summary */}
              {Object.keys(step.output_summary ?? {}).length > 0 && (
                <>
                  <SectionLabel>Output Summary</SectionLabel>
                  <Pre text={JSON.stringify(step.output_summary, null, 2)} maxH={140} />
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export const InvestigatePage: React.FC = () => {
  const { traces, activeTraceId, pendingSteps, setActiveTrace } = useTraceStore()

  const activeTrace: QueryTrace | null = traces.find((t) => t.id === activeTraceId) ?? (traces[0] ?? null)
  const displaySteps: TraceStep[] = activeTrace
    ? (activeTrace.steps.length > 0 ? activeTrace.steps : pendingSteps)
    : pendingSteps

  const isLive = activeTrace != null && activeTrace.steps.length === 0 && pendingSteps.length > 0

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: C.bg, color: C.text }}>

      {/* ── Left: query history list (read-only) ── */}
      <div style={{ width: 260, flexShrink: 0, borderRight: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '12px 14px', borderBottom: `1px solid ${C.border}`, fontWeight: 700, fontSize: 13, color: C.text }}>
          Query Traces
          <span style={{ float: 'right', color: C.muted, fontSize: 11, fontWeight: 400 }}>{traces.length} saved</span>
        </div>

        {/* Info banner */}
        <div style={{ padding: '8px 14px', background: C.accent + '0a', borderBottom: `1px solid ${C.border}22`, fontSize: 11, color: C.muted, lineHeight: 1.5 }}>
          Read-only view. Every query trace shows the <strong style={{ color: C.text }}>actual prompts as sent</strong> to the LLM (fully rendered — not templates).
          <br />To edit prompts, use the <strong style={{ color: C.accent }}>Prompt Studio</strong> tab.
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {traces.length === 0 && (
            <div style={{ padding: 20, color: C.muted, fontSize: 12 }}>Run a query in the Chat tab to capture its trace here.</div>
          )}
          {traces.map((t) => {
            const isActive = t.id === (activeTrace?.id ?? '')
            return (
              <div
                key={t.id}
                onClick={() => setActiveTrace(t.id)}
                style={{
                  padding: '10px 14px', cursor: 'pointer',
                  borderLeft: isActive ? `3px solid ${C.accent}` : '3px solid transparent',
                  background: isActive ? C.accent + '18' : 'transparent',
                  borderBottom: `1px solid ${C.border}22`,
                }}
              >
                <div style={{ fontSize: 12, color: isActive ? C.accent : C.text, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {t.query}
                </div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 3 }}>
                  {t.timestamp instanceof Date
                    ? t.timestamp.toLocaleTimeString()
                    : new Date(t.timestamp as unknown as string).toLocaleTimeString()}
                  {' · '}
                  {t.steps.length > 0 ? `${t.steps.length} steps` : 'live…'}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Right: trace details ── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
        {!activeTrace && traces.length === 0 && (
          <div style={{ paddingTop: 60, textAlign: 'center', color: C.muted }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🔬</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: C.text, marginBottom: 8 }}>Investigate Query</div>
            <div style={{ fontSize: 13 }}>
              Every query you run will have its full processing lifecycle recorded here:<br />
              <strong style={{ color: C.text }}>actual rendered prompts</strong>, LLM calls, graph searches, Oracle tool calls, SQL generation, validation, and execution.
            </div>
          </div>
        )}

        {(activeTrace || isLive) && (
          <>
            {/* Query header */}
            <div style={{ marginBottom: 16, padding: '12px 16px', background: C.panel, borderRadius: 8, border: `1px solid ${C.border}` }}>
              <div style={{ fontSize: 11, color: C.muted, marginBottom: 4 }}>
                Query
                {isLive && <span style={{ marginLeft: 8, color: C.warn, fontWeight: 600 }}>● LIVE</span>}
              </div>
              <div style={{ fontSize: 14, color: C.text, fontWeight: 500 }}>
                {activeTrace?.query ?? '(loading…)'}
              </div>
              {activeTrace && activeTrace.steps.length > 0 && (
                <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <span style={{ fontSize: 11, color: C.muted }}>
                    {activeTrace.steps.length} steps ·{' '}
                    {fmtMs(activeTrace.steps.reduce((sum, s) => sum + s.duration_ms, 0))} total
                  </span>
                  {activeTrace.steps.filter((s) => s.error).length > 0 && (
                    <Badge label={`${activeTrace.steps.filter((s) => s.error).length} error(s)`} color={C.error} />
                  )}
                  {activeTrace.steps.filter((s) => s.llm_call).length > 0 && (
                    <Badge label={`${activeTrace.steps.filter((s) => s.llm_call).length} LLM nodes`} color={C.warn} />
                  )}
                  {(() => {
                    const totalOps = activeTrace.steps.reduce((sum, s) => sum + s.graph_ops.length, 0)
                    const oracleCalls = activeTrace.steps.reduce((sum, s) => sum + s.graph_ops.filter(o => o.op === 'query_oracle').length, 0)
                    const valueLookups = activeTrace.steps.reduce((sum, s) => sum + s.graph_ops.filter(o => o.op === 'get_column_values').length, 0)
                    const agentStep = activeTrace.steps.find((s) => s.node === 'extract_entities')
                    const iterations = agentStep?.output_summary?.iterations as number | undefined
                    return (
                      <>
                        {totalOps > 0 && <Badge label={`${totalOps} graph/tool ops`} color="#38bdf8" />}
                        {oracleCalls > 0 && <Badge label={`${oracleCalls} oracle queries`} color="#f472b6" />}
                        {valueLookups > 0 && <Badge label={`${valueLookups} value lookups`} color="#fb923c" />}
                        {iterations != null && <Badge label={`entity agent: ${iterations} iterations`} color={C.accent} />}
                      </>
                    )
                  })()}
                </div>
              )}
            </div>

            {/* Step cards */}
            {displaySteps.map((step, i) => (
              <StepCard
                key={`${step.node}-${i}`}
                step={step}
                defaultOpen={
                  step.node === 'generate_sql' ||
                  step.node === 'extract_entities' ||
                  step.node === 'retrieve_schema' ||
                  (step.node === 'validate_sql' && step.output_summary?.validation_passed === false) ||
                  !!(step.error)
                }
              />
            ))}

            {isLive && (
              <div style={{ padding: '12px 16px', color: C.muted, fontSize: 12, textAlign: 'center' }}>
                Pipeline is running — steps will appear as they complete…
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
