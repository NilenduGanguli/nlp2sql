/**
 * TeachPage — 4-step wizard for teaching the system from a (query, SQL) pair.
 *
 * Step 1: Enter question + expected SQL.
 * Step 2: Review/edit description and why_this_sql.
 * Step 3: Review/edit + add Q&A pairs, key concepts, tags, key filter values,
 *         curator notes, and sibling KnowledgeEntry attachments.
 * Step 4: Confirm + save.
 *
 * Save is atomic via POST /api/teach/save — one session entry + N
 * LearnedPatterns (from Q&A) + M sibling entries land in one fsync.
 */
import React, { useCallback, useState } from 'react'
import {
  analyzeTeach,
  bulkTeach,
  saveTeach,
  type BulkResponse,
  type TeachAnalysis,
  type TeachClarification,
  type TeachSavePayload,
  type TeachSibling,
} from '../api/teach'

const EMPTY_ANALYSIS: TeachAnalysis = {
  title: '',
  description: '',
  why_this_sql: '',
  key_concepts: [],
  tags: [],
  anticipated_clarifications: [],
  key_filter_values: {},
}

type Step = 1 | 2 | 3 | 4

const stepLabel = (s: Step): string =>
  ({ 1: 'Question + SQL', 2: 'Description & Reasoning', 3: 'Knowledge & Q&A', 4: 'Save' }[s])


