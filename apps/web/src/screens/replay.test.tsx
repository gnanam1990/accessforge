/**
 * Run inspection: what the screen says happened, and what it refuses to say.
 *
 * The acceptance gate for this module is that a fresh reviewer can state what happened, which exact
 * assertion failed, what evidence is unavailable and what safe action is next — and that a reader
 * startup failure is visibly INCONCLUSIVE and cannot acquire a REPRODUCED badge through the
 * interface. Most of these tests are about the second half: the things that must not appear.
 */

import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'
import type { FakeServer, SessionResponse } from '../test/fakeServer'
import type { Run } from '../api/resources'
import { situationFor } from './runStatus'

const MEMBER: SessionResponse = {
  userId: 'u-1',
  email: 'engineer@example.test',
  workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role: 'REVIEWER' }],
}

const RUN: Run = {
  runId: 'run-00000001',
  status: 'COMPLETED',
  outcome: 'FAIL',
  revision: 3,
  leaseEpoch: 1,
  manifestDigest: 'a'.repeat(64),
  cancellationRequestedAt: null,
  stopAcknowledgedAt: null,
  ambiguityReason: null,
  quarantined: false,
  retryOf: null,
}

const renderRun = (server: FakeServer, path = '/w/ws-1/runs/run-00000001'): void => {
  const client = new ApiClient({ fetchImpl: server.fetch, cookieSource: () => '' })
  render(
    <MemoryRouter initialEntries={[path]}>
      <App client={client} />
    </MemoryRouter>,
  )
}

const serverWithRun = (overrides: Partial<Run> = {}): FakeServer => {
  const server = createFakeServer(MEMBER)
  server.data.runs.push({ ...RUN, ...overrides })
  server.data.attempts.push({
    attemptId: 'attempt-1',
    leaseEpoch: 1,
    startedAt: '2026-09-10T12:00:00Z',
    endedAt: '2026-09-10T12:05:00Z',
  })
  return server
}

// --------------------------------------------------------------------------------------------------
// The situation copy, exercised directly: it is the specification for this module
// --------------------------------------------------------------------------------------------------

describe('what a run is described as', () => {
  const run = (overrides: Partial<Run>): Run => ({ ...RUN, ...overrides })

  it('never describes anything as passed unless the server said COMPLETED and PASS', () => {
    const notPassed: Partial<Run>[] = [
      { status: 'QUEUED', outcome: 'NOT_EVALUATED' },
      { status: 'RUNNING', outcome: 'NOT_EVALUATED' },
      { status: 'FINALIZING', outcome: 'NOT_EVALUATED' },
      { status: 'COMPLETED', outcome: 'FAIL' },
      { status: 'COMPLETED', outcome: 'INCONCLUSIVE' },
      { status: 'COMPLETED', outcome: 'NOT_EVALUATED' },
      { status: 'INTERRUPTED', outcome: 'NOT_EVALUATED' },
      { status: 'CANCELLED', outcome: 'NOT_EVALUATED' },
    ]
    for (const overrides of notPassed) {
      const copy = situationFor(run(overrides))
      expect(copy.situation, JSON.stringify(overrides)).not.toBe('PASSED')
      expect(copy.tone, JSON.stringify(overrides)).not.toBe('pass')
      expect(copy.headline.toLowerCase(), JSON.stringify(overrides)).not.toContain('passed')
    }
  })

  it('describes a pass with the scope it does not cover', () => {
    const copy = situationFor(run({ status: 'COMPLETED', outcome: 'PASS' }))
    expect(copy.situation).toBe('PASSED')
    // One execution, one pinned profile, one build. Everything else is untested, and the sentence
    // that says so travels with the result.
    expect(copy.detail).toMatch(/one pinned reader/)
    expect(copy.detail).toMatch(/Everything outside that is untested/)
  })

  it('keeps INTERRUPTED from reading as either an accessible journey or a defect', () => {
    const copy = situationFor(run({ status: 'INTERRUPTED', outcome: 'NOT_EVALUATED' }))
    expect(copy.situation).toBe('INTERRUPTED')
    expect(copy.detail).toMatch(/cannot be resumed/)
    expect(copy.detail).toMatch(/a new run with fresh fixtures/)
  })

  it('says an inconclusive run proved nothing, rather than that it is clean', () => {
    const copy = situationFor(run({ status: 'COMPLETED', outcome: 'INCONCLUSIVE' }))
    expect(copy.detail).toMatch(/not a pass and it is not a defect/)
  })

  it('describes a requested cancellation as unacknowledged, whatever the status says', () => {
    const copy = situationFor(
      run({ status: 'RUNNING', cancellationRequestedAt: '2026-09-10T12:03:00Z' }),
    )
    expect(copy.situation).toBe('CANCELLATION_REQUESTED')
    // The status says RUNNING and the thing a person needs to know is that nothing established the
    // desktop stopped.
    expect(copy.detail).toMatch(/Nothing has established that the desktop stopped/)
  })

  it('separates a proven stop from a cancellation before dispatch', () => {
    const acknowledged = situationFor(
      run({
        status: 'CANCELLED',
        cancellationRequestedAt: '2026-09-10T12:03:00Z',
        stopAcknowledgedAt: '2026-09-10T12:03:30Z',
      }),
    )
    expect(acknowledged.situation).toBe('CANCELLED_STOP_ACKNOWLEDGED')
    // And it does not imply rollback.
    expect(acknowledged.detail).toMatch(/does not undo them/)

    const beforeDispatch = situationFor(run({ status: 'CANCELLED' }))
    expect(beforeDispatch.situation).toBe('CANCELLED_BEFORE_DISPATCH')
  })

  it('reports a pair it does not recognise as unrecognised rather than as the nearest familiar one', () => {
    const copy = situationFor(run({ status: 'SOMETHING_NEW' as Run['status'] }))
    expect(copy.situation).toBe('ENDED_WITHOUT_A_RECOGNISED_OUTCOME')
    expect(copy.detail).toContain('SOMETHING_NEW')
  })
})

