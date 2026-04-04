import type { ConversationMessage, QueryResult, QueryStep, TraceStep } from '../types'

interface SSEEvent {
  type: string
  data: Record<string, unknown>
}

function parseSSEBlock(block: string): SSEEvent | null {
  const lines = block.split('\n')
  let eventType = 'message'
  let dataStr = ''

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      eventType = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      dataStr = line.slice(6).trim()
    }
  }

  if (!dataStr) return null
  try {
    return { type: eventType, data: JSON.parse(dataStr) as Record<string, unknown> }
  } catch {
    return null
  }
}

/**
 * Stream a natural-language query via SSE (POST + fetch + ReadableStream).
 * Returns an AbortController — call .abort() to cancel the request.
 */
export function streamQuery(
  userInput: string,
  history: ConversationMessage[],
  onStep: (step: QueryStep) => void,
  onSql: (sql: string) => void,
  onResult: (result: QueryResult & { _trace?: TraceStep[] }) => void,
  onError: (msg: string) => void,
  onClarification?: (question: string, options: string[]) => void,
  onTrace?: (step: TraceStep) => void,
): AbortController {
  const controller = new AbortController()

  ;(async () => {
    try {
      const response = await fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_input: userInput, conversation_history: history }),
        signal: controller.signal,
      })

      if (!response.ok) {
        let msg = `Request failed (${response.status})`
        try {
          const body = await response.json()
          msg = (body as { detail?: string; message?: string }).detail ?? msg
        } catch {
          // ignore
        }
        onError(msg)
        return
      }

      if (!response.body) {
        onError('No response body from server')
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

        // Extract complete SSE blocks (delimited by \n\n)
        let boundary = buffer.indexOf('\n\n')
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)

          const event = parseSSEBlock(block)
          if (event) {
            switch (event.type) {
              case 'step':
                onStep((event.data.step as QueryStep) ?? 'enriching')
                break
              case 'sql':
                onSql((event.data.sql as string) ?? '')
                break
              case 'result':
                onResult(event.data as unknown as QueryResult)
                break
              case 'clarification':
                onClarification?.(
                  (event.data.question as string) ?? '',
                  (event.data.options as string[]) ?? [],
                )
                break
              case 'trace':
                onTrace?.(event.data as unknown as TraceStep)
                break
              case 'error':
                onError((event.data.message as string) ?? 'Unknown error')
                break
            }
          }

          boundary = buffer.indexOf('\n\n')
        }
      }

      // Flush any remaining buffered event (stream closed without trailing \n\n)
      const remaining = buffer.replace(/\r\n/g, '\n').trim()
      if (remaining) {
        const event = parseSSEBlock(remaining)
        if (event) {
          if (event.type === 'result') onResult(event.data as unknown as QueryResult)
          else if (event.type === 'clarification')
            onClarification?.(
              (event.data.question as string) ?? '',
              (event.data.options as string[]) ?? [],
            )
          else if (event.type === 'error') onError((event.data.message as string) ?? 'Unknown error')
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        onError((err as Error).message ?? 'Streaming error')
      }
    }
  })()

  return controller
}
