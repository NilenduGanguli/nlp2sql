import React from 'react'

interface KycAutoAnswerBadgeProps {
  question: string
  autoAnswer: string
  source: string // "learned_pattern" | "knowledge_base"
}

const SOURCE_CONFIG: Record<string, { label: string; color: string }> = {
  learned_pattern: { label: 'Learned', color: '#34d399' },
  knowledge_base: { label: 'Knowledge Base', color: '#60a5fa' },
}

export const KycAutoAnswerBadge: React.FC<KycAutoAnswerBadgeProps> = ({
  question,
  autoAnswer,
  source,
}) => {
  const config = SOURCE_CONFIG[source] ?? SOURCE_CONFIG.knowledge_base

  return (
    <div
      style={{
        display: 'inline-block',
        background: 'rgba(42,42,62,0.7)',
        borderLeft: `3px solid ${config.color}`,
        borderRadius: '0 6px 6px 0',
        padding: '8px 12px',
        maxWidth: '85%',
        fontSize: 12,
        lineHeight: 1.5,
      }}
    >
      {/* First line: question */}
      <div style={{ color: '#c0c0d8' }}>
        <span style={{ fontWeight: 600, color: config.color, marginRight: 4 }}>
          Auto-answered:
        </span>
        {question}
      </div>

      {/* Second line: answer */}
      <div style={{ color: '#8a8aac', marginTop: 2 }}>
        <span style={{ fontWeight: 500, marginRight: 4 }}>Answer:</span>
        {autoAnswer}
      </div>

      {/* Source tag */}
      <span
        style={{
          display: 'inline-block',
          marginTop: 4,
          padding: '1px 7px',
          background: `${config.color}18`,
          border: `1px solid ${config.color}40`,
          borderRadius: 999,
          fontSize: 10,
          fontWeight: 600,
          color: config.color,
          letterSpacing: '0.02em',
        }}
      >
        {config.label}
      </span>
    </div>
  )
}