export const TeachPage: React.FC = () => {
  const [step, setStep] = useState<Step>(1)
  const [userInput, setUserInput] = useState('')
  const [expectedSql, setExpectedSql] = useState('')
  const [analysis, setAnalysis] = useState<TeachAnalysis>(EMPTY_ANALYSIS)
  const [curatorNotes, setCuratorNotes] = useState('')
  const [siblings, setSiblings] = useState<TeachSibling[]>([])
  const [explanation] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedId, setSavedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reset = useCallback(() => {
    setStep(1)
    setUserInput('')
    setExpectedSql('')
    setAnalysis(EMPTY_ANALYSIS)
    setCuratorNotes('')
    setSiblings([])
    setSavedId(null)
    setError(null)
  }, [])

  const runAnalysis = useCallback(async () => {
    setAnalyzing(true)
    setError(null)
    try {
      const a = await analyzeTeach(userInput.trim(), expectedSql.trim())
      setAnalysis(a)
      setStep(2)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Analysis failed')
    } finally {
      setAnalyzing(false)
    }
  }, [userInput, expectedSql])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setError(null)
    try {
      const payload: TeachSavePayload = {
        user_input: userInput.trim(),
        expected_sql: expectedSql.trim(),
        tables_used: extractTablesFromSql(expectedSql),
        analysis,
        curator_notes: curatorNotes.trim(),
        siblings,
        explanation,
      }
      const r = await saveTeach(payload)
      setSavedId(r.session_entry_id)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }, [userInput, expectedSql, analysis, curatorNotes, siblings, explanation])

  return (
    <div style={{ padding: 24, overflow: 'auto', height: '100%' }}>
      <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Teach the system</h1>
      <p style={{ fontSize: 12, color: '#9090a8', marginBottom: 24 }}>
        Upload a (question, expected SQL) pair. The LLM analyzes it into reusable knowledge,
        you review/edit, then save — every future similar query benefits.
      </p>

      <BulkUploadPanel />

      <StepBar step={step} setStep={setStep} hasInput={Boolean(userInput && expectedSql)} />

      {error && (
        <div
          style={{
            background: 'rgba(248,113,113,0.12)',
            border: '1px solid #f87171',
            color: '#f87171',
            padding: '8px 12px',
            borderRadius: 6,
            marginBottom: 16,
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      {step === 1 && (
        <Step1
          userInput={userInput}
          setUserInput={setUserInput}
          expectedSql={expectedSql}
          setExpectedSql={setExpectedSql}
          onAnalyze={runAnalysis}
          analyzing={analyzing}
        />
      )}
      {step === 2 && (
        <Step2
          analysis={analysis}
          setAnalysis={setAnalysis}
          onBack={() => setStep(1)}
          onNext={() => setStep(3)}
        />
      )}
      {step === 3 && (
        <Step3
          analysis={analysis}
          setAnalysis={setAnalysis}
          curatorNotes={curatorNotes}
          setCuratorNotes={setCuratorNotes}
          siblings={siblings}
          setSiblings={setSiblings}
          onBack={() => setStep(2)}
          onNext={() => setStep(4)}
        />
      )}
      {step === 4 && (
        <Step4
          payload={{
            user_input: userInput.trim(),
            expected_sql: expectedSql.trim(),
            tables_used: extractTablesFromSql(expectedSql),
            analysis,
            curator_notes: curatorNotes,
            siblings,
            explanation,
          }}
          savedId={savedId}
          saving={saving}
          onBack={() => setStep(3)}
          onSave={handleSave}
          onReset={reset}
        />
      )}
    </div>
  )
}


// ───────────────────────────────────────────────────────────────────────────
// Step bar
// ───────────────────────────────────────────────────────────────────────────


const StepBar: React.FC<{ step: Step; setStep: (s: Step) => void; hasInput: boolean }> = ({
  step,
  setStep,
  hasInput,
}) => {
  const steps: Step[] = [1, 2, 3, 4]
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 24, fontSize: 12 }}>
      {steps.map((s, i) => {
        const isActive = step === s
        const isPast = step > s
        const clickable = hasInput && (s === 1 || isPast || isActive)
        const color = isActive ? '#7c6af7' : isPast ? '#22c55e' : '#9090a8'
        return (
          <React.Fragment key={s}>
            <button
              onClick={() => clickable && setStep(s)}
              disabled={!clickable}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                background: 'none',
                border: 'none',
                color,
                fontWeight: isActive ? 600 : 400,
                cursor: clickable ? 'pointer' : 'default',
                padding: '4px 8px',
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  width: 22,
                  height: 22,
                  borderRadius: '50%',
                  background: isActive ? '#7c6af7' : isPast ? '#22c55e' : '#3a3a5c',
                  color: '#fff',
                  fontSize: 11,
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontWeight: 600,
                }}
              >
                {isPast ? '✓' : s}
              </span>
              <span>{stepLabel(s)}</span>
            </button>
            {i < steps.length - 1 && (
              <span
                style={{
                  flex: '0 0 24px',
                  height: 1,
                  background: '#3a3a5c',
                  margin: '0 4px',
                }}
              />
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}


// ───────────────────────────────────────────────────────────────────────────
// Step 1 — Question + Expected SQL
// ───────────────────────────────────────────────────────────────────────────


const Step1: React.FC<{
  userInput: string
  setUserInput: (s: string) => void
  expectedSql: string
  setExpectedSql: (s: string) => void
  onAnalyze: () => void
  analyzing: boolean
}> = ({ userInput, setUserInput, expectedSql, setExpectedSql, onAnalyze, analyzing }) => (
  <Panel>
    <Field label="Question (natural language)">
      <textarea
        value={userInput}
        onChange={(e) => setUserInput(e.target.value)}
        rows={3}
        placeholder="e.g. How many active customers per region?"
        style={textareaStyle}
      />
    </Field>
    <Field label="Expected SQL (Oracle)">
      <textarea
        value={expectedSql}
        onChange={(e) => setExpectedSql(e.target.value)}
        rows={10}
        placeholder="SELECT ..."
        style={{ ...textareaStyle, fontFamily: 'ui-monospace, Consolas, monospace' }}
      />
    </Field>
    <Actions>
      <PrimaryButton
        onClick={onAnalyze}
        disabled={!userInput.trim() || !expectedSql.trim() || analyzing}
      >
        {analyzing ? 'Analyzing…' : 'Analyze with LLM →'}
      </PrimaryButton>
    </Actions>
  </Panel>
)


// ───────────────────────────────────────────────────────────────────────────
// Step 2 — Description & Reasoning
// ───────────────────────────────────────────────────────────────────────────


const Step2: React.FC<{
  analysis: TeachAnalysis
  setAnalysis: (a: TeachAnalysis) => void
  onBack: () => void
  onNext: () => void
}> = ({ analysis, setAnalysis, onBack, onNext }) => (
  <Panel>
    <Field label="Title">
      <input
        value={analysis.title}
        onChange={(e) => setAnalysis({ ...analysis, title: e.target.value })}
        placeholder="≤ 8 word summary"
        style={inputStyle}
      />
    </Field>
    <Field label="Description (business intent, no SQL terms)">
      <textarea
        value={analysis.description}
        onChange={(e) => setAnalysis({ ...analysis, description: e.target.value })}
        rows={3}
        style={textareaStyle}
      />
    </Field>
    <Field label="Why this SQL? (reasoning trace, technical)">
      <textarea
        value={analysis.why_this_sql}
        onChange={(e) => setAnalysis({ ...analysis, why_this_sql: e.target.value })}
        rows={5}
        style={textareaStyle}
      />
    </Field>
    <Actions>
      <SecondaryButton onClick={onBack}>← Back</SecondaryButton>
      <PrimaryButton onClick={onNext}>Next: Knowledge & Q&A →</PrimaryButton>
    </Actions>
  </Panel>
)


// ───────────────────────────────────────────────────────────────────────────
// Step 3 — Knowledge, Q&A, filter values, curator notes, siblings
// ───────────────────────────────────────────────────────────────────────────


const Step3: React.FC<{
  analysis: TeachAnalysis
  setAnalysis: (a: TeachAnalysis) => void
  curatorNotes: string
  setCuratorNotes: (s: string) => void
  siblings: TeachSibling[]
  setSiblings: (xs: TeachSibling[]) => void
  onBack: () => void
  onNext: () => void
}> = ({
  analysis,
  setAnalysis,
  curatorNotes,
  setCuratorNotes,
  siblings,
  setSiblings,
  onBack,
  onNext,
}) => {
  const setClarif = (i: number, c: TeachClarification) => {
    const next = [...analysis.anticipated_clarifications]
    next[i] = c
    setAnalysis({ ...analysis, anticipated_clarifications: next })
  }
  const addClarif = () =>
    setAnalysis({
      ...analysis,
      anticipated_clarifications: [
        ...analysis.anticipated_clarifications,
        { question: '', answer: '' },
      ],
    })
  const removeClarif = (i: number) =>
    setAnalysis({
      ...analysis,
      anticipated_clarifications: analysis.anticipated_clarifications.filter((_, j) => j !== i),
    })

  return (
    <Panel>
      <ChipList
        label="Key concepts"
        values={analysis.key_concepts}
        onChange={(v) => setAnalysis({ ...analysis, key_concepts: v })}
        placeholder="e.g. active customer"
      />
      <ChipList
        label="Tags"
        values={analysis.tags}
        onChange={(v) => setAnalysis({ ...analysis, tags: v })}
        placeholder="e.g. customer, status-filter"
      />

      <Field label="Anticipated clarifications (Q & A)">
        {analysis.anticipated_clarifications.map((c, i) => (
          <div
            key={i}
            style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 8 }}
          >
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                value={c.question}
                onChange={(e) => setClarif(i, { ...c, question: e.target.value })}
                placeholder="Question"
                style={{ ...inputStyle, flex: 1 }}
              />
              <button
                onClick={() => removeClarif(i)}
                style={removeBtnStyle}
                title="Remove"
              >
                ×
              </button>
            </div>
            <input
              value={c.answer}
              onChange={(e) => setClarif(i, { ...c, answer: e.target.value })}
              placeholder="Answer"
              style={inputStyle}
            />
          </div>
        ))}
        <button onClick={addClarif} style={addBtnStyle}>
          + Add Q&A
        </button>
      </Field>

      <KeyValueTable
        label="Key filter values (extracted from WHERE/HAVING/IN)"
        values={analysis.key_filter_values}
        onChange={(v) => setAnalysis({ ...analysis, key_filter_values: v })}
      />

      <Field label="Curator notes (optional, free text)">
        <textarea
          value={curatorNotes}
          onChange={(e) => setCuratorNotes(e.target.value)}
          rows={3}
          placeholder="Caveats, scope notes, or anything the LLM missed…"
          style={textareaStyle}
        />
      </Field>

      <SiblingList siblings={siblings} setSiblings={setSiblings} />

      <Actions>
        <SecondaryButton onClick={onBack}>← Back</SecondaryButton>
        <PrimaryButton onClick={onNext}>Next: Review & Save →</PrimaryButton>
      </Actions>
    </Panel>
  )
}


// ───────────────────────────────────────────────────────────────────────────
// Step 4 — Review & Save
// ───────────────────────────────────────────────────────────────────────────


const Step4: React.FC<{
  payload: TeachSavePayload
  savedId: string | null
  saving: boolean
  onBack: () => void
  onSave: () => void
  onReset: () => void
}> = ({ payload, savedId, saving, onBack, onSave, onReset }) => {
  if (savedId) {
    return (
      <Panel>
        <div
          style={{
            background: 'rgba(34,197,94,0.1)',
            border: '1px solid #22c55e',
            color: '#22c55e',
            padding: '12px 16px',
            borderRadius: 6,
            marginBottom: 16,
            fontSize: 13,
          }}
        >
          Saved as <code style={{ color: '#fff' }}>{savedId}</code>. The system has now learned
          this query.
        </div>
        <Actions>
          <PrimaryButton onClick={onReset}>Teach another</PrimaryButton>
        </Actions>
      </Panel>
    )
  }

  return (
    <Panel>
      <h3 style={sectionTitleStyle}>Review what will be saved</h3>
      <Summary label="Question" value={payload.user_input} />
      <Summary label="Expected SQL" value={payload.expected_sql} mono />
      <Summary label="Tables used" value={payload.tables_used?.join(', ') || '—'} />
      <Summary label="Description" value={payload.analysis.description || '—'} />
      <Summary label="Why this SQL" value={payload.analysis.why_this_sql || '—'} />
      <Summary label="Key concepts" value={payload.analysis.key_concepts.join(', ') || '—'} />
      <Summary label="Tags" value={payload.analysis.tags.join(', ') || '—'} />
      <Summary
        label={`Q&A pairs (${payload.analysis.anticipated_clarifications.length})`}
        value={
          payload.analysis.anticipated_clarifications
            .map((c) => `Q: ${c.question}\nA: ${c.answer}`)
            .join('\n\n') || '—'
        }
      />
      <Summary
        label={`Filter values (${Object.keys(payload.analysis.key_filter_values).length})`}
        value={
          Object.entries(payload.analysis.key_filter_values)
            .map(([k, vs]) => `${k}: ${vs.join(', ')}`)
            .join('\n') || '—'
        }
      />
      {payload.curator_notes && <Summary label="Curator notes" value={payload.curator_notes} />}
      <Summary
        label={`Siblings (${payload.siblings?.length ?? 0})`}
        value={payload.siblings?.map((s) => `[${s.category}] ${s.content}`).join('\n') || '—'}
      />

      <Actions>
        <SecondaryButton onClick={onBack}>← Back</SecondaryButton>
        <PrimaryButton onClick={onSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save to knowledge base'}
        </PrimaryButton>
      </Actions>
    </Panel>
  )
}


// ───────────────────────────────────────────────────────────────────────────
// Reusable atoms
// ───────────────────────────────────────────────────────────────────────────


const Panel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div
    style={{
      background: '#2a2a3e',
      border: '1px solid #3a3a5c',
      borderRadius: 8,
      padding: 20,
      maxWidth: 900,
    }}
  >
    {children}
  </div>
)


const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div style={{ marginBottom: 16 }}>
    <label
      style={{
        display: 'block',
        fontSize: 11,
        color: '#9090a8',
        marginBottom: 6,
        fontWeight: 500,
      }}
    >
      {label}
    </label>
    {children}
  </div>
)


const Actions: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
    {children}
  </div>
)


const PrimaryButton: React.FC<{
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}> = ({ onClick, disabled, children }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    style={{
      background: disabled ? '#4a4a6c' : '#7c6af7',
      color: '#fff',
      border: 'none',
      padding: '8px 16px',
      borderRadius: 6,
      fontSize: 13,
      fontWeight: 500,
      cursor: disabled ? 'not-allowed' : 'pointer',
    }}
  >
    {children}
  </button>
)


const SecondaryButton: React.FC<{ onClick: () => void; children: React.ReactNode }> = ({
  onClick,
  children,
}) => (
  <button
    onClick={onClick}
    style={{
      background: 'transparent',
      color: '#9090a8',
      border: '1px solid #4a4a6c',
      padding: '8px 16px',
      borderRadius: 6,
      fontSize: 13,
      cursor: 'pointer',
    }}
  >
    {children}
  </button>
)


const inputStyle: React.CSSProperties = {
  width: '100%',
  background: '#1a1a2e',
  border: '1px solid #3a3a5c',
  borderRadius: 4,
  color: '#e0e0f0',
  fontSize: 13,
  padding: '6px 10px',
  outline: 'none',
}


const textareaStyle: React.CSSProperties = {
  ...inputStyle,
  resize: 'vertical' as const,
  fontFamily: 'inherit',
}


const sectionTitleStyle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 600,
  marginTop: 0,
  marginBottom: 12,
  color: '#e0e0f0',
}


