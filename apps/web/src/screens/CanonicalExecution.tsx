import { useId, useState } from 'react'
import type { JSX } from 'react'
import { Link } from 'react-router-dom'
import { approveExecutionSeal, readExecutionApproval, readExecutionSeal, requestRun } from '../api/resources'
import type { ExecutionSeal, RunRequested, SealedManifest } from '../api/resources'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { membershipFor, useSession } from '../session/SessionProvider'
import { workspacePath } from '../routes/routeMap'

/** Canonical seals reserve run IDs before admission. A non-null run ID is not proof of use. */
export const CanonicalExecution = ({ workspaceId, projectId, manifest }: {
  readonly workspaceId: string
  readonly projectId: string
  readonly manifest: SealedManifest
}): JSX.Element => {
  const { client, state } = useSession()
  const role = membershipFor(state, workspaceId)?.role
  const mayExecute = role === 'OWNER' || role === 'MAINTAINER'
  const seal = useResource((signal) => readExecutionSeal(client, workspaceId, projectId,
    manifest.sealedManifestId, signal), [client, workspaceId, projectId, manifest.sealedManifestId])
  return <ResourceView resource={seal} what="the exact execution manifest">{(value) => {
    if (value.manifestKind !== 'CANONICAL_EXECUTION' || value.canonicalManifest === null ||
      value.manifestDigest !== manifest.manifestDigest || value.sealedManifestId !== manifest.sealedManifestId ||
      value.canonicalManifest['runId'] !== manifest.runId) {
      return <Notice tone="problem" heading="Execution scope could not be matched" headingLevel={3}>
        <p>Reload the journey before making an execution decision.</p></Notice>
    }
    return <ExecutionDecision key={`${value.sealedManifestId}:${value.revision}`}
      workspaceId={workspaceId} projectId={projectId} seal={value} mayExecute={mayExecute} />
  }}</ResourceView>
}

