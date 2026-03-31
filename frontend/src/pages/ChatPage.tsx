import React from 'react'
import { ChatPanel } from '../components/chat/ChatPanel'

interface ChatPageProps {
  onOpenInEditor: (sql: string) => void
}

export const ChatPage: React.FC<ChatPageProps> = ({ onOpenInEditor }) => {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <ChatPanel onOpenInEditor={onOpenInEditor} />
    </div>
  )
}
