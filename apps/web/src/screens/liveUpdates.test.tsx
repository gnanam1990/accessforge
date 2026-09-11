/**
 * Following a run's live events, and the inference the stream is never allowed to produce.
 *
 * The test that justifies the whole design is `an event never becomes a verdict`. A published
 * `run.finished` must cause a *re-read*, and what appears on the page must be whatever the server
 * then says — not anything the event carried. A screen that rendered the event's own payload would
 * turn a stream into a source of verdicts, and the server's `events.py` is explicit that it is not
 * one: "An event stream carries references, not state."
 *
 * The rest guard the things a convenient live view gets wrong: starting without being asked, calling
 * a truncated stream a gap, treating the transport closing as the work finishing, and implying that
 * looking away stops anything.
 */

import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'
import type { FakeServer, SessionResponse } from '../test/fakeServer'
import type { EventStream } from '../api/client'
import type { Run } from '../api/resources'

const MEMBER: SessionResponse = {
  userId: 'u-1',
  email: 'engineer@example.test',
  workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role: 'REVIEWER' }],
}

const RUN: Run = {
  runId: 'run-00000001',
  status: 'RUNNING',
  outcome: 'NOT_EVALUATED',
  revision: 3,
  leaseEpoch: 1,
  manifestDigest: 'a'.repeat(64),
  cancellationRequestedAt: null,
  stopAcknowledgedAt: null,
  ambiguityReason: null,
  quarantined: false,
  retryOf: null,
}

/**
 * A stream a test can push frames into.
 *
 * Implements only `addEventListener` and `close`, which is the whole of `EventStream`. A double that
 * reimplemented `EventSource` would be a second copy of a standard, most of it unused and the unused
 * parts free to be wrong.
 */
interface FakeStream extends EventStream {
  emit: (type: string) => void
  readonly closed: () => boolean
  readonly openedUrl: string
}

const streamFactory = (): { readonly open: (url: string) => EventStream; readonly last: () => FakeStream | null } => {
  let last: FakeStream | null = null
  return {
    open: (url: string) => {
      const listeners = new Map<string, ((event: MessageEvent) => void)[]>()
      let closed = false
      const stream: FakeStream = {
        openedUrl: url,
        addEventListener: (type, listener) => {
          listeners.set(type, [...(listeners.get(type) ?? []), listener])
        },
        close: () => {
          closed = true
        },
        emit: (type: string) => {
          for (const listener of listeners.get(type) ?? []) {
            listener(new MessageEvent(type, { data: '{}' }))
          }
        },
        closed: () => closed,
      }
      last = stream
      return stream
    },
    last: () => last,
  }
}

const renderRun = (
  server: FakeServer,
  streams: ReturnType<typeof streamFactory> | null = null,
): void => {
  const client = new ApiClient({
    fetchImpl: server.fetch,
    cookieSource: () => '',
    ...(streams === null ? {} : { eventStreamImpl: streams.open }),
  })
  render(
    <MemoryRouter initialEntries={['/w/ws-1/runs/run-00000001']}>
      <App client={client} />
    </MemoryRouter>,
  )
}

const serverWithRun = (overrides: Partial<Run> = {}): FakeServer => {
  const server = createFakeServer(MEMBER)
  server.data.runs.push({ ...RUN, ...overrides })
  return server
}

const follow = async (): Promise<void> => {
  await userEvent.click(await screen.findByRole('button', { name: 'Follow live updates' }))
}