const ExecutionDecision = ({ workspaceId, projectId, seal, mayExecute }: {
  readonly workspaceId: string
  readonly projectId: string
  readonly seal: ExecutionSeal
  readonly mayExecute: boolean
}): JSX.Element => {
  const { client } = useSession()
  const [refresh, setRefresh] = useState(0)
  const approval = useResource((signal) => readExecutionApproval(client, workspaceId, projectId,
    seal.sealedManifestId, signal), [client, workspaceId, projectId, seal.sealedManifestId, refresh])
  const [expiresAt, setExpiresAt] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [pendingApproval, setPendingApproval] = useState<{ key: string; expiresAt: string } | null>(null)
  const [runKey, setRunKey] = useState<string | null>(null)
  const [requested, setRequested] = useState<RunRequested | null>(null)
  const reviewId = useId()
  const canonical = seal.canonicalManifest!
  const missing = approval.state.kind === 'problem' && approval.state.problem.code === 'RESOURCE_NOT_FOUND'
  const issued = approval.state.kind === 'ready' ? approval.state.value : null
  const current = issued !== null && issued.scope === 'RUN_EFFECTS' &&
    issued.approvalId === canonical['authorizationId'] &&
    issued.targetId === seal.sealedManifestId && issued.targetDigest === seal.manifestDigest &&
    issued.expectedRevision === seal.revision && issued.revokedAt === null &&
    Date.parse(issued.expiresAt) > Date.now()

  const approve = async (): Promise<void> => {
    if (busy || !reviewed) return
    const expiry = Date.parse(expiresAt)
    const sealExpiry = Date.parse(String(canonical['expiresAt']))
    if (pendingApproval === null && (!Number.isFinite(expiry) || expiry <= Date.now() ||
      !Number.isFinite(sealExpiry) || expiry > sealExpiry)) {
      setMessage('Choose a future approval expiry no later than the manifest expiry, including its timezone.')
      return
    }
    const pending = pendingApproval ?? { key: crypto.randomUUID(), expiresAt: new Date(expiry).toISOString() }
    setPendingApproval(pending)
    setBusy(true)
    const outcome = await approveExecutionSeal(client, workspaceId, projectId, seal, pending.expiresAt, pending.key)
    setBusy(false)
    if (outcome.kind === 'ok' || outcome.kind === 'accepted') {
      setPendingApproval(null)
      setMessage('Execution approval recorded. No run was requested by this action.')
      setRefresh((value) => value + 1)
    } else if (outcome.kind === 'problem') {
      setMessage(outcome.problem.detail)
      setPendingApproval(null)
      setRefresh((value) => value + 1)
    } else if (outcome.kind === 'offline') {
      setMessage('Approval outcome is unknown. Retry keeps exactly the same expiry and operation key.')
    }
  }

  const queue = async (): Promise<void> => {
    if (busy || !current) return
    const key = runKey ?? crypto.randomUUID()
    setRunKey(key)
    setBusy(true)
    const outcome = await requestRun(client, workspaceId, { manifestDigest: seal.manifestDigest }, key)
    setBusy(false)
    if (outcome.kind === 'ok' || outcome.kind === 'accepted') {
      setRequested(outcome.value)
      setMessage('Run requested, not completed. A qualified runner and all live prerequisites are still required.')
    } else if (outcome.kind === 'problem') {
      setMessage(outcome.problem.detail)
    } else if (outcome.kind === 'offline') {
      setMessage('Run request outcome is unknown. Retry reuses this exact request; do not create a new seal to retry it.')
    }
  }

  return <div className="af-panel af-stack">
    <h3>Review exact execution scope</h3>
    <p>Manifest <code>{seal.manifestDigest}</code>, revision {seal.revision}.</p>
    <p>A reserved run ID is not a started run. Approval permits the effects in this manifest;
      it does not approve repairs, merges, deployment or publication.</p>
    <dl>
      <dt>Reserved run</dt><dd><code>{String(canonical['runId'])}</code></dd>
      <dt>Manifest expires</dt><dd>{String(canonical['expiresAt'])}</dd>
      <dt>Maximum actions</dt><dd>{String(canonical['actionBudget'])}</dd>
      <dt>Maximum runtime in seconds</dt><dd>{String(canonical['wallTimeBudgetSeconds'])}</dd>
      <dt>Permitted effects</dt><dd>{Array.isArray(canonical['permittedEffects'])
        ? canonical['permittedEffects'].join(', ') : 'Not available'}</dd>
    </dl>
    <pre className="af-mono">{JSON.stringify(canonical, null, 2)}</pre>
    {message !== null && <Notice tone="information" heading="Execution decision status" headingLevel={4} live>
      <p>{message}</p></Notice>}
    {missing ? <p>No execution approval has been issued for this seal.</p> :
      <ResourceView resource={approval} what="the execution approval">{(value) => <>
        <p>{value.meaning}</p><p>Approval expires {value.expiresAt}.
          {value.revokedAt !== null ? ` Revoked ${value.revokedAt}.` : ''}</p>
        {!current && <p>This approval is not current for execution. A new seal or owner review may be required.</p>}
      </>}</ResourceView>}
    {!mayExecute && <p>Only a workspace owner or maintainer can approve or request execution.</p>}
    {mayExecute && missing && <>
      <FormField label="Approval expires at" hint="Use an explicit timezone, for example 2026-09-18T10:00:00Z. Must not exceed the manifest expiry." required>
        {({ id, describedBy }) => <input id={id} aria-describedby={describedBy}
          value={expiresAt} disabled={busy || pendingApproval !== null}
          onChange={(event) => setExpiresAt(event.target.value)} />}
      </FormField>
      <label htmlFor={reviewId}><input id={reviewId} type="checkbox" checked={reviewed}
        disabled={busy || pendingApproval !== null} onChange={(event) => setReviewed(event.target.checked)} />
        I reviewed the complete manifest and approve only its RUN_EFFECTS scope.</label>
      <Button busy={busy} disabled={!reviewed} onClick={() => void approve()}>
        {pendingApproval === null ? 'Approve exact execution' : 'Retry same approval'}
      </Button>
    </>}
    {mayExecute && current && requested === null && <>
      <p>Requesting queues the exact reserved run. The server rechecks allowance and identity;
        dispatch rechecks approval, expiry, environment and runner eligibility. It is not a passing result.</p>
      <Button variant="primary" busy={busy}
        onClick={() => void queue()}>{runKey === null ? 'Request this approved run' : 'Retry same run request'}</Button>
    </>}
    {requested !== null && <Link className="af-link" to={workspacePath(workspaceId, `runs/${requested.runId}`)}>Open requested run</Link>}
    <Button disabled={busy} onClick={() => setRefresh((value) => value + 1)}>Refresh approval status</Button>
  </div>
}