// --------------------------------------------------------------------------------------------------
// The screen
// --------------------------------------------------------------------------------------------------

describe('the run screen', () => {
  it('shows status and outcome as separate fields and says why they are separate', async () => {
    renderRun(serverWithRun())
    await screen.findByRole('heading', { level: 1, name: /Run run-0000/ })
    expect(screen.getByText('Status:')).toBeInTheDocument()
    expect(screen.getByText('Outcome:')).toBeInTheDocument()
    expect(
      screen.getByText(/a run can end cleanly having established nothing/),
    ).toBeVisible()
  })

  it('states that per-assertion results are not available rather than showing the run outcome twice', async () => {
    renderRun(serverWithRun())
    await screen.findByRole('heading', { level: 1, name: /Run run-0000/ })

    // The acceptance gate asks a reviewer to state which exact assertion failed. Nothing assembles
    // the evaluator's inputs from a stored attempt, so the screen says so instead of listing the
    // run's outcome once per assertion.
    const section = screen.getByRole('heading', { name: 'Not available in this build' })
    expect(section).toBeVisible()
    expect(
      screen.getByText(/would be this interface inventing the thing it exists to report/),
    ).toBeVisible()
  })

  it('offers cancellation only while the run is not terminal', async () => {
    const { unmount } = render(<div />)
    unmount()

    renderRun(serverWithRun({ status: 'RUNNING', outcome: 'NOT_EVALUATED' }))
    expect(
      await screen.findByRole('button', { name: 'Request cancellation' }),
    ).toBeInTheDocument()
  })

  it('does not offer cancellation on a terminal run', async () => {
    renderRun(serverWithRun({ status: 'COMPLETED', outcome: 'FAIL' }))
    await screen.findByRole('heading', { level: 1, name: /Run run-0000/ })
    expect(screen.queryByRole('button', { name: 'Request cancellation' })).not.toBeInTheDocument()
  })

  it('reports a cancellation as requested, never as done', async () => {
    const user = userEvent.setup()
    const server = serverWithRun({ status: 'RUNNING', outcome: 'NOT_EVALUATED' })
    renderRun(server)

    await user.click(await screen.findByRole('button', { name: 'Request cancellation' }))
    // Scoped by name: the pagination control is also a status region on this page.
    const notice = await screen.findByRole('status', { name: 'Cancellation' })
    expect(notice).toHaveTextContent(/Cancellation is requested/)
    // The word "stopped" does appear — inside "Nothing has established that the desktop stopped".
    // What must not appear is the claim, so the assertion is about the claim rather than the word.
    expect(notice).toHaveTextContent(/Nothing has established that the desktop stopped/)
    expect(notice.textContent).not.toMatch(/(has been|was) cancelled|(has|had) stopped\b/i)
  })

  it('names an ambiguous effect rather than folding it into the status', async () => {
    renderRun(
      serverWithRun({
        status: 'INTERRUPTED',
        outcome: 'NOT_EVALUATED',
        ambiguityReason: 'a submit action was dispatched and no result arrived',
      }),
    )
    await screen.findByRole('heading', { level: 1, name: /Run run-0000/ })
    expect(screen.getByRole('heading', { name: 'An effect is ambiguous' })).toBeVisible()
    expect(screen.getByText(/It may have taken effect/)).toBeVisible()
  })

  it('says a retry is a new run and does not resume one', async () => {
    renderRun(serverWithRun({ retryOf: 'run-00000000' }))
    await screen.findByRole('heading', { level: 1, name: /Run run-0000/ })
    expect(screen.getByText(/a retry never resumes one/)).toBeVisible()
  })

  it('distinguishes a run with no attempt from an attempt that recorded nothing', async () => {
    const server = createFakeServer(MEMBER)
    server.data.runs.push(RUN)
    renderRun(server)

    await screen.findByRole('heading', { level: 1, name: /Run run-0000/ })
    expect(
      await screen.findByRole('heading', { name: 'No attempt was ever started' }),
    ).toBeVisible()
    expect(screen.getByText(/not the same as an attempt that ran and recorded nothing/)).toBeVisible()
  })
})

