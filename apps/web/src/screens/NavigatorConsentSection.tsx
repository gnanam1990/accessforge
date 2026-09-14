import { useEffect, useId, useRef, useState, type JSX } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { ApiOutcome } from '../api/client'
import { issueNavigatorConsent, readNavigatorConsent, reviewNavigatorConsent, revokeNavigatorConsent,
  type NavigatorConsent, type NavigatorInvocation, type NavigatorModelScope } from '../api/navigatorConsent'
import type { Run } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { Button } from '../components/Button'
import { Dialog } from '../components/Dialog'
import { ErrorSummary, type FieldError } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'

const OPERATION = 'navigatorConsentOperation'
const uncertainty = 'The decision is not confirmed. Read stored model consent to reconcile. Do not issue a replacement grant; cancellation does not prove a provider call stopped.'
const failure = (result: ApiOutcome<unknown>) => result.kind === 'problem' ? `${result.problem.detail} ${uncertainty}` : uncertainty
const STATES: Record<NavigatorInvocation['status'], string> = {
  STARTED: 'Reservation open. Provider activity is not confirmed; the full hold remains and replay is blocked.',
  UNCONFIRMED: 'Provider or action outcome is uncertain. The full hold remains; do not retry this turn.',
  RECORDED: 'Invocation disposition retained. This is not task success, measured token usage or a currency spending cap.',
  NOT_CALLED: 'Provider entry was ruled out. The hold is released, but this consumed turn cannot be replayed.',
}

export const NavigatorConsentSection = ({ workspaceId, run }: { readonly workspaceId: string; readonly run: Run }): JSX.Element => {
  const { state, client } = useSession()
  const [open, setOpen] = useState(false)
  const role = state.status === 'authenticated' ? state.workspaces.find(item => item.workspaceId === workspaceId)?.role : undefined
  const actor = state.status === 'authenticated' ? state.userId : 'signed-out'
  return <section className="af-stack">
    <h2>Navigator model consent</h2>
    <p>Review provider disclosure and call limits separately from execution approval and reader startup. Storing consent does not launch a model or a desktop action.</p>
    {!open ? <Button onClick={() => setOpen(true)}>Inspect model consent</Button> :
      <ConsentPanel key={`${workspaceId}:${run.runId}:${actor}:${role}:${client.epoch}`} workspaceId={workspaceId} run={run}
        canApprove={role === 'OWNER' || role === 'MAINTAINER'} onClose={() => setOpen(false)} />}
  </section>
}

