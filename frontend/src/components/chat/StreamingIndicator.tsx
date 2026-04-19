import React from 'react'
import type { QueryStep } from '../../types'

const STEP_ORDER: QueryStep[] = [
  'enriching',
  'classifying',
  'auto_clarifying',
  'extracting',
  'retrieving',
  'generating',
  'validating',
  'optimizing',
  'presenting',
  'executing',
  'formatting',
]

const STEP_LABELS: Record<QueryStep, string> = {
  enriching: 'Enriching query',
  classifying: 'Classifying intent',
  auto_clarifying: 'Auto-clarifying',
  extracting: 'Extracting entities',
  retrieving: 'Retrieving schema',
  generating: 'Generating SQL',
  validating: 'Validating SQL',
  optimizing: 'Optimizing',
  presenting: 'Presenting SQL',
  executing: 'Executing',
  formatting: 'Formatting results',
}

interface StreamingIndicatorProps {
  steps: QueryStep[]
  isStreaming: boolean
}

export const StreamingIndicator: React.FC<StreamingIndicatorProps> = ({ steps, isStreaming }) => {
  const lastStep = steps[steps.length - 1]
  const progress = lastStep
    ? ((STEP_ORDER.indexOf(lastStep) + 1) / STEP_ORDER.length) * 100
    : 5

  return (
    <div
      style={{
        margin: '8px 16px',
        padding: '12px 16px',
        background: '#2a2a3e',
        border: '1px solid #3a3a5c',
        borderRadius: 8,
      }}
    >
      {/* Step label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        {isStreaming && (
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: '#7c6af7',
              display: 'inline-block',
              animation: 'pulse 1s ease-in-out infinite',
            }}
          />
        )}
        <span style={{ fontSize: 12, color: '#c0c0d8', fontWeight: 500 }}>
          {lastStep ? STEP_LABELS[lastStep] : 'Starting…'}
        </span>
        <span style={{ fontSize: 11, color: '#9090a8', marginLeft: 'auto' }}>
          {Math.round(progress)}%
        </span>
      </div>

      {/* Progress bar */}
      <div
        style={{
          height: 3,
          background: '#3a3a5c',
          borderRadius: 2,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${progress}%`,
            background: 'linear-gradient(90deg, #7c6af7, #6366f1)',
            borderRadius: 2,
            transition: 'width 0.3s ease',
          }}
        />
      </div>

      {/* Completed steps */}
      {steps.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 4,
            marginTop: 8,
          }}
        >
          {steps.map((step) => (
            <span
              key={step}
              style={{
                fontSize: 10,
                padding: '1px 6px',
                borderRadius: 999,
                background: 'rgba(74,222,128,0.12)',
                color: '#4ade80',
                fontWeight: 500,
              }}
            >
              {STEP_LABELS[step]}
            </span>
          ))}
        </div>
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  )
}
