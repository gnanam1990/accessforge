/**
 * Reading one resource into a component, with the states kept distinct.
 *
 * The return type is the point. It is not `{ data, loading, error }` — with that shape a component
 * renders `data` when it is present and something else when it is not, and "not present" covers a
 * failed request, a cancelled one and an empty answer alike. It is a discriminated union, so a
 * component has to say what it renders for each case and cannot fall through.
 *
 * Two behaviours worth stating:
 *
 * **Every read is abortable, and an abort is not a failure.** Navigating away mid-load aborts the
 * request; the hook then renders nothing rather than a problem notice, because a person who left
 * the page did not encounter an error.
 *
 * **A reload is explicit.** There is no polling and no refetch-on-focus. This product's screens
 * describe durable state that a background refresh could replace under someone's cursor while they
 * were reading it, and UI-UX section 5 requires a person to be able to stop following without the
 * work stopping. `reload` is a function a control calls.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import type { ApiOutcome } from './client'
import type { Problem } from './problem'

export type ResourceState<T> =
  | { readonly kind: 'loading' }
  | { readonly kind: 'ready'; readonly value: T; readonly loadedAt: string }
  | { readonly kind: 'problem'; readonly problem: Problem }
  | { readonly kind: 'offline' }
  /** The session ended while this was loading. The shell handles it; the screen renders nothing. */
  | { readonly kind: 'gone' }

export interface Resource<T> {
  readonly state: ResourceState<T>
  readonly reload: () => void
}

export const useResource = <T>(
  read: (signal: AbortSignal) => Promise<ApiOutcome<T>>,
  dependencies: readonly unknown[],
): Resource<T> => {
  const [state, setState] = useState<ResourceState<T>>({ kind: 'loading' })
  const [generation, setGeneration] = useState(0)
  const inFlight = useRef<AbortController | null>(null)
  // The read function is captured in a ref so that a caller passing an inline closure — which is
  // every caller — does not restart the request on every render. The declared dependencies are what
  // decides when to read again, exactly as they do for `useEffect`.
  const latest = useRef(read)
  latest.current = read

  useEffect(() => {
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    setState({ kind: 'loading' })

    void latest.current(controller.signal).then((outcome) => {
      if (controller.signal.aborted) return
      switch (outcome.kind) {
        case 'ok':
          setState({ kind: 'ready', value: outcome.value, loadedAt: new Date().toISOString() })
          break
        case 'accepted':
          // A read should never be answered with 202. Reported as a problem rather than rendered as
          // data, because whatever this is, it is not the resource that was asked for.
          setState({
            kind: 'problem',
            problem: {
              code: 'UNRECOGNISED',
              title: 'Unexpected response',
              detail:
                'The server accepted this read for later processing. A read has no later; this is ' +
                'not the resource that was requested.',
              status: 202,
              requestId: null,
            },
          })
          break
        case 'offline':
          setState({ kind: 'offline' })
          break
        case 'unauthenticated':
        case 'stale':
          setState({ kind: 'gone' })
          break
        case 'cancelled':
          break
        case 'problem':
          setState({ kind: 'problem', problem: outcome.problem })
          break
      }
    })

    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, generation])

  const reload = useCallback(() => setGeneration((current) => current + 1), [])
  return { state, reload }
}
