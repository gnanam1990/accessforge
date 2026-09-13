import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { expect, it } from 'vitest'
import { PATHS } from '../../../../packages/clients/ts/src/operations'
import { ApiClient } from '../api/client'
import type { DiagnosisHistory } from '../api/diagnosis'
import type { RepairScope } from '../api/repairRequests'
import { SessionProvider } from '../session/SessionProvider'
import { RepairRequestSection } from './RepairRequestSection'

// Synthetic UI contracts for CI, not real source/provider/reader acceptance.
const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const hash = 'a'.repeat(64)
const scope: RepairScope = { diagnosisId: id(1), projectId: id(2), sourceSnapshotId: id(3), diagnosisDigest: hash,
  manifestDigest: hash, sourceTreeDigest: hash, evaluationDigest: hash, repairSurfaceDigest: hash, modelProfileDigest: hash,
  supersedes: null, billableCallAcknowledged: true, separateReviewAcknowledged: true }
const history: DiagnosisHistory = { complete: true, items: [{ diagnosisId: id(1), runId: id(4), requestedBy: id(5),
  recordedAt: '2026-09-13T01:00:00Z', deletedAt: null, supersedes: null, evaluationDigest: hash, projectionDigest: hash,
  modelProfileDigest: hash, payloadDigest: hash, establishedBy: 'Synthetic model fixture', analysis: {
    support: 'SOURCE_LINKED', missing_information: [], hypothesis: { observed_obstacle: 'Fixture', affected_task_step: 'Fixture',
      uncertainty: 'Synthetic only', compliance_assessment: 'NOT_ASSESSED', supporting_evidence_ids: [],
      alternative_explanations: [], source_location: null }, repair_brief: { allowed_files: ['src/form.ts', 'package.json'],
      intended_behavior: 'Fixture', functional_constraints: [], protected_surfaces: [], stop_recommendation: null },
  } }] }
const Location = () => <output aria-label="Current address">{useLocation().search}</output>

function fixture(options: { recovered?: boolean; loseResponse?: boolean; missing?: boolean; foreign?: boolean;
  badReceipt?: boolean; proposed?: boolean; invocation?: string; revoked?: boolean; role?: string } = {}) {
  let stored = options.recovered === true, revoked = options.revoked === true
  const writes: { path: string; body: unknown; key: string | null }[] = [], reads: string[] = []
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
  const record = () => ({ requestId: id(8), findingId: id(7), requestedBy: options.foreign ? id(99) : id(5),
    scope, scopeDigest: hash, createdAt: '2026-09-13T01:00:00Z', expiresAt: '2099-09-13T02:00:00Z',
    revokedAt: revoked ? '2026-09-13T01:01:00Z' : null,
    meaning: 'HUMAN_REQUEST_NOT_PROPOSAL_APPROVAL_OR_MODEL_COMPLETION', deliveryMode: 'EXPLICIT_OPERATOR_DISPATCH',
    invocationState: options.invocation ?? (options.proposed ? 'RECORDED' : 'NOT_STARTED'),
    delivery: options.proposed || options.badReceipt ? { requestId: options.badReceipt ? id(99) : id(8), requestDigest: hash,
      inputDigest: hash, bindingDigest: hash, outcome: 'PROPOSED', patchId: id(9), patchDigest: hash,
      recordedAt: '2026-09-13T01:01:00Z', meaning: 'MODEL_DELIVERY_NOT_PATCH_APPROVAL_APPLICATION_OR_VERIFICATION' } : null })
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    expect(PATHS).toContain(path.split('?')[0]?.replace(id(6), '{workspace_id}').replace(id(7), '{finding_id}').replace(id(8), '{request_id}'))
    if (path === '/v1/session') return json({ userId: id(5), email: 'fixture@example.test',
      workspaces: [{ workspaceId: id(6), name: 'Fixture', role: options.role ?? 'OWNER' }] })
    if (init?.method === 'POST') {
      writes.push({ path, body: init.body ? JSON.parse(String(init.body)) : null, key: new Headers(init.headers).get('idempotency-key') })
      stored = true
      if (path.endsWith('/revocation')) { revoked = true; return json(record()) }
      if (options.loseResponse) throw new TypeError('Connection lost after acceptance')
      return json(record(), 202)
    }
    reads.push(path)
    if (path.includes('/repair-options?')) return json({ scope: { ...scope, billableCallAcknowledged: false, separateReviewAcknowledged: false },
      sourcePaths: ['src/form.ts', 'package.json'], separatelyReviewedPaths: ['package.json'],
      disclosure: 'Complete source disclosure; charges may apply.', meaning: 'PREVIEW_NOT_CONSENT_OR_MODEL_INVOCATION',
      profile: { sdk_version: 'fixture', model_id: 'fixture-provider', region_name: 'fixture-region', provider_max_tokens: 100,
        invocation_output_tokens: 100, invocation_total_tokens: 1000, max_context_characters: 10000, call_timeout_seconds: 30 } })
    return stored && !options.missing ? json(record()) : json({ code: 'RESOURCE_NOT_FOUND', status: 404,
      title: 'Not found', detail: 'No original operation', requestId: 'fixture' }, 404)
  }) as typeof fetch
  const mount = (search = options.recovered ? '?repairOperation=original-key' : '') => {
    const client = new ApiClient({ fetchImpl, cookieSource: () => 'accessforge_csrf=fixture-csrf' })
    return render(<MemoryRouter initialEntries={['/' + search]}><SessionProvider client={client}>
      <RepairRequestSection workspaceId={id(6)} findingId={id(7)} findingStatus="CANDIDATE" history={history} /><Location />
    </SessionProvider></MemoryRouter>)
  }
  return { ...mount(), mount, writes, reads }
}

