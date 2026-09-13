import { useEffect, useRef, useState, type JSX } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { readDiagnosisProfile, recoverDiagnosis, requestDiagnosis, revokeDiagnosis,
  type DiagnosisDecision, type DiagnosisScope } from '../api/diagnosisRequests'
import { getRunEvaluation, type Run } from '../api/resources'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { useSession } from '../session/SessionProvider'
import { DiagnosisRequestForm } from './DiagnosisRequestForm'

const PARAMETER = 'diagnosisOperation'

export const DiagnosisRequestSection = ({ workspaceId, run }: { readonly workspaceId: string; readonly run: Run }): JSX.Element => {
  const { client, state } = useSession()
  const [params, setParams] = useSearchParams()
  const key = params.get(PARAMETER)
  const userId = state.status === 'authenticated' ? state.userId : ''
  const canRequest = state.status === 'authenticated' && state.workspaces.some((item) =>
    item.workspaceId === workspaceId && ['OWNER', 'MAINTAINER'].includes(item.role))
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [message, setMessage] = useState<string | null>(null)
  const active = useRef<{ controller: AbortController; key: string } | null>(null)
  useEffect(() => () => active.current?.controller.abort(), [])
  useEffect(() => {
    if (active.current !== null && active.current.key !== key) {
      active.current.controller.abort(); active.current = null; setBusy(false)
    }
  }, [key])
  const begin = (operationKey: string): AbortController | null => {
    if (active.current !== null) return null
    const controller = new AbortController()
    active.current = { controller, key: operationKey }; setBusy(true); setMessage(null)
    return controller
  }
  const finish = (controller: AbortController): boolean => {
    if (controller.signal.aborted || active.current?.controller !== controller) return false
    active.current = null; setBusy(false); setRevision((value) => value + 1); return true
  }
  const submit = async (scope: DiagnosisScope) => {
    if (!canRequest || key !== null || run.status !== 'COMPLETED' || !['FAIL', 'INCONCLUSIVE'].includes(run.outcome ?? '') || scope.manifestDigest !== run.manifestDigest) return
    const operationKey = crypto.randomUUID()
    const controller = begin(operationKey); if (controller === null) return
    // Persist only an opaque recovery key before POST. No source text, body or credentials in storage.
    const next = new URLSearchParams(params); next.set(PARAMETER, operationKey); setParams(next)
    const result = await requestDiagnosis(client, workspaceId, run.runId, userId, scope, operationKey, controller.signal)
    if (!finish(controller)) return
    setMessage(result.kind === 'accepted' || result.kind === 'ok'
      ? 'Request recorded, not dispatched. Readback below shows its current state.'
      : 'Acceptance was not confirmed. Reconcile this operation below before starting another request.')
  }
  const revoke = async (record: DiagnosisDecision) => {
    if (!canRequest || key === null || record.requestedBy !== userId) return
    const controller = begin(key); if (controller === null) return
    const result = await revokeDiagnosis(client, workspaceId, record, controller.signal)
    if (!finish(controller)) return
    setMessage(result.kind === 'ok'
      ? 'Request revoked. Already-entered provider work is not confirmed stopped, and prior disclosure cannot be undone.'
      : 'Revocation was not confirmed. Read this original operation again; do not assume the provider stopped.')
  }
  const newRequest = () => {
    if (busy) return
    const next = new URLSearchParams(params); next.delete(PARAMETER); setParams(next)
    setMessage(null); setOpen(true)
  }
  return <section className="af-stack" aria-labelledby="diagnosis-request-heading">
    <h2 id="diagnosis-request-heading">Request model diagnosis</h2>
    <p>Read-only review first, then an explicit disclosure decision. This page never invokes a model or starts a reader.</p>
    {message !== null && <Notice tone="information" heading="Diagnosis request result" headingLevel={3} live><p>{message}</p></Notice>}
    {key !== null ? <RequestReadback key={`${workspaceId}:${run.runId}:${key}`} workspaceId={workspaceId} runId={run.runId}
      userId={userId} operationKey={key} revision={revision} busy={busy} canRequest={canRequest} revoke={revoke} newRequest={newRequest} /> :
      canRequest && run.status === 'COMPLETED' && ['FAIL', 'INCONCLUSIVE'].includes(run.outcome ?? '') ? <>
        <Button disabled={busy} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Close diagnosis form' : 'Scope a diagnosis request'}</Button>
        {open && <RequestInputs workspaceId={workspaceId} run={run} busy={busy} submit={(scope) => void submit(scope)} />}
      </> : <p>Only an owner or maintainer can request diagnosis of an original completed FAIL or INCONCLUSIVE evaluation.</p>}
  </section>
}