const ConsentPanel = ({ workspaceId, run, canApprove, onClose }: {
  readonly workspaceId: string; readonly run: Run; readonly canApprove: boolean; readonly onClose: () => void
}): JSX.Element => {
  const { client } = useSession()
  const [search, setSearch] = useSearchParams()
  const address = useRef({ search, setSearch }); address.current = { search, setSearch }
  const attempted = search.has(OPERATION)
  const history = useResource(signal => readNavigatorConsent(client, workspaceId, run.runId, signal), [client, workspaceId, run.runId])
  const [busy, setBusy] = useState(false)
  const active = useRef<AbortController | null>(null)
  const [scope, setScope] = useState<NavigatorModelScope | null>(null)
  const [maxCalls, setMaxCalls] = useState('1')
  const [expires, setExpires] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [errors, setErrors] = useState<readonly FieldError[]>([])
  const [submission, setSubmission] = useState(0)
  const [revokeTarget, setRevokeTarget] = useState<NavigatorConsent | null>(null)
  const prefix = useId(), callsId = `${prefix}-calls`, expiryId = `${prefix}-expiry`
  const latest = useRef(run); latest.current = run
  const eligible = ['QUEUED', 'LEASED', 'RUNNING'].includes(run.status) && run.cancellationRequestedAt === null && !run.quarantined
  useEffect(() => () => active.current?.abort(), [])
  useEffect(() => { setScope(null); setAcknowledged(false); setErrors([]) }, [run.revision, run.manifestDigest, eligible])

  const operate = async <T,>(work: (signal: AbortSignal) => Promise<ApiOutcome<T>>): Promise<ApiOutcome<T> | null> => {
    if (active.current !== null) return null
    const controller = new AbortController(); active.current = controller; setBusy(true)
    try {
      const result = await work(controller.signal)
      return controller.signal.aborted ? null : result
    } catch { return controller.signal.aborted ? null : { kind: 'offline' } }
    finally { active.current = null; if (!controller.signal.aborted) setBusy(false) }
  }
  const review = async () => {
    if (!canApprove || !eligible || attempted) return
    setScope(null); setAcknowledged(false); setMessage(null); setErrors([])
    const result = await operate(signal => reviewNavigatorConsent(client, workspaceId, run.runId, signal))
    if (result === null) return
    if (result.kind !== 'ok') {
      setMessage(`${result.kind === 'problem' ? result.problem.detail : 'Model scope could not be read.'} No consent was submitted by this review. Read the run state before reviewing again.`)
      return
    }
    if (result.value.revision !== latest.current.revision || result.value.manifestDigest !== latest.current.manifestDigest) {
      setMessage('The run changed. Reload this run before reviewing a new scope.'); return
    }
    setScope(result.value); setMaxCalls('1'); setExpires(result.value.maximumExpiresAt)
  }
  const submit = async () => {
    if (active.current !== null || scope === null || !canApprove || !eligible || attempted || !acknowledged) return
    if (scope.revision !== latest.current.revision || scope.manifestDigest !== latest.current.manifestDigest) {
      setScope(null); setAcknowledged(false); setMessage('The run changed. Review its current scope again.'); return
    }
    const problems: FieldError[] = []
    const count = Number(maxCalls), end = Date.parse(expires)
    if (!/^[1-9]\d*$/.test(maxCalls) || !Number.isSafeInteger(count) || count > scope.maximumCalls) {
      problems.push({ fieldId: callsId, message: `Choose 1 to ${scope.maximumCalls} model calls.` })
    }
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(expires) || !Number.isFinite(end) ||
        end <= Date.now() || end > Date.parse(scope.maximumExpiresAt) || new Date(end).toISOString().slice(0, 19) !== expires.slice(0, 19)) {
      problems.push({ fieldId: expiryId, message: `Enter a future UTC expiry no later than ${scope.maximumExpiresAt}.` })
    }
    setErrors(problems); setSubmission(value => value + 1)
    if (problems.length > 0) return
    // Keep a non-secret intent marker in this run's URL before the write. Reload/back/close cannot
    // silently offer a replacement after an unknown response. Stored consent is read by run ID.
    const key = crypto.randomUUID(), next = new URLSearchParams(search)
    next.set(OPERATION, key); setSearch(next, { replace: true })
    const result = await operate(signal => issueNavigatorConsent(client, workspaceId, run.runId, scope, count, expires, key, signal))
    if (result === null) return
    setScope(null); setAcknowledged(false)
    // Only recognised pre-write validation/authorization refusals permit a fresh review. A
    // conflict, malformed success, incomplete body or lost response remains reconciliation-only.
    const refused = result.kind === 'problem' &&
      ((result.problem.status === 400 && result.problem.code === 'INVALID_INPUT') ||
       (result.problem.status === 403 && ['PERMISSION_DENIED', 'CSRF_REQUIRED'].includes(result.problem.code)))
    if (refused) {
      // Router setters capture a render's search parameters; unlike React state updaters,
      // their callback does not read the newest location after an awaited request.
      const updated = new URLSearchParams(address.current.search)
      if (!updated.has(OPERATION) || updated.get(OPERATION) === key) {
        updated.delete(OPERATION)
        address.current.setSearch(updated, { replace: true })
      }
    }
    setMessage(result.kind === 'ok' ? 'Model consent stored. No provider or desktop work was started by this button.' :
      refused ? 'The server refused this request before storing consent. Read the current state and review a new scope before submitting again.' : failure(result))
    history.reload()
  }
  const revoke = async () => {
    if (!canApprove || revokeTarget === null) return
    const result = await operate(signal => revokeNavigatorConsent(client, workspaceId, revokeTarget, signal))
    if (result === null) return
    setRevokeTarget(null)
    setMessage(result.kind === 'ok' ? 'Consent permanently revoked. Already-entered provider work is not confirmed stopped, and unresolved holds remain.' : failure(result))
    history.reload()
  }
  const missing = history.state.kind === 'problem' && history.state.problem.status === 404
  return <div className="af-stack">
    {message !== null && <Notice tone="information" heading="Model consent request result" headingLevel={3} live><p>{message}</p></Notice>}
    {busy && <p role="status">Decision pending. Leaving this page does not prove rollback.</p>}
    {missing ? <>
      <p>No stored model consent is available in this read.</p>
      {attempted && !busy ? <Notice tone="warning" heading="Reconcile the original consent request" headingLevel={3}>
        <p>A submission was attempted from this address. A missing read does not prove rollback. Read again or ask an operator to reconcile; this screen will not submit a replacement.</p>
      </Notice> : canApprove && eligible ? <>
        <Button busy={busy} onClick={() => void review()}>Review model disclosure and limits</Button>
        {scope !== null && <form className="af-stack" noValidate onSubmit={event => { event.preventDefault(); void submit() }}>
          <h3>Review the exact model grant</h3>
          <ErrorSummary errors={errors} submissionId={submission} headingLevel={4} />
          <p>{scope.disclosure}</p>
          <ProfileDetails value={scope} />
          <FormField id={callsId} label="Maximum model calls" required hint={`From 1 to ${scope.maximumCalls}; each call reserves ${scope.tokensPerCall} tokens including configured attempts.`}
            {...(errors.find(error => error.fieldId === callsId) ? { error: errors.find(error => error.fieldId === callsId)!.message } : {})}>
            {({ id, describedBy, invalid }) => <input id={id} type="number" inputMode="numeric" min={1} max={scope.maximumCalls} step={1}
              value={maxCalls} required disabled={busy} aria-describedby={describedBy} aria-invalid={invalid || undefined}
              onChange={event => { setMaxCalls(event.target.value); setAcknowledged(false) }} />}
          </FormField>
          <FormField id={expiryId} label="Model consent expiry (UTC)" required hint={`Use a timestamp ending in Z, no later than ${scope.maximumExpiresAt}.`}
            {...(errors.find(error => error.fieldId === expiryId) ? { error: errors.find(error => error.fieldId === expiryId)!.message } : {})}>
            {({ id, describedBy, invalid }) => <input id={id} value={expires} required disabled={busy} aria-describedby={describedBy} aria-invalid={invalid || undefined}
              onChange={event => { setExpires(event.target.value); setAcknowledged(false) }} />}
          </FormField>
          <label className="af-consent-acknowledgement"><input type="checkbox" checked={acknowledged} disabled={busy}
            onChange={event => setAcknowledged(event.target.checked)} />{' '}I explicitly permit disclosure of the listed task, safe fixture values and retained reader announcements to this provider, including billable calls and configured retries, within these limits.</label>
          <p>This one-time per-run grant cannot be edited or reissued after revocation. Token holds are not measured usage or a currency spending cap. Execution and reader-startup approvals remain separate.</p>
          <Button type="submit" variant="primary" busy={busy} disabled={!acknowledged}>Store navigator model consent</Button>
        </form>}
      </> : <p>Only a workspace owner or maintainer can review and approve model consent for an eligible, execution-approved run.</p>}
    </> : <ResourceView resource={history} what="stored navigator model consent">{consent => consent.manifestDigest !== run.manifestDigest ?
      <Notice tone="problem" heading="Model consent identity differs" headingLevel={3}><p>Reload this run before making a decision.</p></Notice> : <>
        <h3>{consent.revokedAt === null ? 'Stored model consent' : 'Revoked model consent'}</h3>
        <p>This is retained history, not proof of current permission, physical readiness or a model invocation. Current authority and expiry are checked again by the coordinator.</p>
        <p>{consent.disclosure}</p>
        <dl><dt>Consent ID for the trusted operator</dt><dd><code>{consent.consentId}</code></dd>
          <dt>Approver</dt><dd><code>{consent.actorId}</code></dd>
          <dt>Call limit</dt><dd>{consent.maxCalls}</dd>
          <dt>Expires (UTC)</dt><dd><time dateTime={consent.expiresAt}>{consent.expiresAt}</time></dd>
          <dt>Revoked at</dt><dd>{consent.revokedAt ?? 'No revocation recorded in this read'}</dd></dl>
        <ProfileDetails value={consent} />
        <InvocationHistory calls={consent.invocations} />
        {canApprove && consent.revokedAt === null && <Button variant="destructive" disabled={busy} onClick={() => setRevokeTarget(consent)}>Revoke navigator model consent</Button>}
      </>}</ResourceView>}
    <div className="af-row"><Button disabled={busy} onClick={() => { setScope(null); setAcknowledged(false); history.reload() }}>Read stored model consent again</Button>
      <Button disabled={busy} onClick={onClose}>Close model consent</Button></div>
    <Dialog open={revokeTarget !== null && canApprove} heading="Permanently revoke model consent?" onClose={() => { if (!busy) setRevokeTarget(null) }} actions={<>
      <Button disabled={busy} onClick={() => setRevokeTarget(null)}>Keep model consent</Button>
      <Button variant="destructive" busy={busy} onClick={() => void revoke()}>Confirm model revocation</Button>
    </>}><p>This exact grant cannot be restored or reissued. Already-entered provider work is not undone. Revocation does not clear uncertain token holds or acknowledge that a desktop stopped.</p></Dialog>
  </div>
}

