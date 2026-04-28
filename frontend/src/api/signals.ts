import type { SignalEvent } from '../types'

export async function postSignal(event: SignalEvent): Promise<void> {
  try {
    await fetch('/api/signals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(event),
    })
  } catch (err) {
    console.warn('signal post failed', event.event, err)
  }
}

export function sha1Hex(s: string): Promise<string> {
  const enc = new TextEncoder().encode(s)
  return crypto.subtle.digest('SHA-1', enc).then((buf) => {
    return Array.from(new Uint8Array(buf))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('')
  })
}
