/**
 * One live region for the whole application, with a bounded queue.
 *
 * UI-UX section 8 asks for "one restrained live-region strategy", and section 5 is specific about
 * what restraint means: announce "Run interrupted; 1 unresolved action", not every streamed token.
 * So this is a single polite region, and the things it is allowed to say are summaries.
 *
 * Three properties:
 *
 * **Polite, always.** An assertive region interrupts the reader mid-sentence. Nothing in this
 * product is urgent enough to justify that — a genuine alert belongs in a `Notice` with
 * `role="alert"`, on the page, where it can be re-read.
 *
 * **Bounded.** At most `MAX_PENDING` messages are held. A background event stream can produce
 * hundreds per minute, and a queue that grew without limit would still be reading out the first
 * minute's events long after they stopped mattering.
 *
 * **Deduplicated against the last message.** The same text twice in a row is usually a re-render,
 * not news, and the cost of being wrong is a single missed repetition rather than a reader who hears
 * the same sentence nine times.
 *
 * Announcing never moves focus. Focus belongs to the person.
 */

import type { JSX } from 'react'

import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

const MAX_PENDING = 3

interface PendingMessage {
  readonly id: number
  readonly text: string
}

interface AnnouncerApi {
  readonly announce: (message: string) => void
}

const AnnouncerContext = createContext<AnnouncerApi | null>(null)

export const AnnouncerProvider = ({ children }: { readonly children: ReactNode }): JSX.Element => {
  const [messages, setMessages] = useState<readonly PendingMessage[]>([])
  const last = useRef<string | null>(null)
  const nextId = useRef(0)

  const announce = useCallback((message: string) => {
    if (message === last.current) return
    last.current = message
    nextId.current += 1
    const entry = { id: nextId.current, text: message }
    setMessages((current) => [...current, entry].slice(-MAX_PENDING))
  }, [])

  const api = useMemo(() => ({ announce }), [announce])

  return (
    <AnnouncerContext.Provider value={api}>
      {children}
      <div aria-live="polite" aria-atomic="false" className="af-visually-hidden">
        {messages.map((message) => (
          // Keyed by a monotonic id, not by position or text. Position changes for every retained
          // message as soon as the queue is full and shifts, which recreates their <p> elements —
          // and a live region reads a recreated node again. The result would be the fourth
          // announcement re-reading the second and third, which is precisely the flood this
          // component exists to prevent. Text alone would collapse two identical announcements that
          // were separated by a third, and both of those should be read.
          <p key={message.id}>{message.text}</p>
        ))}
      </div>
    </AnnouncerContext.Provider>
  )
}

export const useAnnouncer = (): AnnouncerApi => {
  const api = useContext(AnnouncerContext)
  if (api === null) {
    throw new Error('useAnnouncer requires an AnnouncerProvider above it')
  }
  return api
}
