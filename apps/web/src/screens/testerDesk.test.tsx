import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it } from 'vitest'
import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'

/**
 * Tester Desk surfaces against a stub API. The stub answers the tester-desk endpoints;
 * session/workspace endpoints delegate to the shared fake server.
 */

const WS = 'ws-1'
const TD = `/v1/workspaces/${WS}/tester-desk`

const brief = {
  releaseLabel: 'r18',
  taskSteps: [
    { stepId: 's1', title: 'Open the checkout' },
    { stepId: 's4', title: 'Advance past shipping' },
    { stepId: 's5', title: 'Complete payment' },
  ],
  environment: { browserName: 'Safari', browserVersion: '18', assistiveTechnology: 'VoiceOver', inputMode: 'KEYBOARD_ONLY' },
  consentTermsVersion: 1,
}

const assignment = {
  assignmentId: 'a-1', commissionId: 'c-1', briefRevision: 1, briefHash: 'f'.repeat(64),
  status: 'COMPLETED', expiresAt: null, brief,
}

const submission = {
  submissionId: 'sub-1', revision: 1, submissionHash: 'e'.repeat(64), taskResult: 'FAILED',
  observations: [
    { stepId: 's4', result: 'FAILED', notes: 'focus trap', source: 'HUMAN_MANUAL', observedAt: '2026-11-20T10:00:00Z' },
    { stepId: 's5', result: 'UNREACHABLE', notes: 'cannot reach', source: 'HUMAN_MANUAL', observedAt: '2026-11-20T10:01:00Z' },
  ],
  attachmentManifest: [{ attachmentId: 'att-1', kind: 'ACTION_TRACE', sha256: 'd'.repeat(64) }],
  sealedAt: '2026-11-20T11:00:00Z',
  decision: { decision: 'ACCEPTED', justification: 'complete evidence of the trap' },
}

interface Routes { [path: string]: (init?: RequestInit) => Response | Promise<Response> }

const mount = (
  entry: string,
  routes: Routes,
  workspaces = [{ workspaceId: WS, name: 'Fixture', role: 'OWNER' }],
) => {
  const server = createFakeServer({
    userId: 'u-1', email: 'tester@example.test',
    workspaces,
  })
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    for (const [prefix, handler] of Object.entries(routes)) {
      if (path.includes(prefix)) return handler(init)
    }
    return server.fetch(input, init)
  }) as typeof fetch
  const client = new ApiClient({ fetchImpl, cookieSource: () => 'accessforge_csrf=fixture' })
  render(<MemoryRouter initialEntries={[entry]}><App client={client} /></MemoryRouter>)
  return { server }
}

const ok = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

it('T-015: capture screen shows the human observation and BOTH outcome fields', async () => {
  mount(`/w/${WS}/tester-desk/tester/assignments/a-1`, {
    [`${TD}/tester/assignments/a-1/draft`]: () => ok({
      assignmentId: 'a-1', draftRevision: 3, observations: submission.observations, updatedAt: '2026-11-20T10:05:00Z',
    }),
    [`${TD}/tester/assignments/a-1/submissions`]: () => ok({ items: [submission] }),
    [`${TD}/tester/assignments/a-1/attachments`]: () => ok({
      items: [{
        attachmentId: 'att-1', assignmentId: 'a-1', kind: 'ACTION_TRACE',
        contentType: 'application/json', filename: 'action-trace.json',
        sha256: 'ab'.repeat(32), byteSize: 512, createdAt: '2026-11-20T10:30:00Z',
      }],
    }),
    [`${TD}/tester/assignments/a-1/progress`]: () => ok({ submittable: true, missing: [], taskResult: 'FAILED', draftRevision: 3 }),
    [`${TD}/tester/assignments/a-1/payable`]: () => ok({
      payable: { payableId: 'p-1', amountMinor: 2000, currency: 'USD', status: 'SETTLED', createdAt: '2026-11-20T11:05:00Z' },
    }),
    [`${TD}/tester/assignments/a-1`]: () => ok(assignment),
  })

  // The human observation row: step, its FAILED result, and the tester's note.
  const subs = await screen.findByRole('table', { name: /sealed submissions/i })
  const row = within(subs).getAllByRole('row')[1]!
  expect(within(row).getByText('FAILED')).toBeInTheDocument()
  expect(within(row).getByText('ACCEPTED')).toBeInTheDocument()
  expect(within(row).getByText('complete evidence of the trap')).toBeInTheDocument()

  // The digest-backed attachment manifest renders beside the outcome fields.
  const evidence = await screen.findByRole('table', { name: /digest-backed evidence/i })
  expect(within(evidence).getByText('action-trace.json')).toBeInTheDocument()
  expect(within(evidence).getByText('ACTION_TRACE')).toBeInTheDocument()

  // Payable is settled and shows the pinned fee (amount and currency are split text nodes).
  expect(
    await screen.findByText((_, el) => el?.tagName === 'P' && (el.textContent ?? '').includes('20.00 USD')),
  ).toBeInTheDocument()
})