describe('following live events', () => {
  it('does not follow until somebody asks', async () => {
    // A view that began updating under a reader's cursor would replace what they were reading
    // mid-sentence. UI-UX section 5 makes following a choice.
    const streams = streamFactory()
    renderRun(serverWithRun(), streams)

    expect(await screen.findByRole('button', { name: 'Follow live updates' })).toBeVisible()
    expect(streams.last()).toBeNull()
  })

  it('says what following does before anyone turns it on', async () => {
    const streams = streamFactory()
    renderRun(serverWithRun(), streams)
    await screen.findByRole('button', { name: 'Follow live updates' })
    expect(screen.getByText(/An event is a reference, never a result/)).toBeVisible()
  })

  it('opens the workspace stream when asked', async () => {
    const streams = streamFactory()
    renderRun(serverWithRun(), streams)
    await follow()

    expect(streams.last()?.openedUrl).toBe('/v1/workspaces/ws-1/events/stream')
    expect(await screen.findByRole('button', { name: 'Stop following' })).toBeVisible()
  })

  it('an event never becomes a verdict', async () => {
    // The test this design exists for. The server's run is RUNNING; a `run.finished` event arrives
    // and the screen must show whatever the server says on re-read -- which is still RUNNING --
    // rather than anything the event implied.
    const streams = streamFactory()
    const server = serverWithRun({ status: 'RUNNING', outcome: 'NOT_EVALUATED' })
    renderRun(server, streams)
    await follow()

    streams.last()?.emit('run.finished')

    await waitFor(() => expect(screen.getByText(/1 update so far/)).toBeVisible())
    // Re-read, and the authoritative record has not changed.
    expect(screen.getByText('RUNNING')).toBeVisible()
    expect(screen.queryByText('PASS')).toBeNull()
    expect(screen.queryByText('COMPLETED')).toBeNull()
  })

  it('renders what the server says after an event, not what the event was called', async () => {
    // The other half of the same property: when the record *has* moved, the screen shows the new
    // record -- because it re-read it, not because the event told it so.
    const streams = streamFactory()
    const server = serverWithRun({ status: 'RUNNING', outcome: 'NOT_EVALUATED' })
    renderRun(server, streams)
    await follow()

    server.data.runs[0] = { ...RUN, status: 'INTERRUPTED', outcome: 'INCONCLUSIVE' }
    streams.last()?.emit('run.finished')

    // INTERRUPTED and INCONCLUSIVE, from the record. An event named `run.finished` did not make this
    // a finished, passing run.
    await waitFor(() => expect(screen.getByText('INTERRUPTED')).toBeVisible())
    expect(screen.getByText('INCONCLUSIVE')).toBeVisible()
  })

  it('counts updates without naming them', async () => {
    // A count, not a log. A list of event names on the page would be the stream's contents rendered
    // as content, which is the thing that must not happen.
    const streams = streamFactory()
    renderRun(serverWithRun(), streams)
    await follow()

    streams.last()?.emit('run.leased')
    streams.last()?.emit('evidence.recorded')

    await waitFor(() => expect(screen.getByText(/2 updates so far/)).toBeVisible())
    expect(screen.queryByText(/run\.leased/)).toBeNull()
    expect(screen.queryByText(/evidence\.recorded/)).toBeNull()
  })

  it('stopping stops the updates and closes the stream, and says it stops nothing else', async () => {
    const streams = streamFactory()
    renderRun(serverWithRun(), streams)
    await follow()
    const stream = streams.last()

    expect(screen.getByText(/Stopping stops the updates, not the run/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Stop following' }))

    expect(stream?.closed()).toBe(true)
    expect(await screen.findByRole('button', { name: 'Follow live updates' })).toBeVisible()
  })
})

describe('a truncated stream', () => {
  it('is reported as a reset, not as a gap', async () => {
    // Serving what remains would leave a reader believing they had seen everything since their
    // cursor, and a partial stream is indistinguishable from a complete one to the client receiving
    // it. The screen says the view was out of date and has been re-read.
    const streams = streamFactory()
    renderRun(serverWithRun(), streams)
    await follow()

    streams.last()?.emit('reset')

    expect(await screen.findByText(/Your view was out of date/)).toBeVisible()
    expect(screen.getByText(/This is a reset, not a gap/)).toBeVisible()
    // And the connection is closed rather than continuing from a truncated cursor.
    expect(streams.last()?.closed()).toBe(true)
  })

  it('can be acknowledged, and the notice then goes', async () => {
    const streams = streamFactory()
    renderRun(serverWithRun(), streams)
    await follow()
    streams.last()?.emit('reset')

    await userEvent.click(await screen.findByRole('button', { name: 'Understood' }))
    expect(screen.queryByText(/Your view was out of date/)).toBeNull()
  })
})

describe('what following never claims', () => {
  it('does not treat access ending as the run ending', async () => {
    const streams = streamFactory()
    renderRun(serverWithRun(), streams)
    await follow()

    streams.last()?.emit('access-revoked')

    expect(await screen.findByText(/Live updates stopped/)).toBeVisible()
    expect(screen.getByText(/The run itself is unaffected/)).toBeVisible()
  })

  it('offers no follow control for a run that has already ended', async () => {
    // A terminal run receives no further events. The control would connect, sit silent, and leave a
    // reader wondering what it was waiting for.
    const streams = streamFactory()
    renderRun(serverWithRun({ status: 'COMPLETED', outcome: 'FAIL' }), streams)

    await screen.findByText('COMPLETED')
    expect(screen.queryByRole('button', { name: 'Follow live updates' })).toBeNull()
  })

  it('says so plainly where the browser has no event stream', async () => {
    // Null, not an error. A reader who never asked for a live view should not be shown a failure
    // about a transport they did not request -- the page reads from the API as it always did.
    renderRun(serverWithRun(), null)

    expect(
      await screen.findByText(/This browser cannot open an event stream/),
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Follow live updates' })).toBeNull()
  })
})
