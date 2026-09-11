/**
 * Following a workspace's live events, and what the stream is never allowed to tell you.
 *
 * **An event triggers a re-read. It never carries state into the screen.** The server's own
 * `events.py` says it: "An event stream carries references, not state." So this hook's entire output
 * is a counter and a status — nothing from an event's payload reaches a component. A hook that
 * returned the events themselves would invite a screen to render `run.finished` as a finished run,
 * which is the inference this product exists to prevent. What the screen renders always comes from
 * re-reading the authoritative record.
 *
 * **Following is opt-in and stoppable.** UI-UX section 5 requires that a person can stop following
 * without the work stopping. So `following` starts false, a control turns it on, and turning it off
 * closes the connection and changes nothing about the run.
 *
 * **A `reset` is a reset, not a gap.** If the cursor falls below what retention holds, the server
 * says so explicitly and names the snapshot. The hook surfaces that as its own status so the screen
 * can say "your view was out of date and has been reloaded" rather than silently continuing from a
 * truncated stream — which would leave a reader believing they had seen everything since.
 *
 * **Ending is not finishing.** The server closes a stream on its own schedule and the browser
 * reconnects with `Last-Event-ID`. Neither the close nor the silence between means a run ended, and
 * the status this hook reports for it says `following` throughout rather than anything that reads
 * like completion.
 */

import { useEffect, useRef, useState } from 'react'

import type { ApiClient } from './client'

export type LiveStatus =
  /** Not following. The initial state, and where `stop()` returns to. */
  | 'idle'
  /** Connected, or reconnecting on its own. Both are "following": a reader must not be told the
   * stream broke for a gap the browser is already closing. */
  | 'following'
  /** The cursor fell below retention. The screen has been told to reload from authoritative state. */
  | 'reset'
  /** Access to this workspace ended while the stream was open. */
  | 'accessEnded'
  /** This environment has no event stream at all. Nothing is wrong; nothing is live either. */
  | 'unavailable'

export interface LiveEvents {
  readonly status: LiveStatus
  /** Increments on every event the server published. A screen re-reads when this changes. */
  readonly changes: number
  /** True while a reset is unacknowledged, so a screen can say the view was stale. */
  readonly wasReset: boolean
  readonly start: () => void
  readonly stop: () => void
  readonly acknowledgeReset: () => void
}

/**
 * Subscribe to a workspace's events while `following`.
 *
 * `onChange` is called for every published event, after the counter moves. It is where a screen puts
 * its `reload()` calls — deliberately the only thing an event can cause.
 */
export const useLiveEvents = (
  client: ApiClient,
  workspaceId: string,
  onChange: () => void,
): LiveEvents => {
  // Availability is known at mount, not discovered by trying. A screen has to decide whether to
  // *offer* following before anyone asks for it, and an offer that cannot be honoured is worse than
  // none: the reader presses the button and learns the truth afterwards.
  const available = client.canOpenEventStream
  const [status, setStatus] = useState<LiveStatus>(available ? 'idle' : 'unavailable')
  const [changes, setChanges] = useState(0)
  const [wasReset, setWasReset] = useState(false)
  const [following, setFollowing] = useState(false)

  // The callback is held in a ref so that a screen passing an inline closure -- which every screen
  // does -- does not tear down and reopen the connection on every render. Reconnecting once per
  // render would make a live view cost more than polling and lose its cursor each time.
  const latest = useRef(onChange)
  latest.current = onChange

  useEffect(() => {
    if (!available) {
      setStatus('unavailable')
      return
    }
    if (!following) {
      setStatus('idle')
      return
    }

    const stream = client.openEventStream(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/events/stream`,
    )
    if (stream === null) {
      setStatus('unavailable')
      return
    }
    setStatus('following')

    // One listener per server-sent event type this application understands, and no catch-all.
    // `EventSource` delivers an unnamed event as `message`; every frame the server sends is named,
    // so a `message` arriving at all would mean the protocol had changed and is better ignored than
    // guessed at.
    const onPublished = (): void => {
      setChanges((count) => count + 1)
      latest.current()
    }

    const onReset = (): void => {
      // The cursor is older than retention. The screen must discard what it has and re-read rather
      // than continue from a truncated stream, so this both flags the staleness and asks for a read.
      setStatus('reset')
      setWasReset(true)
      latest.current()
      stream.close()
    }

    const onAccessEnded = (): void => {
      setStatus('accessEnded')
      stream.close()
    }

    // Every topic the outbox publishes. Listed rather than wildcarded, because a stream that
    // reloaded on an unrecognised event would make this screen react to messages added for somebody
    // else's consumer -- and one that ignored a *new* run topic would silently stop being live.
    // Adding a topic here is the same commit as adding it to the outbox.
    for (const topic of [
      'run.requested',
      'run.leased',
      'run.running',
      'run.finalizing',
      'run.finished',
      'run.interrupted',
      'run.cancelled',
      'evidence.recorded',
      'finding.recorded',
    ]) {
      stream.addEventListener(topic, onPublished)
    }
    stream.addEventListener('reset', onReset)
    stream.addEventListener('access-revoked', onAccessEnded)
    // `stream-ended` is the server closing on its own schedule. Deliberately no listener: the
    // browser reconnects with Last-Event-ID by itself, and reacting to it would mean showing a
    // reader something about a gap the transport is already closing. It is also, emphatically, not
    // a statement that anything finished.

    return () => {
      stream.close()
    }
  }, [client, workspaceId, following, available])

  return {
    status,
    changes,
    wasReset,
    // A no-op where there is nothing to follow, so a caller cannot move the screen into a state
    // that claims to be following a stream that was never opened.
    start: () => setFollowing(available),
    stop: () => setFollowing(false),
    acknowledgeReset: () => setWasReset(false),
  }
}
