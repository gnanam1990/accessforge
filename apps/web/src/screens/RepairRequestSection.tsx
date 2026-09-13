import { useEffect, useRef, useState, type JSX } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { isDiagnosisAnalysis, type DiagnosisHistory } from '../api/diagnosis'
import { recoverRepair, requestRepair, revokeRepair, type RepairDecision, type RepairScope } from '../api/repairRequests'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { useSession } from '../session/SessionProvider'
import { RepairRequestForm } from './RepairRequestForm'

const PARAMETER = 'repairOperation'
interface Props {
  readonly workspaceId: string
  readonly findingId: string
  readonly findingStatus: string
  readonly history: DiagnosisHistory | undefined
}
export const RepairRequestSection = (props: Props): JSX.Element | null => {
  const { state } = useSession()
  if (state.status !== 'authenticated') return null
  const role = state.workspaces.find((item) => item.workspaceId === props.workspaceId)?.role
  if (role === undefined) return null
  // Identity/membership changes discard disclosure acknowledgement and fence in-flight mutations.
  return <Requests key={`${props.workspaceId}:${props.findingId}:${state.userId}:${role}`} {...props}
    userId={state.userId} canRequest={['OWNER', 'MAINTAINER'].includes(role)} />
}
const Requests = ({ workspaceId, findingId, findingStatus, history, userId, canRequest }: Props & {
  readonly userId: string; readonly canRequest: boolean
}): JSX.Element => {
  const { client } = useSession()
  const [params, setParams] = useSearchParams()
  const key = params.get(PARAMETER)
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [message, setMessage] = useState<string | null>(null)
  const active = useRef<{ key: string; controller: AbortController } | null>(null)
  useEffect(() => () => active.current?.controller.abort(), [])
  useEffect(() => {
    if (active.current !== null && active.current.key !== key) {
      active.current.controller.abort(); active.current = null; setBusy(false)
    }
  }, [key])
  const items = history?.items ?? []
  const eligible = items.filter((item) => item.deletedAt === null &&
    !items.some((next) => next.supersedes === item.diagnosisId) && isDiagnosisAnalysis(item.analysis) &&
    item.analysis.support === 'SOURCE_LINKED' && item.analysis.missing_information.length === 0 &&
    item.analysis.repair_brief !== null && item.analysis.repair_brief.stop_recommendation === null)
  const openFinding = ['CANDIDATE', 'REPRODUCED'].includes(findingStatus)
  const begin = (operation: string): AbortController | null => {
    if (active.current !== null) return null
    const controller = new AbortController()
    active.current = { key: operation, controller }; setBusy(true); setMessage(null)
    return controller
  }
  const finish = (controller: AbortController): boolean => {
    if (controller.signal.aborted || active.current?.controller !== controller) return false
    active.current = null; setBusy(false); setRevision((value) => value + 1); return true
  }
  const submit = async (scope: RepairScope) => {
    if (!canRequest || !openFinding || key !== null || scope.diagnosisId !== selected ||
        !eligible.some((item) => item.diagnosisId === selected) || !scope.billableCallAcknowledged) return
    const operation = crypto.randomUUID(), controller = begin(operation)
    if (controller === null) return
    // Retain only an opaque operation identity before any write. Never store source or consent bodies.
    const next = new URLSearchParams(params); next.set(PARAMETER, operation); setParams(next)
    const result = await requestRepair(client, workspaceId, findingId, userId, scope, operation, controller.signal)
    if (!finish(controller)) return
    setMessage(result.kind === 'accepted' || result.kind === 'ok'
      ? 'Repair request recorded, not dispatched. Read its current state below.'
      : 'Acceptance was not confirmed. Read this original operation; do not submit a replacement as a retry.')
  }
  const revoke = async (record: RepairDecision) => {
    if (key === null || record.requestedBy !== userId) return
    const controller = begin(key); if (controller === null) return
    const result = await revokeRepair(client, workspaceId, record, controller.signal)
    if (!finish(controller)) return
    setMessage(result.kind === 'ok'
      ? 'Request revoked. Prior source disclosure cannot be undone; already-entered provider work is not confirmed stopped.'
      : 'Revocation was not confirmed. Read the original operation again; do not assume provider work stopped.')
  }
  return <section className="af-stack" aria-labelledby="repair-request-heading">
    <h2 id="repair-request-heading">Request a model repair proposal</h2>
    <p>A retained source-linked diagnosis is required. A model proposal remains separate from patch approval, application and verification.</p>
    {message !== null && <Notice tone="information" heading="Repair request result" headingLevel={3} live><p>{message}</p></Notice>}
    {key !== null ? <Readback key={key} workspaceId={workspaceId} findingId={findingId} userId={userId}
      operation={key} revision={revision} busy={busy} canRequest={canRequest && openFinding} revoke={revoke}
      newRequest={() => {
        if (busy) return
        const next = new URLSearchParams(params); next.delete(PARAMETER); setParams(next)
        setSelected(''); setMessage(null)
      }} /> : !canRequest ? <p>Only an owner or maintainer can record a repair request.</p> :
      !openFinding || eligible.length === 0 ? <p>No eligible retained diagnosis is available for repair on this open finding. Refresh the finding after a diagnosis is retained.</p> : <>
        <label htmlFor="repair-diagnosis">Retained diagnosis for repair</label>
        <select id="repair-diagnosis" value={selected} disabled={busy} onChange={(event) => setSelected(event.target.value)}>
          <option value="">Select a diagnosis to review its disclosure scope</option>
          {eligible.map((item) => <option key={item.diagnosisId} value={item.diagnosisId}>{item.recordedAt} — {item.diagnosisId}</option>)}
        </select>
        {selected !== '' && eligible.some((item) => item.diagnosisId === selected) &&
          <RepairRequestForm key={`${selected}:${revision}`} workspaceId={workspaceId} findingId={findingId}
            diagnosisId={selected} busy={busy} submit={(scope) => void submit(scope)} />}
      </>}
  </section>
}

