import React from 'react'

type StatusValue = 'ok' | 'error' | 'warning' | 'unknown'

interface StatusPillProps {
  status: StatusValue
  label: string
  tooltip?: string
}

const COLOR_MAP: Record<StatusValue, { bg: string; text: string; dot: string }> = {
  ok: { bg: 'rgba(74,222,128,0.15)', text: '#4ade80', dot: '#4ade80' },
  error: { bg: 'rgba(248,113,113,0.15)', text: '#f87171', dot: '#f87171' },
  warning: { bg: 'rgba(251,191,36,0.15)', text: '#fbbf24', dot: '#fbbf24' },
  unknown: { bg: 'rgba(144,144,168,0.15)', text: '#9090a8', dot: '#9090a8' },
}

export const StatusPill: React.FC<StatusPillProps> = ({ status, label, tooltip }) => {
  const colors = COLOR_MAP[status] ?? COLOR_MAP.unknown

  return (
    <span
      title={tooltip}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: '2px 8px',
        borderRadius: 999,
        background: colors.bg,
        color: colors.text,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: '0.02em',
        whiteSpace: 'nowrap',
        userSelect: 'none',
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: colors.dot,
          flexShrink: 0,
        }}
      />
      {label}
    </span>
  )
}