const RequestInputs = ({ workspaceId, run, busy, submit }: {
  readonly workspaceId: string; readonly run: Run; readonly busy: boolean; readonly submit: (scope: DiagnosisScope) => void
}): JSX.Element => {
  const { client } = useSession()
  const profile = useResource((signal) => readDiagnosisProfile(client, workspaceId, signal), [client, workspaceId])
  const evaluation = useResource((signal) => getRunEvaluation(client, workspaceId, run.runId, signal), [client, workspaceId, run.runId])
  return <ResourceView resource={profile} what="the diagnosis provider profile">{(p) =>
    <ResourceView resource={evaluation} what="the original diagnosis evaluation">{(e) =>
      <DiagnosisRequestForm run={run} evaluation={e} profile={p} busy={busy} submit={submit} />
    }</ResourceView>
  }</ResourceView>
}

const RequestReadback = ({ workspaceId, runId, userId, operationKey, revision, busy, canRequest, revoke, newRequest }: {
  readonly workspaceId: string; readonly runId: string; readonly userId: string; readonly operationKey: string
  readonly revision: number; readonly busy: boolean; readonly canRequest: boolean
  readonly revoke: (record: DiagnosisDecision) => Promise<void>; readonly newRequest: () => void
}): JSX.Element => {
  const { client } = useSession()
  const record = useResource((signal) => recoverDiagnosis(client, workspaceId, runId, userId, operationKey, signal),
    [client, workspaceId, runId, userId, operationKey, revision])
  const [confirmed, setConfirmed] = useState(false)
  if (busy) return <p role="status">Decision pending. Leaving this page does not prove the server rolled it back; its recovery key is retained in this address.</p>
  return <div className="af-stack">
    <p>Recovery key: <code>{operationKey}</code>. This address can be reopened by the original requester; the key is not permission to access another person's request.</p>
    {record.state.kind === 'problem' && record.state.problem.status === 404 ?
      <Notice tone="warning" heading="Request acceptance is not confirmed" headingLevel={3}>
        <p>No matching request is available to this read. The original submission may still be settling, or an older request may have no recovery key. Read again or reconcile with an operator; do not create a replacement request as a retry.</p>
      </Notice> : <ResourceView resource={record} what="this original diagnosis operation">{(value) => <>
        <h3>Stored diagnosis request</h3>
        <dl>
          <dt>Request ID for operator dispatch</dt><dd><code>{value.requestId}</code></dd>
          <dt>Original assertion</dt><dd>{value.scope.assertionId}</dd>
          <dt>Component</dt><dd>{value.scope.componentName} — <code>{JSON.stringify(value.scope.componentPath)}</code></dd>
          <dt>Invocation state as read</dt><dd>{value.invocationState}</dd>
          <dt>Expires (UTC)</dt><dd><time dateTime={value.expiresAt}>{value.expiresAt}</time></dd>
          <dt>Revoked</dt><dd>{value.revokedAt ?? 'No revocation recorded in this read'}</dd>
        </dl>
        <details><summary>Stored request scope</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value.scope, null, 2)}</pre></details>
        <p>Expiry and permission are checked by the worker. STARTED or UNCONFIRMED is not zero provider use; do not redispatch under a new identity to bypass it.</p>
        {value.findingId && <Link className="af-link" to={`/w/${encodeURIComponent(workspaceId)}/findings/${encodeURIComponent(value.findingId)}`}>Read the retained finding</Link>}
        {canRequest && value.revokedAt === null && <>
          <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> Permanently revoke this original diagnosis request</label>
          <Button disabled={!confirmed} onClick={() => { setConfirmed(false); void revoke(value) }}>Revoke diagnosis request</Button>
        </>}
        {canRequest && (Boolean(value.findingId) || (value.revokedAt !== null && ['NOT_STARTED', 'NOT_CALLED'].includes(value.invocationState ?? ''))) &&
          <Button onClick={newRequest}>Review a new, separate diagnosis request</Button>}
      </>}</ResourceView>}
    <Button disabled={busy} onClick={record.reload}>Read this diagnosis operation again</Button>
  </div>
}