const removeBtnStyle: React.CSSProperties = {
  background: 'transparent',
  color: '#f87171',
  border: '1px solid #4a4a6c',
  padding: '0 10px',
  borderRadius: 4,
  fontSize: 14,
  cursor: 'pointer',
}


const addBtnStyle: React.CSSProperties = {
  background: 'rgba(124,106,247,0.12)',
  color: '#7c6af7',
  border: '1px dashed #7c6af7',
  padding: '6px 12px',
  borderRadius: 4,
  fontSize: 12,
  cursor: 'pointer',
  marginTop: 4,
}


// ── Chip-list editor (used for tags + key concepts) ──────────────────────────

const ChipList: React.FC<{
  label: string
  values: string[]
  onChange: (v: string[]) => void
  placeholder?: string
}> = ({ label, values, onChange, placeholder }) => {
  const [draft, setDraft] = useState('')
  const add = () => {
    const v = draft.trim()
    if (v && !values.includes(v)) {
      onChange([...values, v])
    }
    setDraft('')
  }
  return (
    <Field label={label}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
        {values.map((v) => (
          <span
            key={v}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              background: 'rgba(124,106,247,0.12)',
              color: '#a5b4fc',
              border: '1px solid #4a4a6c',
              padding: '2px 8px',
              borderRadius: 999,
              fontSize: 11,
            }}
          >
            {v}
            <button
              onClick={() => onChange(values.filter((x) => x !== v))}
              style={{
                background: 'none',
                border: 'none',
                color: '#9090a8',
                cursor: 'pointer',
                fontSize: 12,
              }}
              title="Remove"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
          placeholder={placeholder}
          style={{ ...inputStyle, flex: 1 }}
        />
        <button onClick={add} style={addBtnStyle}>+ Add</button>
      </div>
    </Field>
  )
}


// ── Key-value editor for filter values ───────────────────────────────────────

const KeyValueTable: React.FC<{
  label: string
  values: Record<string, string[]>
  onChange: (v: Record<string, string[]>) => void
}> = ({ label, values, onChange }) => {
  const [colDraft, setColDraft] = useState('')
  const [valDraft, setValDraft] = useState('')

  const addRow = () => {
    const c = colDraft.trim().toUpperCase()
    if (!c) return
    const existing = values[c] ?? []
    if (valDraft.trim()) {
      onChange({ ...values, [c]: [...existing, valDraft.trim()] })
    } else {
      onChange({ ...values, [c]: existing })
    }
    setColDraft('')
    setValDraft('')
  }

  const removeRow = (c: string) => {
    const next = { ...values }
    delete next[c]
    onChange(next)
  }

  const removeValue = (c: string, v: string) => {
    const next = { ...values, [c]: values[c].filter((x) => x !== v) }
    onChange(next)
  }

  return (
    <Field label={label}>
      {Object.entries(values).map(([col, vs]) => (
        <div
          key={col}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 6,
            marginBottom: 6,
            flexWrap: 'wrap',
          }}
        >
          <span style={{ ...inputStyle, width: 160, padding: '4px 10px', color: '#a5b4fc' }}>
            {col}
          </span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, flex: 1 }}>
            {vs.map((v) => (
              <span
                key={`${col}|${v}`}
                style={{
                  background: 'rgba(124,106,247,0.12)',
                  color: '#e0e0f0',
                  border: '1px solid #4a4a6c',
                  padding: '2px 8px',
                  borderRadius: 4,
                  fontSize: 11,
                  fontFamily: 'ui-monospace, monospace',
                }}
              >
                '{v}'
                <button
                  onClick={() => removeValue(col, v)}
                  style={{
                    marginLeft: 4,
                    background: 'none',
                    border: 'none',
                    color: '#9090a8',
                    cursor: 'pointer',
                  }}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <button onClick={() => removeRow(col)} style={removeBtnStyle}>×</button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          value={colDraft}
          onChange={(e) => setColDraft(e.target.value)}
          placeholder="COLUMN_NAME"
          style={{ ...inputStyle, width: 200 }}
        />
        <input
          value={valDraft}
          onChange={(e) => setValDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              addRow()
            }
          }}
          placeholder="value"
          style={{ ...inputStyle, flex: 1 }}
        />
        <button onClick={addRow} style={addBtnStyle}>+ Add</button>
      </div>
    </Field>
  )
}


// ── Sibling KnowledgeEntry attachments ───────────────────────────────────────

const SiblingList: React.FC<{
  siblings: TeachSibling[]
  setSiblings: (xs: TeachSibling[]) => void
}> = ({ siblings, setSiblings }) => {
  const [content, setContent] = useState('')
  const [category, setCategory] = useState('business_rule')

  const add = () => {
    if (!content.trim()) return
    setSiblings([...siblings, { content: content.trim(), category }])
    setContent('')
    setCategory('business_rule')
  }

  return (
    <Field label="Sibling knowledge entries (optional — saved alongside the main entry)">
      {siblings.map((s, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 6,
            marginBottom: 6,
            background: 'rgba(124,106,247,0.06)',
            padding: 8,
            borderRadius: 4,
          }}
        >
          <span
            style={{
              fontSize: 10,
              padding: '1px 6px',
              borderRadius: 999,
              background: '#3a3a5c',
              color: '#a5b4fc',
              flexShrink: 0,
            }}
          >
            {s.category}
          </span>
          <span style={{ flex: 1, fontSize: 12, color: '#e0e0f0', whiteSpace: 'pre-wrap' }}>
            {s.content}
          </span>
          <button
            onClick={() => setSiblings(siblings.filter((_, j) => j !== i))}
            style={removeBtnStyle}
          >
            ×
          </button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          style={{ ...inputStyle, width: 150 }}
        >
          <option value="business_rule">Business rule</option>
          <option value="glossary">Glossary</option>
          <option value="column_values">Column values</option>
          <option value="manual">Manual</option>
        </select>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={2}
          placeholder="Free-text entry: business rule, glossary term, etc."
          style={{ ...textareaStyle, flex: 1 }}
        />
        <button onClick={add} style={addBtnStyle}>+ Add</button>
      </div>
    </Field>
  )
}


// ── Step 4 summary row ───────────────────────────────────────────────────────

const Summary: React.FC<{ label: string; value: string; mono?: boolean }> = ({
  label,
  value,
  mono,
}) => (
  <div style={{ marginBottom: 12 }}>
    <div style={{ fontSize: 10, color: '#9090a8', textTransform: 'uppercase', letterSpacing: 0.5 }}>
      {label}
    </div>
    <pre
      style={{
        margin: 0,
        marginTop: 2,
        padding: '6px 8px',
        background: '#1a1a2e',
        border: '1px solid #3a3a5c',
        borderRadius: 4,
        fontSize: 12,
        color: '#e0e0f0',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        fontFamily: mono ? 'ui-monospace, Consolas, monospace' : 'inherit',
      }}
    >
      {value}
    </pre>
  </div>
)


// ───────────────────────────────────────────────────────────────────────────
// Bulk upload — drop-zone + results table (Phase 3)
// ───────────────────────────────────────────────────────────────────────────


const BulkUploadPanel: React.FC = () => {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<BulkResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [drag, setDrag] = useState(false)
  const inputRef = React.useRef<HTMLInputElement>(null)

  const upload = useCallback(async (f: File) => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const r = await bulkTeach(f)
      setResult(r)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Upload failed')
    } finally {
      setBusy(false)
    }
  }, [])

  const onFileChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) void upload(f)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDrag(false)
    const f = e.dataTransfer.files?.[0]
    if (f) void upload(f)
  }

  return (
    <div
      style={{
        background: '#2a2a3e',
        border: '1px solid #3a3a5c',
        borderRadius: 8,
        padding: 16,
        marginBottom: 24,
        maxWidth: 900,
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          background: 'none',
          border: 'none',
          color: '#a5b4fc',
          fontSize: 13,
          fontWeight: 500,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span style={{ display: 'inline-block', transition: 'transform 0.15s', transform: open ? 'rotate(90deg)' : 'rotate(0)' }}>
          ›
        </span>
        <span>Bulk upload — JSON / CSV / SQL / ZIP-of-SQL</span>
      </button>

      {open && (
        <div style={{ marginTop: 12 }}>
          <div
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            style={{
              padding: 24,
              border: `2px dashed ${drag ? '#7c6af7' : '#4a4a6c'}`,
              borderRadius: 8,
              background: drag ? 'rgba(124,106,247,0.08)' : 'rgba(26,26,46,0.5)',
              textAlign: 'center',
              fontSize: 12,
              color: '#9090a8',
              cursor: 'pointer',
              transition: 'border-color 0.15s, background 0.15s',
            }}
          >
            {busy
              ? 'Uploading & analysing…'
              : 'Drop a .json / .csv / .sql / .zip file here, or click to choose'}
            <input
              ref={inputRef}
              type="file"
              accept=".json,.csv,.sql,.zip"
              style={{ display: 'none' }}
              onChange={onFileChosen}
            />
          </div>

          {error && (
            <div
              style={{
                marginTop: 8,
                padding: '6px 10px',
                background: 'rgba(248,113,113,0.12)',
                border: '1px solid #f87171',
                color: '#f87171',
                borderRadius: 4,
                fontSize: 12,
              }}
            >
              {error}
            </div>
          )}

          {result && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 12, color: '#a5b4fc', marginBottom: 8 }}>
                Format detected: <strong>{result.format_detected}</strong> ·
                {' '}{result.saved} saved · {result.failed} failed
              </div>
              <div
                style={{
                  maxHeight: 240,
                  overflow: 'auto',
                  border: '1px solid #3a3a5c',
                  borderRadius: 4,
                }}
              >
                {result.items.map((it, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      gap: 8,
                      padding: '4px 8px',
                      borderBottom: '1px solid #3a3a5c',
                      fontSize: 11,
                      fontFamily: 'ui-monospace, monospace',
                    }}
                  >
                    <span
                      style={{
                        color: it.status === 'saved' ? '#22c55e' : '#f87171',
                        flexShrink: 0,
                        width: 16,
                      }}
                    >
                      {it.status === 'saved' ? '✓' : '✗'}
                    </span>
                    <span style={{ flex: 1, color: '#e0e0f0' }}>
                      {it.user_input}
                    </span>
                    {it.status === 'saved' ? (
                      <span style={{ color: '#9090a8' }}>
                        +{it.learned_pattern_count} Q&A
                      </span>
                    ) : (
                      <span style={{ color: '#f87171' }}>{it.error}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}


// ── Helpers ──────────────────────────────────────────────────────────────────

function extractTablesFromSql(sql: string): string[] {
  // Best-effort grab of SCHEMA.TABLE tokens from FROM / JOIN clauses.
  const out = new Set<string>()
  const pattern = /\b(?:from|join)\s+([A-Za-z_]\w*\.[A-Za-z_]\w*)/gi
  for (const match of sql.matchAll(pattern)) {
    out.add(match[1].toUpperCase())
  }
  return [...out]
}