const ProfileDetails = ({ value }: { readonly value: NavigatorModelScope | NavigatorConsent }): JSX.Element => <>
  <dl><dt>Provider</dt><dd>{value.modelProfile.provider}</dd>
    <dt>Model</dt><dd><code>{value.modelProfile.model_id}</code></dd>
    <dt>Region</dt><dd>{value.modelProfile.region_name}</dd>
    <dt>Reserved tokens per call</dt><dd>{value.tokensPerCall} — not measured usage or money</dd></dl>
  <details><summary>Exact sealed profile and identities</summary>
    <dl><dt>Manifest digest</dt><dd><code>{value.manifestDigest}</code></dd>
      <dt>Model configuration digest</dt><dd><code>{value.modelConfigDigest}</code></dd></dl>
    <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value.modelProfile, null, 2)}</pre>
  </details>
</>

const InvocationHistory = ({ calls }: { readonly calls: readonly NavigatorInvocation[] }): JSX.Element => {
  const [visible, setVisible] = useState(20)
  const chronological = [...calls].sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt) || a.operationId.localeCompare(b.operationId))
  return <details><summary>Retained model invocations ({calls.length})</summary>
    {calls.length === 0 ? <p>No invocation is retained in this read. This does not prove zero provider activity or cost.</p> : <>
      <p>Showing the latest {Math.min(visible, calls.length)} of {calls.length} retained invocations. No retry control is provided.</p>
      <ol>{chronological.slice(-visible).map(call => <li key={call.operationId}><p><code>{call.operationId}</code> — {call.status}</p>
        <p>{STATES[call.status]}</p><p>After action {call.afterActionSequence}; reserved {call.reservedTokens} tokens.
          {' '}Created <time dateTime={call.createdAt}>{call.createdAt}</time>. Finished {call.finishedAt ?? 'not recorded'}.</p></li>)}</ol>
      {calls.length > visible && <Button onClick={() => setVisible(count => count + 20)}>Show older invocations</Button>}
    </>}
  </details>
}
