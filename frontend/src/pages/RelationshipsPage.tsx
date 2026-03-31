import React from 'react'
import { FkConstraintTable } from '../components/relationships/FkConstraintTable'
import { JoinPathExplorer } from '../components/relationships/JoinPathExplorer'
import { ColumnBrowser } from '../components/relationships/ColumnBrowser'

export const RelationshipsPage: React.FC = () => {
  return (
    <div
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '16px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: 20,
      }}
    >
      {/* FK Constraint Table */}
      <FkConstraintTable />

      {/* Join Path Explorer */}
      <JoinPathExplorer />

      {/* Column Browser */}
      <ColumnBrowser />
    </div>
  )
}