it('T-016: payout adapter disconnected — payable stays pending while findings stay available', async () => {
  mount(`/w/${WS}/tester-desk`, {
    [`${TD}/commissions`]: () => ok({
      items: [{
        commissionId: 'c-1', reference: 'AF-21', status: 'ACCEPTED', defectKey: 'focus-trap-s4',
        predecessorCommissionId: null, offerExpiresAt: null, currentBriefRevision: 1,
        createdAt: '2026-11-20T09:00:00Z',
        brief: { releaseLabel: 'r18', feeMinor: 2000, currency: 'USD', environment: brief.environment },
      }],
    }),
    [`${TD}/review/queue`]: () => ok({ items: [] }),
    [`${TD}/finance/payables`]: () => ok({
      items: [{
        payableId: 'p-1', assignmentId: 'a-1', amountMinor: 2000, currency: 'USD',
        status: 'INTENT', providerReference: null, createdAt: '2026-11-20T11:05:00Z',
      }],
    }),
  })

  // The payable is honestly PENDING DISPATCH, not a fake green.
  const payables = await screen.findByRole('table', { name: /compensation payables/i })
  expect(within(payables).getByText('PENDING DISPATCH')).toBeInTheDocument()
  expect(within(payables).queryByText('SETTLED')).not.toBeInTheDocument()

  // The accepted commission/finding remains visible — disconnect ate only the money leg.
  const commissions = screen.getByRole('table', { name: /test commissions/i })
  expect(within(commissions).getByText('AF-21')).toBeInTheDocument()
  expect(within(commissions).getByText('ACCEPTED')).toBeInTheDocument()
})

const pendingSubmission = { ...submission, decision: null }

const captureStubs = (extra: Routes = {}): Routes => ({
  [`${TD}/tester/assignments/a-1/submissions`]: () => ok({ items: [] }),
  [`${TD}/tester/assignments/a-1/attachments`]: () => ok({ items: [] }),
  [`${TD}/tester/assignments/a-1/progress`]: () =>
    ok({ submittable: true, missing: [], taskResult: 'FAILED', draftRevision: 1 }),
  [`${TD}/tester/assignments/a-1/payable`]: () => ok({ payable: null }),
  // Specific sub-paths before the bare assignment — `a-1` is a prefix of every one of them,
  // and handlers match on inclusion in declaration order.
  ...extra,
  [`${TD}/tester/assignments/a-1`]: () => ok({ ...assignment, status: 'ACTIVE' }),
})

it('participants land on their own surfaces from the workspace chooser', async () => {
  mount('/workspaces', {}, [
    { workspaceId: 'ws-t', name: 'TesterCo', role: 'TESTER' },
    { workspaceId: 'ws-c', name: 'ClientCo', role: 'CLIENT' },
    { workspaceId: 'ws-o', name: 'MemberCo', role: 'OWNER' },
  ])

  const nav = await screen.findByRole('navigation', { name: 'Your workspaces' })
  expect(within(nav).getAllByRole('link').map((l) => l.getAttribute('href'))).toEqual([
    '/w/ws-t/tester-desk/tester',
    '/w/ws-c/tester-desk/client',
    '/w/ws-o/overview',
  ])
})