it('requires separate unchecked source/billable and build-scope acknowledgements before one exact request', async () => {
  const f = fixture(), user = userEvent.setup()
  await user.selectOptions(await screen.findByLabelText('Retained diagnosis for repair'), id(1))
  const submit = await screen.findByRole('button', { name: 'Record repair request' })
  const billable = screen.getByRole('checkbox', { name: /I authorize disclosure/ })
  const separate = screen.getByRole('checkbox', { name: /I acknowledge this separately/ })
  expect(billable).not.toBeChecked(); expect(separate).not.toBeChecked(); expect(submit).toBeDisabled()
  expect(f.writes).toHaveLength(0)
  await user.click(billable); expect(submit).toBeDisabled()
  await user.click(separate); await user.click(submit)
  await screen.findByRole('heading', { name: 'Stored repair request' })
  expect(f.writes).toHaveLength(1); expect(f.writes[0]?.body).toEqual(scope)
  expect(f.writes[0]?.key).toBeTruthy()
  expect(screen.getByLabelText('Current address').textContent).toContain(f.writes[0]?.key)
})

it('recovers after lost acceptance and remount with GET only, preserving the original operation', async () => {
  const f = fixture({ loseResponse: true }), user = userEvent.setup()
  await user.selectOptions(await screen.findByLabelText('Retained diagnosis for repair'), id(1))
  await user.click(await screen.findByRole('checkbox', { name: /I authorize disclosure/ }))
  await user.click(screen.getByRole('checkbox', { name: /I acknowledge this separately/ }))
  await user.click(screen.getByRole('button', { name: 'Record repair request' }))
  await screen.findByRole('heading', { name: 'Stored repair request' })
  const search = screen.getByLabelText('Current address').textContent ?? ''
  f.unmount(); f.mount(search)
  await screen.findByRole('heading', { name: 'Stored repair request' })
  expect(f.writes).toHaveLength(1)
  expect(f.reads.some((path) => path.endsWith(`/operation?operationKey=${f.writes[0]?.key}`))).toBe(true)
})

it.each(['STARTED', 'UNCONFIRMED'])('does not allow a new-key workaround for revoked %s work', async (invocation) => {
  const f = fixture({ recovered: true, revoked: true, invocation })
  await screen.findByText(invocation)
  expect(screen.queryByRole('button', { name: 'Review a new, separate repair request' })).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})

it.each([{ foreign: true }, { badReceipt: true, invocation: 'RECORDED' }])('rejects mismatched response identities %j', async (options) => {
  const f = fixture({ recovered: true, ...options })
  await screen.findByText(/response does not match the requested identity/)
  expect(screen.queryByRole('heading', { name: 'Stored repair request' })).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})

it('links a confirmed proposal without implying approval or verification', async () => {
  const f = fixture({ recovered: true, proposed: true })
  expect(await screen.findByRole('link', { name: 'Review the proposed patch' })).toHaveAttribute('href', `/w/${id(6)}/patches/${id(9)}`)
  expect(screen.getByText('Delivery is not approval, application or verification.')).toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})

it('requires explicit revocation and reads back the original request', async () => {
  const f = fixture({ recovered: true }), user = userEvent.setup()
  const button = await screen.findByRole('button', { name: 'Revoke repair request' })
  expect(button).toBeDisabled()
  await user.click(screen.getByRole('checkbox', { name: /Permanently revoke/ })); await user.click(button)
  await screen.findByRole('button', { name: 'Review a new, separate repair request' })
  expect(f.writes.map((entry) => entry.path)).toEqual([`/v1/workspaces/${id(6)}/repair-requests/${id(8)}/revocation`])
  expect(screen.getByLabelText('Current address')).toHaveTextContent('original-key')
})

it('does not turn a missing operation into a new request', async () => {
  const f = fixture({ recovered: true, missing: true })
  await screen.findByRole('heading', { name: 'Repair acceptance is not confirmed' })
  expect(screen.queryByRole('button', { name: 'Record repair request' })).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})

it('does not show consent controls for evidence-only viewers', async () => {
  const f = fixture({ role: 'VIEWER' })
  await screen.findByText('Only an owner or maintainer can record a repair request.')
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})