// --------------------------------------------------------------------------------------------------
// Evidence completeness — where a replay screen can become a reassurance
// --------------------------------------------------------------------------------------------------

describe('evidence completeness', () => {
  it('reports a contiguous chain with an open producer as both, not as complete', async () => {
    const server = serverWithRun()
    server.data.completeness = {
      reasons: [
        'required producers have not closed their streams: observer-1. A contiguous chain does ' +
          'not cover this — a producer that stopped halfway leaves a perfect chain and half the ' +
          'evidence.',
      ],
      contiguous: true,
      producersClosed: false,
      artifactsPresent: true,
      lifecycleBounded: true,
      producers: [
        { producerId: 'supervisor-1', admittedThrough: 4, closedAt: 4 },
        { producerId: 'observer-1', admittedThrough: 2, closedAt: null },
      ],
      meaning: 'This describes the evidence, not the run.',
    }
    renderRun(server)

    await screen.findByRole('heading', { name: 'Evidence completeness' })
    expect(await screen.findByText('No gaps')).toBeVisible()
    expect(await screen.findByText('Some still open')).toBeVisible()
    // "Never closed" and "closed at position zero" are different statements.
    expect(screen.getByText(/never closed its stream/)).toBeVisible()
  })

  it('never renders an outcome or a verdict in the completeness section', async () => {
    const server = serverWithRun()
    renderRun(server)
    const heading = await screen.findByRole('heading', { name: 'Evidence completeness' })
    const section = heading.closest('section') as HTMLElement
    // Awaited: the heading renders before the resource resolves, so a synchronous query here would
    // assert against an empty section and pass for the wrong reason.
    expect(
      await within(section).findByText(/complete set can still describe a failure/),
    ).toBeVisible()
    // Completeness is an input to a verdict, not a verdict.
    expect(within(section).queryByText(/\bPASS\b/)).toBeNull()
  })

  it('says an attempt with no producers is missing evidence, not uneventful', async () => {
    const server = serverWithRun()
    server.data.completeness = {
      reasons: ['the attempt has no RUN_STARTED and RUN_FINISHED pair'],
      contiguous: true,
      producersClosed: true,
      artifactsPresent: true,
      lifecycleBounded: false,
      producers: [],
      meaning: 'This describes the evidence, not the run.',
    }
    renderRun(server)
    expect(
      await screen.findByText(/That is missing evidence, not an uneventful run/),
    ).toBeVisible()
  })
})

// --------------------------------------------------------------------------------------------------
// The timeline
// --------------------------------------------------------------------------------------------------

const EVENTS = [
  {
    sequence: 1,
    eventId: 'e-1',
    eventType: 'RUN_STARTED',
    leaseEpoch: 1,
    sourceTime: '2026-09-10T12:00:00Z',
    receivedTime: '2026-09-10T12:00:01Z',
    payloadDigest: 'b'.repeat(64),
    previousEventHash: '0'.repeat(64),
    payload: { manifest: 'sealed' },
    producerId: 'supervisor-1',
    producerSequence: 1,
    sourceRecordDigest: 'c'.repeat(64),
  },
  {
    sequence: 2,
    eventId: 'e-2',
    eventType: 'READER_OBSERVATION',
    leaseEpoch: 1,
    // Earlier than the first event's, deliberately: the producers' clocks disagree.
    sourceTime: '2026-09-10T11:00:00Z',
    receivedTime: '2026-09-10T12:00:02Z',
    payloadDigest: 'd'.repeat(64),
    previousEventHash: 'b'.repeat(64),
    payload: { phrase: 'Email address, edit text, invalid entry' },
    producerId: 'observer-1',
    producerSequence: 1,
    sourceRecordDigest: 'e'.repeat(64),
  },
]