it('the workspace switcher takes a participant to their surface, not the member overview', async () => {
  const user = userEvent.setup()
  mount(`/w/ws-t/tester-desk/tester`, {
    '/v1/workspaces/ws-t/tester-desk/tester/offers': () => ok({ items: [] }),
    '/v1/workspaces/ws-t/tester-desk/tester/assignments': () => ok({ items: [] }),
    '/v1/workspaces/ws-c/tester-desk/client/reports': () => ok({ items: [] }),
  }, [
    { workspaceId: 'ws-t', name: 'TesterCo', role: 'TESTER' },
    { workspaceId: 'ws-c', name: 'ClientCo', role: 'CLIENT' },
  ])

  await screen.findByRole('heading', { level: 1, name: 'My test work' })
  await user.selectOptions(screen.getByLabelText('Workspace'), 'ws-c')
  // Landing on the overview would render a member surface the server refuses for a CLIENT.
  await screen.findByRole('heading', { level: 1, name: 'Test reports' })
})

it('the reviewer sees the sealed observations and evidence manifest before deciding', async () => {
  mount(`/w/${WS}/tester-desk`, {
    [`${TD}/commissions`]: () => ok({ items: [] }),
    [`${TD}/reports/ready`]: () => ok({ items: [] }),
    [`${TD}/review/queue`]: () =>
      ok({ items: [{ assignmentId: 'a-1', reference: 'AF-21', latestSubmission: pendingSubmission }] }),
    [`${TD}/review/assignments/a-1`]: () =>
      ok({ ...assignment, status: 'UNDER_REVIEW', submissions: [pendingSubmission] }),
    [`${TD}/finance/payables`]: () => ok({ items: [] }),
  })

  const observations = await screen.findByRole('table', { name: /sealed observations for af-21/i })
  expect(within(observations).getByText('Advance past shipping')).toBeInTheDocument()
  expect(within(observations).getByText('FAILED')).toBeInTheDocument()
  expect(within(observations).getByText('focus trap')).toBeInTheDocument()

  const manifest = screen.getByRole('table', { name: /sealed evidence manifest for af-21/i })
  expect(within(manifest).getByText('ACTION_TRACE')).toBeInTheDocument()
  expect(within(manifest).getByRole('button', { name: 'View evidence' })).toBeInTheDocument()

  // Nothing offers report generation before the evidence is accepted.
  expect(screen.queryByRole('button', { name: /client report/i })).not.toBeInTheDocument()
})

it('accepted evidence lands on the durable report list and generation updates it', async () => {
  const user = userEvent.setup()
  let queueItems: unknown[] = [
    { assignmentId: 'a-1', reference: 'AF-21', latestSubmission: pendingSubmission },
  ]
  let readyItems: unknown[] = []
  const calls: string[] = []
  mount(`/w/${WS}/tester-desk`, {
    [`${TD}/commissions`]: () => ok({ items: [] }),
    [`${TD}/reports/generate`]: () => {
      calls.push('generate')
      readyItems = [{ assignmentId: 'a-1', commissionId: 'c-1', reference: 'AF-21', latestReportRevision: 1 }]
      return ok({}, 201)
    },
    [`${TD}/reports/ready`]: () => ok({ items: readyItems }),
    [`${TD}/review/queue`]: () => ok({ items: queueItems }),
    [`${TD}/review/assignments/a-1/decision`]: () => {
      calls.push('decision')
      queueItems = []
      readyItems = [{ assignmentId: 'a-1', commissionId: 'c-1', reference: 'AF-21', latestReportRevision: null }]
      return ok({})
    },
    [`${TD}/review/assignments/a-1`]: () =>
      ok({ ...assignment, status: 'UNDER_REVIEW', submissions: [pendingSubmission] }),
    [`${TD}/finance/payables`]: () => ok({ items: [] }),
  })

  await screen.findByRole('table', { name: /sealed observations for af-21/i })
  await user.type(screen.getByLabelText(/^Justification/), 'complete evidence of the trap')
  await user.click(screen.getByRole('button', { name: 'Accept evidence' }))

  // The card leaves the queue; the accepted assignment surfaces on the durable list.
  const generate = await screen.findByRole('button', { name: 'Generate client report' })
  await user.click(generate)
  await screen.findByText('REPORT r1 GENERATED')
  expect(calls).toEqual(['decision', 'generate'])
})

