import React from 'react'
import type { QueryStep } from '../../types'

const STEP_LABELS: Record<string, string> = {
  enriching: 'Enriching query…',
  classifying: 'Classifying intent…',
  extracting: 'Extracting entities…',
  retrieving: 'Retrieving schema…',
  generating: 'Generating SQL…',
  validating: 'Validating SQL…',
  optimizing: 'Optimizing…',
  executing: 'Executing query…',
  formatting: 'Formatting results…',
}

const ALL_STEPS: QueryStep[] = [
  'enriching',
  'classifying',
  'extracting',
  'retrieving',
  'generating',
  'validating',
  'optimizing',
  'executing',
  'formatting',
]

interface StepIndicatorProps {
  step: QueryStep | null
}

export const StepIndicator: React.FC<StepIndicatorProps> = ({ step }) => {
  if (!step) return null

  const currentIdx = ALL_STEPS.indexOf(step)

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: '12px 16px',
        background: '#2a2a3e',
        borderRadius: 8,
        border: '1px solid #3a3a5c',
      }}
    >
      {/* Animated label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          style={{
            width: 12,
            height: 12,
            borderRadius: '50%',
            border: '2px solid #7c6af7',
            borderTopColor: 'transparent',
            display: 'inline-block',
            animation: 'spin 0.8s linear infinite',
            flexShrink: 0,
          }}
        />
        <span style={{ color: '#7c6af7', fontWeight: 600, fontSize: 13 }}>
          {STEP_LABELS[step] ?? step}
        </span>
      </div>

      {/* Step dots */}
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        {ALL_STEPS.map((s, i) => (
          <React.Fragment key={s}>
            <div
              title={STEP_LABELS[s] ?? s}
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background:
                  i < currentIdx
                    ? '#4ade80'
                    : i === currentIdx
                      ? '#7c6af7'
                      : '#3a3a5c',
                transition: 'background 0.3s',
                flexShrink: 0,
              }}
            />
            {i < ALL_STEPS.length - 1 && (
              <div
                style={{
                  flex: 1,
                  height: 1,
                  background: i < currentIdx ? '#4ade80' : '#3a3a5c',
                  transition: 'background 0.3s',
                }}
              />
            )}
          </React.Fragment>
        ))}
      </div>

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}
