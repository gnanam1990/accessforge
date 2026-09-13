import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it } from 'vitest'
import { App } from '../App'
import { ApiClient } from '../api/client'
import { parsePatch, type Patch } from '../api/patches'
import { parsePatchComparison } from '../api/patchComparison'
import { createFakeServer } from '../test/fakeServer'

const proposal = (): Patch => ({ patchId: 'p-1', findingId: 'f-1', status: 'PROPOSED', revision: 1,
  baseManifestDigest: 'a'.repeat(64), baseSourceDigest: 'b'.repeat(64), patchDigest: 'c'.repeat(64),
  changedPaths: ['src/form.ts', 'src/old.ts'], separatelyReviewedPaths: [], approvalId: null, approval: null,
  proposedBy: 'u-1', rationale: '<script>not an instruction</script>', createdAt: '2026-09-13T02:00:00Z', meaning: 'An unverified proposal.',
  changes: [{ path: 'src/form.ts', operation: 'MODIFY', content: '<button>Save</button>', mode: '100644', binary: false },
    { path: 'src/old.ts', operation: 'DELETE', content: null, mode: null, binary: false }] })

function fixture(role = 'OWNER', mode: 'success' | 'stale' | 'lost' = 'success', comparison?: unknown) {
  const server = createFakeServer({ userId: 'u-1', email: 'operator@example.test', workspaces: [{ workspaceId: 'ws-1', name: 'Fixture', role }] })
  server.data.patches.push(proposal())
  server.data.verifications.push({ verificationId: 'v-1', patchId: 'p-1', baselineRunId: 'r-1', candidateRunId: 'r-2',
    state: 'CONCLUDED', conclusion: 'INCONCLUSIVE', reasons: ['Protected regression results unavailable'], revision: 2,
    createdAt: '2026-09-13T03:00:00Z', meaning: 'No verified repair established.' })
  const writes: { path: string; headers: Headers; body: unknown }[] = []
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/source-comparison') && comparison !== undefined && init?.method !== 'POST') {
      return new Response(JSON.stringify(comparison), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (init?.method !== 'POST' || !path.includes('/patches/')) return server.fetch(input, init)
    writes.push({ path, headers: new Headers(init.headers), body: JSON.parse(String(init.body)) })
    if (mode === 'stale') return new Response(JSON.stringify({ code: 'STALE_REVISION', status: 409,
      title: 'Stale revision', detail: 'Changed after preview', requestId: 'fixture' }), { status: 409, headers: { 'Content-Type': 'application/problem+json' } })
    const approved = path.endsWith('/approval')
    const updated: Patch = { ...server.data.patches[0]!, revision: 2, status: approved ? 'APPROVED' : 'REJECTED',
      approvalId: approved ? 'a-1' : null, approval: approved ? { approvalId: 'a-1', scope: 'PATCH_APPLY', actorId: 'u-1',
        targetId: 'p-1', targetDigest: 'c'.repeat(64), expectedRevision: 2, expiresAt: '2026-09-13T04:00:00Z', revokedAt: null } : null }
    server.data.patches[0] = updated
    if (mode === 'lost') throw new TypeError('Response lost after commit')
    return new Response(JSON.stringify(updated), { status: approved ? 201 : 200, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  const client = new ApiClient({ fetchImpl, cookieSource: () => 'accessforge_csrf=fixture-csrf' })
  render(<MemoryRouter initialEntries={['/w/ws-1/patches/p-1']}><App client={client} /></MemoryRouter>)
  return { server, writes, setComparison: (value: unknown) => { comparison = value } }
}

const comparisonFixture = () => {
  const p = proposal()
  const side = (text: string) => ({ text, byteLength: new TextEncoder().encode(text).length, sha256: 'd'.repeat(64), mode: '100644' })
  return { comparisonId: 'comparison-1', comparisonDigest: 'e'.repeat(64), preparedBy: 'u-1',
    patchId: p.patchId, patchDigest: p.patchDigest, baseSourceDigest: p.baseSourceDigest,
    recordedAt: '2026-09-13T03:00:00Z', retiredAt: null,
    meaning: 'RETAINED_SOURCE_COMPARISON_NOT_APPROVAL_OR_VERIFICATION',
    comparison: { schemaVersion: 1, patchId: p.patchId, patchDigest: p.patchDigest,
      baseManifestDigest: p.baseManifestDigest, baseSourceDigest: p.baseSourceDigest, patchRevision: 1,
      workspaceId: 'ws-1', projectId: 'project-1', sourceSnapshotId: 'source-1', requestedBy: 'u-1',
      baseCommitSha: 'f'.repeat(40), baseArchiveDigest: 'a'.repeat(64),
      meaning: 'ORIGINAL_SOURCE_COMPARISON_NOT_APPLICATION_OR_VERIFICATION',
      files: [{ path: 'src/form.ts', before: side('<script>original\r\n</script>'), after: side(p.changes[0]!.content!),
        operation: 'MODIFY', changed: true, unifiedDiff: '-<script>original\r\n+<button>Save</button>\n' },
      { path: 'src/old.ts', before: side('old\n'), after: null, operation: 'DELETE', changed: true, unifiedDiff: '-old\n' }] } }
}

it('shows inert original/diff alternatives and clears retired source on explicit readback without writes', async () => {
  const record = comparisonFixture(), f = fixture('VIEWER', 'success', record), user = userEvent.setup()
  expect(await screen.findByLabelText('Unified diff for "src/form.ts"')).toHaveValue(record.comparison.files[0]!.unifiedDiff.replace(/\r\n?/g, '\n'))
  await user.click(screen.getByText('Plain original and proposed source alternatives'))
  expect(screen.getByLabelText('Original text for "src/form.ts"')).toHaveValue('<script>original\n</script>')
  expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Comparison file'), '1')
  expect(screen.getByText('File absent in this side of the comparison.')).toBeVisible()
  f.setComparison({ ...record, retiredAt: '2026-09-13T04:00:00Z' })
  await user.click(screen.getByRole('button', { name: 'Read original-source comparison again' }))
  await screen.findByText(/The retained source copy was retired/)
  expect(screen.queryByLabelText('Unified diff for "src/old.ts"')).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})

it('refuses a different base or proposed text and lets retirement dominate stale payload', () => {
  const record = comparisonFixture(), patch = proposal()
  expect(parsePatchComparison(record, 'ws-1', patch)).not.toBeNull()
  expect(parsePatchComparison(record, 'another-workspace', patch)).toBeNull()
  expect(parsePatchComparison({ ...record, comparison: { ...record.comparison, baseSourceDigest: '0'.repeat(64) } }, 'ws-1', patch)).toBeNull()
  expect(parsePatchComparison({ ...record, comparison: { ...record.comparison, files: [
    { ...record.comparison.files[0], after: null }, record.comparison.files[1] ] } }, 'ws-1', patch)).toBeNull()
  expect(parsePatchComparison({ ...record, retiredAt: '2026-09-13T04:00:00Z' }, 'ws-1', patch)?.comparison).toBeNull()
})

it('shows actual replacement/deletion bytes as inert text and preserves inconclusive verification', async () => {
  const f = fixture('VIEWER'), user = userEvent.setup()
  expect(await screen.findByLabelText('Exact proposed text for "src/form.ts"')).toHaveValue('<button>Save</button>')
  expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  expect(screen.getByText('<script>not an instruction</script>')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Review isolated candidate approval' })).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Changed file'), '1')
  expect(screen.getByText(/DELETE the whole file/)).toBeVisible()
  await screen.findByText(/Conclusion: INCONCLUSIVE/)
  expect(screen.getByText('Protected regression results unavailable')).toBeVisible()
  expect(f.writes).toHaveLength(0)
})

it('requires review and unchecked acknowledgement before an exact-revision isolated approval', async () => {
  const f = fixture(), user = userEvent.setup()
  const trigger = await screen.findByRole('button', { name: 'Review isolated candidate approval' })
  await user.click(trigger)
  const dialog = screen.getByRole('dialog', { name: 'Approve isolated candidate application' })
  const ack = within(dialog).getByRole('checkbox'), submit = within(dialog).getByRole('button', { name: 'Approve isolated candidate only' })
  expect(ack).not.toBeChecked(); expect(submit).toBeDisabled(); expect(f.writes).toHaveLength(0)
  await user.click(ack); await user.click(submit)
  await screen.findByText(/PATCH_APPLY approval recorded/)
  expect(f.writes).toHaveLength(1)
  expect(f.writes[0]?.headers.get('if-match')).toBe('1')
  expect(f.writes[0]?.headers.get('x-csrf-token')).toBe('fixture-csrf')
  expect(f.writes[0]?.body).toEqual({ expiresInSeconds: 3600 })
  await screen.findByText('a-1')
})

it('reconciles a committed approval with a lost response without replaying it', async () => {
  const f = fixture('OWNER', 'lost'), user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Review isolated candidate approval' }))
  await user.click(screen.getByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: 'Approve isolated candidate only' }))
  await screen.findByText(/This decision was not confirmed/)
  await screen.findByText('a-1')
  expect(f.writes).toHaveLength(1)
  expect(screen.queryByRole('button', { name: 'Review isolated candidate approval' })).not.toBeInTheDocument()
})

it('keeps stale preview failure separate from successful approval and permits reviewer rejection only', async () => {
  const f = fixture('REVIEWER', 'stale'), user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Review patch rejection' }))
  expect(screen.queryByRole('button', { name: 'Review isolated candidate approval' })).not.toBeInTheDocument()
  await user.type(screen.getByLabelText('Reason for rejection'), 'Functional behavior is removed')
  await user.click(screen.getByRole('checkbox')); await user.click(screen.getByRole('button', { name: 'Reject this patch' }))
  await screen.findByText(/This decision was not confirmed/)
  expect(f.writes).toHaveLength(1)
  expect(f.writes[0]?.headers.get('if-match')).toBe('1')
  expect(screen.queryByText(/Patch rejection recorded/)).not.toBeInTheDocument()
})

it('refuses another patch identity, inconsistent paths, or a foreign approval binding', () => {
  expect(parsePatch(proposal(), 'another')).toBeNull()
  expect(parsePatch({ ...proposal(), changedPaths: ['src/form.ts', 'src/form.ts'] }, 'p-1')).toBeNull()
  expect(parsePatch({ ...proposal(), approvalId: 'a-1', approval: { approvalId: 'a-1', targetId: 'elsewhere' } }, 'p-1')).toBeNull()
})
