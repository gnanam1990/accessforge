import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type { DiagnosisScope } from '../api/diagnosisRequests'
import type { RunEvaluation } from '../api/evaluation'
import type { Run } from '../api/resources'
import { SessionProvider } from '../session/SessionProvider'
import { DiagnosisRequestSection } from './DiagnosisRequestSection'
import { PATHS } from '../../../../packages/clients/ts/src/operations'

const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const run: Run = { runId: id(1), status: 'COMPLETED', outcome: 'INCONCLUSIVE', revision: 2,
  leaseEpoch: 1, manifestDigest: 'a'.repeat(64), cancellationRequestedAt: null,
  stopAcknowledgedAt: null, ambiguityReason: null, quarantined: false, retryOf: null }
// Synthetic contracts only; these cases do not invoke a provider or reader.
const evaluation: RunEvaluation = { evaluationId: id(5), snapshotDigest: 'b'.repeat(64),
  recordedAt: '2026-09-13T02:00:00Z', meaning: 'ORIGINAL_EVALUATION_SNAPSHOT',
  snapshot: { schemaVersion: 1, runId: run.runId, attemptId: id(6), manifestDigest: run.manifestDigest,
    evidenceSetDigest: 'c'.repeat(64), evaluatorVersion: '1.0.0', outcome: 'INCONCLUSIVE',
    reasons: ['Identity unavailable'], scope: 'Original recorded execution', sealedIdentities: {}, observedIdentities: {},
    assertions: [{ assertionId: 'task-complete', kind: 'TASK_COMPLETION', condition: 'UNKNOWN',
      provenance: 'ABSENT', evidenceRefs: [], unknownReason: 'Not observed' }], artifacts: [] } }
const scope: DiagnosisScope = { manifestDigest: run.manifestDigest, evaluationDigest: evaluation.snapshotDigest,
  modelProfileDigest: 'd'.repeat(64), assertionId: 'task-complete', componentPath: 'src/form.ts',
  componentName: 'Form', excerpts: [{ path: 'src/form.ts', lineStart: 1, lineEnd: 20 }],
  supersedes: null, billableCallAcknowledged: true }
const Location = () => <output aria-label="Current address">{useLocation().search}</output>

function fixture(options: { recovered?: boolean; unknown?: boolean; foreign?: boolean; loseResponse?: boolean; role?: string } = {}) {
  let stored = options.recovered === true
  const writes: { path: string; body: unknown; key: string | null }[] = []
  const reads: string[] = []
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
  const record = () => ({ requestId: id(7), runId: run.runId, requestedBy: options.foreign ? id(9) : id(3),
    scope, scopeDigest: 'e'.repeat(64), createdAt: '2026-09-13T02:00:00Z', expiresAt: '2026-09-13T03:00:00Z',
    revokedAt: options.unknown ? '2026-09-13T02:01:00Z' : null,
    meaning: 'HUMAN_REQUEST_NOT_MODEL_COMPLETION', deliveryMode: 'EXPLICIT_OPERATOR_DISPATCH',
    invocationState: options.unknown ? 'UNCONFIRMED' : 'NOT_STARTED', findingId: null })
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    expect(PATHS).toContain(path.split('?')[0]?.replace(id(4), '{workspace_id}').replace(id(1), '{run_id}'))
    if (path === '/v1/session') return json({ userId: id(3), email: 'operator@example.test',
      workspaces: [{ workspaceId: id(4), name: 'Fixture', role: options.role ?? 'OWNER' }] })
    if (init?.method === 'POST') {
      writes.push({ path, body: JSON.parse(String(init.body)), key: new Headers(init.headers).get('idempotency-key') })
      stored = true
      if (options.loseResponse) throw new TypeError('Connection lost after acceptance')
      return json(record(), 202)
    }
    reads.push(path)
    if (path.endsWith('/diagnosis-profile')) return json({ modelProfileDigest: scope.modelProfileDigest,
      meaning: 'Source and retained evidence disclosure; provider charges may apply.',
      profile: { sdk_version: '1.0', model_id: 'fixture-provider', region_name: 'fixture-region', provider_max_tokens: 100,
        invocation_output_tokens: 200, invocation_total_tokens: 300, max_context_characters: 1000, call_timeout_seconds: 30 } })
    if (path.endsWith('/evaluation')) return json(evaluation)
    return stored ? json(record()) : json({ code: 'RESOURCE_NOT_FOUND', status: 404, title: 'Not found', detail: 'No original operation', requestId: 'fixture' }, 404)
  }) as typeof fetch
  const mount = (search = options.recovered ? '?diagnosisOperation=original-key' : '') => {
    const client = new ApiClient({ fetchImpl, cookieSource: () => 'accessforge_csrf=fixture-csrf' })
    return render(<MemoryRouter initialEntries={['/' + search]}><SessionProvider client={client}>
      <DiagnosisRequestSection workspaceId={id(4)} run={run} /><Location />
    </SessionProvider></MemoryRouter>)
  }
  return { ...mount(), mount, writes, reads }
}

