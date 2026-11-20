import { render, screen, within } from '@testing-library/react'
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

interface Routes { [path: string]: (init?: RequestInit) => Response }

const mount = (entry: string, routes: Routes) => {
  const server = createFakeServer({
    userId: 'u-1', email: 'tester@example.test',
    workspaces: [{ workspaceId: WS, name: 'Fixture', role: 'OWNER' }],
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