it('a draft answer arriving after the tester started typing does not overwrite the edit', async () => {
  const user = userEvent.setup()
  let releaseDraft: (response: Response) => void = () => undefined
  const held = new Promise<Response>((resolve) => { releaseDraft = resolve })
  const puts: { baseRevision: number }[] = []
  mount(`/w/${WS}/tester-desk/tester/assignments/a-1`, captureStubs({
    [`${TD}/tester/assignments/a-1/draft`]: (init) => {
      if (init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)) as { baseRevision: number })
        return ok({ assignmentId: 'a-1', draftRevision: 8, observations: [], updatedAt: null })
      }
      return held
    },
  }))

  const notes = await screen.findByLabelText('Notes for Open the checkout')
  await user.type(notes, 'typed while the draft was in flight')

  // The delayed answer carries a different note and a newer revision. Both matter: the
  // revision is adopted (the next save is not stale) while the typed row is kept.
  releaseDraft(ok({
    assignmentId: 'a-1', draftRevision: 7,
    observations: [
      { stepId: 's1', result: 'PASSED', notes: 'server draft note', source: 'HUMAN_MANUAL', observedAt: '2026-11-20T10:00:00Z' },
    ],
    updatedAt: null,
  }))
  await waitFor(() => expect(notes).toHaveValue('typed while the draft was in flight'))

  await user.click(screen.getByRole('button', { name: 'Save draft' }))
  await waitFor(() => expect(puts).toHaveLength(1))
  expect(puts[0]!.baseRevision).toBe(7)
  expect(notes).toHaveValue('typed while the draft was in flight')
})

it('seal persists the current rows before it submits — the seal binds what is on screen', async () => {
  const user = userEvent.setup()
  const calls: string[] = []
  const puts: { baseRevision: number; observations: { stepId: string; result: string }[] }[] = []
  mount(`/w/${WS}/tester-desk/tester/assignments/a-1`, captureStubs({
    [`${TD}/tester/assignments/a-1/draft`]: (init) => {
      if (init?.method === 'PUT') {
        calls.push('draft:put')
        puts.push(JSON.parse(String(init.body)) as (typeof puts)[number])
        return ok({ assignmentId: 'a-1', draftRevision: 2, observations: [], updatedAt: null })
      }
      calls.push('draft:get')
      return ok({ assignmentId: 'a-1', draftRevision: 1, observations: [], updatedAt: null })
    },
    [`${TD}/tester/assignments/a-1/submit`]: () => {
      calls.push('submit')
      return ok(submission)
    },
  }))

  const fieldset = await screen.findByRole('group', { name: 'Result for Open the checkout' })
  await user.click(within(fieldset).getByRole('radio', { name: 'PASSED' }))
  await user.click(screen.getByRole('button', { name: 'Seal and submit for review' }))

  await waitFor(() => expect(calls).toEqual(['draft:get', 'draft:put', 'submit']))
  expect(puts[0]!.baseRevision).toBe(1)
  expect(puts[0]!.observations).toContainEqual(
    expect.objectContaining({ stepId: 's1', result: 'PASSED' }),
  )
})

it('a refused draft save seals nothing', async () => {
  const user = userEvent.setup()
  const calls: string[] = []
  mount(`/w/${WS}/tester-desk/tester/assignments/a-1`, captureStubs({
    [`${TD}/tester/assignments/a-1/draft`]: (init) => {
      if (init?.method === 'PUT') {
        calls.push('draft:put')
        return new Response(JSON.stringify({ status: 409, code: 'CONFLICT', title: 'Conflict', detail: 'the draft moved on' }), { status: 409, headers: { 'content-type': 'application/problem+json' } })
      }
      return ok({ assignmentId: 'a-1', draftRevision: 1, observations: [], updatedAt: null })
    },
    [`${TD}/tester/assignments/a-1/submit`]: () => {
      calls.push('submit')
      return ok(submission)
    },
  }))

  const fieldset = await screen.findByRole('group', { name: 'Result for Open the checkout' })
  await user.click(within(fieldset).getByRole('radio', { name: 'PASSED' }))
  await user.click(screen.getByRole('button', { name: 'Seal and submit for review' }))

  await screen.findByText(/the draft moved on/)
  expect(calls).toEqual(['draft:put'])
})
