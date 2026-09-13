import { useEffect, useRef, useState, type JSX } from 'react'
import { Link, useParams } from 'react-router-dom'
import { RouteHeading } from '../a11y/RouteHeading'
import { decidePatch, getPatch, listVerifications, type Patch } from '../api/patches'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { Dialog } from '../components/Dialog'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { PatchComparisonSection } from './PatchComparisonSection'

const reviewable = (patch: Patch): boolean => patch.changes.every((c) => !c.binary && [null, '100644', '100755'].includes(c.mode))
const escapedSource = (content: string): string => JSON.stringify(content).replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,
  (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`)

export const PatchScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId(), { patchId } = useParams()
  if (patchId === undefined) throw new Error('patch route requires its exact identity')
  return <PatchWorkspace key={`${workspaceId}:${patchId}`} workspaceId={workspaceId} patchId={patchId} />
}
const PatchWorkspace = ({ workspaceId, patchId }: { readonly workspaceId: string; readonly patchId: string }): JSX.Element => {
  const { client, state } = useSession()
  const resource = useResource((signal) => getPatch(client, workspaceId, patchId, signal), [client, workspaceId, patchId])
  const role = state.status === 'authenticated' ? state.workspaces.find((w) => w.workspaceId === workspaceId)?.role : undefined
  const [preview, setPreview] = useState<{ patch: Patch; kind: 'approval' | 'rejection' } | null>(null)
  const [seconds, setSeconds] = useState('3600'), [reason, setReason] = useState(''), [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false), [locked, setLocked] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const active = useRef<AbortController | null>(null)
  useEffect(() => () => active.current?.abort(), [])
  useEffect(() => { if (resource.state.kind === 'ready') setLocked(false) }, [resource.state])
  const open = (patch: Patch, kind: 'approval' | 'rejection') => {
    if (busy || locked) return
    setPreview({ patch, kind }); setSeconds('3600'); setReason(''); setAck(false)
  }
  const validSeconds = /^[1-9]\d*$/.test(seconds) && Number(seconds) <= 86400
  const act = async () => {
    if (preview === null || active.current !== null || !ack || resource.state.kind !== 'ready' || locked) return
    const { patch, kind } = preview, current = resource.state.value
    if (current.revision !== patch.revision || current.patchDigest !== patch.patchDigest ||
        (kind === 'approval' ? !['OWNER', 'MAINTAINER'].includes(role ?? '') || !validSeconds || !reviewable(patch) : !['OWNER', 'REVIEWER'].includes(role ?? '') || !reason.trim())) return
    const controller = new AbortController(); active.current = controller; setBusy(true); setMessage(null)
    const result = await decidePatch(client, workspaceId, patch,
      kind === 'approval' ? { kind, seconds: Number(seconds) } : { kind, reason: reason.trim() }, controller.signal)
    if (controller.signal.aborted || active.current !== controller) return
    active.current = null; setBusy(false); setPreview(null); setAck(false); setLocked(true)
    setMessage(result.kind === 'ok'
      ? kind === 'approval' ? 'PATCH_APPLY approval recorded. No build, verification, merge or deployment is claimed.' : 'Patch rejection recorded. The finding and original run outcome are unchanged.'
      : 'This decision was not confirmed. Readback is being reconciled; do not assume approval, rejection or rollback.')
    resource.reload()
  }
  return <>
    <RouteHeading>Proposed repair</RouteHeading>
    {message && <Notice tone="information" heading="Repair decision result" headingLevel={2} live><p>{message}</p></Notice>}
    <ResourceView resource={resource} what="this proposed repair">{(patch) => <>
      <p><StatusBadge tone="neutral" kind="Patch status">{patch.status}</StatusBadge></p><p>{patch.meaning}</p>
      <Link className="af-link" to={`/w/${encodeURIComponent(workspaceId)}/findings/${encodeURIComponent(patch.findingId)}`}>Read the original finding and diagnosis</Link>
      <section className="af-stack"><h2>Exact proposal and base</h2>
        <dl><dt>Patch</dt><dd><code>{patch.patchId}</code></dd><dt>Revision reviewed</dt><dd>{patch.revision}</dd>
          <dt>Patch digest</dt><dd><code>{patch.patchDigest}</code></dd><dt>Base manifest digest</dt><dd><code>{patch.baseManifestDigest}</code></dd>
          <dt>Base source tree digest</dt><dd><code>{patch.baseSourceDigest}</code></dd><dt>Proposed by</dt><dd>{patch.proposedBy}</dd>
          <dt>Author rationale, not verification</dt><dd style={{ whiteSpace: 'pre-wrap' }}>{patch.rationale}</dd></dl>
        {patch.separatelyReviewedPaths.length > 0 && <Notice tone="warning" heading="Separate dependency or build scope" headingLevel={3}>
          <p>These paths change dependencies or build configuration, not just application accessibility:</p>
          <ul>{patch.separatelyReviewedPaths.map((path) => <li key={path}><code>{JSON.stringify(path)}</code></li>)}</ul>
        </Notice>}
      </section>
      <PatchComparisonSection key={`comparison:${patch.patchDigest}:${patch.revision}`} workspaceId={workspaceId} patch={patch} />
      <PatchFiles key={`${patch.patchDigest}:${patch.revision}`} patch={patch} />
      <section className="af-stack"><h2>Isolated candidate approval</h2>
        <p>PATCH_APPLY authorizes only application in an isolated candidate workspace. It does not merge, deploy, publish or assert that this repair works. The worker rechecks current authority, source identity, expiry and revocation.</p>
        {patch.approval ? <dl><dt>Approval ID</dt><dd>{patch.approval.approvalId}</dd><dt>Scope</dt><dd>{patch.approval.scope}</dd>
          <dt>Actor</dt><dd>{patch.approval.actorId}</dd><dt>Bound revision</dt><dd>{patch.approval.expectedRevision}</dd>
          <dt>Expires (UTC)</dt><dd><time dateTime={patch.approval.expiresAt}>{patch.approval.expiresAt}</time></dd>
          <dt>Revoked</dt><dd>{patch.approval.revokedAt ?? 'Not recorded in this read'}</dd></dl>
          : <p>{patch.approvalId === null ? 'No approval is attached.' : 'Approval details are unavailable; attached status alone is not current execution authority.'}</p>}
        {!reviewable(patch) && <p role="alert">Binary, symlink or unsupported file-mode content cannot be approved from this page.</p>}
        {patch.status === 'PROPOSED' && reviewable(patch) && ['OWNER', 'MAINTAINER'].includes(role ?? '') &&
          <Button disabled={busy || locked} onClick={() => open(patch, 'approval')}>Review isolated candidate approval</Button>}
        {['PROPOSED', 'APPROVED'].includes(patch.status) && ['OWNER', 'REVIEWER'].includes(role ?? '') &&
          <Button disabled={busy || locked} onClick={() => open(patch, 'rejection')}>Review patch rejection</Button>}
      </section>
      <VerificationHistory key={`${patch.patchId}:${patch.revision}`} workspaceId={workspaceId} patchId={patch.patchId} />
    </>}</ResourceView>
    <Button disabled={busy} onClick={() => { setPreview(null); resource.reload() }}>Read this repair again</Button>
    <Dialog open={preview !== null} heading={preview?.kind === 'rejection' ? 'Reject this exact patch' : 'Approve isolated candidate application'}
      onClose={() => { if (!busy) setPreview(null) }} actions={<>
        <Button disabled={busy} onClick={() => setPreview(null)}>Back without a decision</Button>
        <Button busy={busy} disabled={!ack || (preview?.kind === 'approval' ? !validSeconds : !reason.trim())} onClick={() => void act()}>
          {preview?.kind === 'rejection' ? 'Reject this patch' : 'Approve isolated candidate only'}
        </Button>
      </>}>
      {preview && <>
        <p>Patch <code>{preview.patch.patchId}</code>, revision {preview.patch.revision}, digest <code>{preview.patch.patchDigest}</code>.</p>
        <p>Base source tree <code>{preview.patch.baseSourceDigest}</code>. Changed bytes or revision require a new review.</p>
        {preview.kind === 'approval' ? <>
          <p>This approves the displayed full proposed file content. Review the original-source comparison above when available; otherwise inspect the exact base separately before deciding. A comparison is not functional repair proof.</p>
          <label>Approval duration (seconds, 1–86400)<input value={seconds} disabled={busy} inputMode="numeric" aria-invalid={!validSeconds || undefined} onChange={(e) => { setSeconds(e.target.value); setAck(false) }} /></label>
          {!validSeconds && <p role="alert">Use a whole number from 1 to 86400 seconds.</p>}
          <p>PATCH_APPLY only. Does not merge or deploy. Dependency/build paths, if listed above, require separate review.</p>
        </> : <label>Reason for rejection<textarea value={reason} disabled={busy} onChange={(e) => { setReason(e.target.value); setAck(false) }} /></label>}
        <label><input type="checkbox" checked={ack} disabled={busy} onChange={(e) => setAck(e.target.checked)} />{' '}
          {preview.kind === 'approval' ? 'I reviewed the exact proposed bytes against their base, including any separate dependency/build scope, and authorize isolated candidate application only.' : 'I confirm rejection of this exact revision with the reason above.'}</label>
        {busy && <p role="status">Decision pending. Leaving this page does not prove the server rolled it back.</p>}
      </>}
    </Dialog>
  </>
}
const PatchFiles = ({ patch }: { readonly patch: Patch }): JSX.Element => {
  const [index, setIndex] = useState(0)
  const change = patch.changes[index]!
  return <section className="af-stack"><h2>Proposed file content</h2>
    <p>This section is the exact full replacement text or deletion request, not a unified diff. The separate original-source comparison above is available only when retained from the authorized base.</p>
    <label>Changed file<select value={index} onChange={(e) => setIndex(Number(e.target.value))}>
      {patch.changes.map((c, i) => <option key={c.path} value={i}>{JSON.stringify(c.path)} — {c.operation}</option>)}
    </select></label>
    <p>File mode: <code>{change.mode ?? 'Preserve existing mode'}</code>. Binary flag: {change.binary ? 'Yes — inspect before any decision' : 'No'}.</p>
    {change.content === null ? <p>DELETE the whole file. Consult the original-source comparison above, if available, for its original content.</p> : <>
      <textarea aria-label={`Exact proposed text for ${JSON.stringify(change.path)}`} readOnly rows={14} value={change.content} spellCheck={false} />
      <p>The text control may normalize line endings for display. The escaped representation below preserves them and makes directional control characters explicit.</p>
      <details><summary>Escaped source text and line endings</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{escapedSource(change.content)}</pre></details>
    </>}
  </section>
}
const VerificationHistory = ({ workspaceId, patchId }: { readonly workspaceId: string; readonly patchId: string }): JSX.Element => {
  const { client } = useSession()
  const resource = useResource((signal) => listVerifications(client, workspaceId, patchId, signal), [client, workspaceId, patchId])
  const runLink = (run: string) => `/w/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(run)}`
  return <section className="af-stack"><h2>Recorded verification attempts</h2>
    <p>All recorded attempts are shown. Approval is not verification; a passing accessibility assertion does not override a failing protected functional check. Human assessment cannot promote INCONCLUSIVE to VERIFIED.</p>
    <ResourceView resource={resource} what="recorded repair verifications">{(items) => items.length === 0 ? <p>No verification attempt has been recorded.</p> :
      <ol className="af-stack">{items.map((v) => <li key={v.verificationId} className="af-panel af-stack">
        <h3>Verification <code>{v.verificationId}</code></h3><p>State: {v.state}. Conclusion: {v.conclusion ?? 'No conclusion recorded'}.</p>
        <p>{v.meaning}</p><ul>{v.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul>
        <Link className="af-link" to={runLink(v.baselineRunId)}>Read this attempt's baseline run</Link>
        {v.candidateRunId ? <Link className="af-link" to={runLink(v.candidateRunId)}>Read this attempt's candidate run</Link> : <p>No candidate run is attached.</p>}
      </li>)}</ol>}
    </ResourceView>
  </section>
}