it('reviews an exact scope without POST and requires fresh disclosure acknowledgement', async () => {
  const f = fixture(), user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Scope a diagnosis request' }))
  await user.selectOptions(await screen.findByLabelText(/Original assertion/), 'task-complete')
  await user.type(screen.getByLabelText(/Component name/), 'Form')
  await user.type(screen.getByLabelText(/Excerpt 1 relative file path/), 'src/form.ts')
  await user.click(screen.getByRole('button', { name: 'Review diagnosis request' }))
  expect(screen.getByRole('heading', { name: 'Review diagnosis disclosure and scope' })).toHaveFocus()
  const ack = screen.getByRole('checkbox'), store = screen.getByRole('button', { name: 'Record diagnosis request' })
  expect(ack).not.toBeChecked(); expect(store).toBeDisabled(); expect(f.writes).toHaveLength(0)
  await user.click(ack); await user.click(store)
  await screen.findByRole('heading', { name: 'Stored diagnosis request' })
  expect(f.writes).toHaveLength(1); expect(f.writes[0]?.body).toEqual(scope)
  expect(f.writes[0]?.key).toBeTruthy()
  expect(screen.getByLabelText('Current address').textContent).toContain(f.writes[0]?.key)
})

it('recovers a lost POST response by the original URL key after remount, without another write', async () => {
  const f = fixture({ loseResponse: true }), user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Scope a diagnosis request' }))
  await user.selectOptions(await screen.findByLabelText(/Original assertion/), 'task-complete')
  await user.type(screen.getByLabelText(/Component name/), 'Form')
  await user.type(screen.getByLabelText(/Excerpt 1 relative file path/), 'src/form.ts')
  await user.click(screen.getByRole('button', { name: 'Review diagnosis request' }))
  await user.click(screen.getByRole('checkbox')); await user.click(screen.getByRole('button', { name: 'Record diagnosis request' }))
  await screen.findByRole('heading', { name: 'Stored diagnosis request' })
  const search = screen.getByLabelText('Current address').textContent ?? ''
  f.unmount(); f.mount(search)
  await screen.findByRole('heading', { name: 'Stored diagnosis request' })
  expect(f.writes).toHaveLength(1)
  expect(f.reads.some((path) => path.endsWith(`/operation?operationKey=${f.writes[0]?.key}`))).toBe(true)
})

it('never offers a replacement for revoked but unconfirmed provider work', async () => {
  const f = fixture({ recovered: true, unknown: true })
  await screen.findByText('UNCONFIRMED')
  expect(screen.queryByRole('button', { name: 'Review a new, separate diagnosis request' })).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})

it('rejects a recovery response belonging to another requester', async () => {
  const f = fixture({ recovered: true, foreign: true })
  await screen.findByText(/response does not match the requested identity/)
  expect(screen.queryByRole('heading', { name: 'Stored diagnosis request' })).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})