const STATES: Record<RepairDecision['invocationState'], string> = {
  NOT_STARTED: 'No invocation is recorded in this read. Dispatch still requires valid consent and current source authority.',
  STARTED: 'A reservation was committed. The provider may have been entered; this is not completion or zero use.',
  UNCONFIRMED: 'Provider use or persistence is unresolved. Reconcile the original request; do not redispatch with a new identity.',
  RECORDED: 'Invocation settlement was recorded. Only a retained delivery receipt identifies any proposed patch.',
  NOT_CALLED: 'This invocation was settled without provider entry. This original request is not automatically dispatched again.',
}
const Readback = ({ workspaceId, findingId, userId, operation, revision, busy, canRequest, revoke, newRequest }: {
  readonly workspaceId: string; readonly findingId: string; readonly userId: string; readonly operation: string
  readonly revision: number; readonly busy: boolean; readonly canRequest: boolean
  readonly revoke: (record: RepairDecision) => Promise<void>; readonly newRequest: () => void
}): JSX.Element => {
  const { client } = useSession()
  const record = useResource((signal) => recoverRepair(client, workspaceId, findingId, userId, operation, signal),
    [client, workspaceId, findingId, userId, operation, revision])
  const [confirmed, setConfirmed] = useState(false)
  if (busy) return <p role="status">Decision pending. Leaving does not prove rollback; this address retains the recovery key.</p>
  return <div className="af-stack">
    <p>Recovery key: <code>{operation}</code>. Reopen this address as the original requester. Recovery only reads; it never resubmits.</p>
    {record.state.kind === 'problem' && record.state.problem.status === 404 ?
      <Notice tone="warning" heading="Repair acceptance is not confirmed" headingLevel={3}>
        <p>No matching request is available to this read. Submission may still be settling or the request may belong to another actor. Read again or reconcile with an operator; do not create a replacement as a retry.</p>
      </Notice> : <ResourceView resource={record} what="this original repair operation">{(value) => <>
        <h3>Stored repair request</h3>
        <dl>
          <dt>Request ID for operator dispatch</dt><dd><code>{value.requestId}</code></dd>
          <dt>Invocation state as read</dt><dd>{value.invocationState}</dd>
          <dt>Expires (UTC)</dt><dd><time dateTime={value.expiresAt}>{value.expiresAt}</time></dd>
          <dt>Revoked</dt><dd>{value.revokedAt ?? 'No revocation recorded in this read'}</dd>
        </dl>
        <p>{STATES[value.invocationState]}</p>
        <p>Expiry and permission are checked at dispatch and before recording delivery. Revocation does not release an unresolved provider reservation.</p>
        <details><summary>Stored repair scope</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value.scope, null, 2)}</pre></details>
        {value.delivery === null ? <p>No retained delivery receipt is available in this read. This does not establish a patch or zero provider use.</p> : <>
          <h4>Retained model delivery</h4>
          {value.delivery.outcome === 'PROPOSED' && value.delivery.patchId !== null ? <Link className="af-link"
            to={`/w/${encodeURIComponent(workspaceId)}/patches/${encodeURIComponent(value.delivery.patchId)}`}>Review the proposed patch</Link> :
            <p>NO_PROPOSAL — delivery was recorded without a proposed patch.</p>}
          <p>Delivery is not approval, application or verification.</p>
          <details><summary>Exact delivery receipt</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value.delivery, null, 2)}</pre></details>
        </>}
        {value.revokedAt === null && <>
          <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> Permanently revoke this original repair request</label>
          <Button disabled={!confirmed} onClick={() => { setConfirmed(false); void revoke(value) }}>Revoke repair request</Button>
        </>}
        {canRequest && (['RECORDED', 'NOT_CALLED'].includes(value.invocationState) ||
          (value.invocationState === 'NOT_STARTED' && (value.revokedAt !== null || Date.parse(value.expiresAt) <= Date.now()))) &&
          <Button onClick={newRequest}>Review a new, separate repair request</Button>}
      </>}</ResourceView>}
    <Button disabled={busy} onClick={record.reload}>Read this repair operation again</Button>
  </div>
}