const withTimeline = (): FakeServer => {
  const server = serverWithRun()
  server.data.timeline = {
    events: EVENTS,
    nextAfterSequence: 2,
    exhausted: true,
    orderingMeaning:
      'Ordered by the sequence the trusted sequencer assigned, not by any clock. Source times come ' +
      'from the producers’ own machines and disagree with each other.',
  }
  return server
}

describe('the evidence timeline', () => {
  it('is a semantic ordered list, in the sequencer’s order', async () => {
    renderRun(withTimeline())
    await screen.findByRole('heading', { name: 'Evidence timeline' })

    const entries = await screen.findAllByRole('listitem')
    const timeline = entries.filter((item) => /RUN_STARTED|READER_OBSERVATION/.test(item.textContent ?? ''))
    // The second event's clock reads an hour earlier than the first's. Order comes from the
    // sequence, so a reader is not shown an order that never happened.
    expect(timeline[0]?.textContent).toContain('RUN_STARTED')
    expect(timeline[1]?.textContent).toContain('READER_OBSERVATION')
  })

  it('labels who recorded each event', async () => {
    renderRun(withTimeline())
    await screen.findByRole('heading', { name: 'Evidence timeline' })
    // A supervisor receipt and an observer receipt are different kinds of claim, and the difference
    // is a labelled field rather than a colour.
    expect(await screen.findByText('supervisor-1')).toBeVisible()
    expect(screen.getByText('observer-1')).toBeVisible()
    expect(screen.getAllByText(/producers’ clocks disagree/).length).toBeGreaterThan(0)
  })

  it('keeps the recorded payload behind a keyboard-operable disclosure', async () => {
    const user = userEvent.setup()
    renderRun(withTimeline())
    await screen.findByRole('heading', { name: 'Evidence timeline' })

    const toggle = await screen.findByRole('button', { name: 'Show what was recorded at #2' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)

    expect(
      screen.getByRole('button', { name: 'Hide what was recorded at #2' }),
    ).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/Email address, edit text, invalid entry/)).toBeVisible()
  })

  it('renders every event on the page, with no virtualization to be equivalent to', async () => {
    renderRun(withTimeline())
    await screen.findByRole('heading', { name: 'Evidence timeline' })
    // Both entries are in the DOM at once. A virtualized list would need a complete accessible mode
    // beside it; this is that mode.
    const entries = await screen.findAllByRole('listitem')
    const positions = entries
      .map((item) => item.querySelector('.af-mono')?.textContent)
      .filter((text): text is string => text !== undefined && text !== null)
    expect(positions).toEqual(['#1', '#2'])
  })

  it('says a filtered view is filtered', async () => {
    const user = userEvent.setup()
    renderRun(withTimeline())
    await screen.findByRole('heading', { name: 'Evidence timeline' })

    await user.type(screen.getByLabelText(/Search this page/), 'READER')
    // A filtered list that looked like the whole chain would present a gap as an absence of events.
    expect(await screen.findByText(/This is a filtered view, not the whole attempt/)).toBeVisible()
  })

  it('calls an empty page missing evidence rather than a quiet journey', async () => {
    renderRun(serverWithRun())
    await screen.findByRole('heading', { name: 'Evidence timeline' })
    expect(
      await screen.findByRole('heading', { name: 'No events on this page' }),
    ).toBeVisible()
    expect(
      screen.getByText(/missing evidence, not a quiet successful journey/),
    ).toBeVisible()
  })

  it('claims the end of the chain only when the server said the page was short', async () => {
    const server = serverWithRun()
    server.data.timeline = {
      events: EVENTS,
      nextAfterSequence: 2,
      exhausted: false,
      orderingMeaning: 'Ordered by the sequence the trusted sequencer assigned.',
    }
    renderRun(server)
    await screen.findByRole('heading', { name: 'Evidence timeline' })
    expect(await screen.findByText(/There are more\./)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()
  })
})
