import { useEffect, useRef, useState, type JSX } from 'react'
import type { ApiOutcome } from '../api/client'
import { issueReaderConsent, readReaderConsent, reviewReaderConsent, revokeReaderConsent, type ReaderConsent, type ReaderConsentScope } from '../api/readerConsent'
import { listRunners, type Run } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { Button } from '../components/Button'
import { Dialog } from '../components/Dialog'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'

const EFFECTS: Record<string, string> = {
  TERMINATE_AND_RESTART_VOICEOVER: 'Terminate and restart VoiceOver.',
  MOUNT_GUIDEPUP_READER_PREFERENCES: 'Mount the Guidepup reader preferences.',
  SDK_INTERNAL_STARTUP_ATTEMPTS: 'Allow the SDK’s internal startup attempts.',
  RESTORE_PREFERENCES_DURING_NORMAL_STOP: 'Restore preferences during a normal STOP.',
  GRANT_TCC_PERMISSIONS: 'Grant macOS privacy/accessibility permissions.',
  INITIAL_APPLESCRIPT_CONFIGURATION: 'Perform initial AppleScript configuration.',
}
const failure = (outcome: ApiOutcome<unknown>): string => outcome.kind === 'problem' ? outcome.problem.detail :
  'The server did not confirm the decision. Read the stored consent again to reconcile; do not assume the reader stopped or retry the grant.'

// One operation at a time; leaving the target aborts reads and discards late mutation replies.
const useOperation = () => {
  const active = useRef<AbortController | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => () => active.current?.abort(), [])
  const start = (): AbortController | null => {
    if (active.current !== null) return null
    const controller = new AbortController(); active.current = controller; setBusy(true)
    return controller
  }
  const finish = (controller: AbortController): boolean => {
    if (controller.signal.aborted) return false
    active.current = null; setBusy(false); return true
  }
  return { busy, start, finish }
}

export const ReaderStartupSection = ({ workspaceId, run }: { readonly workspaceId: string; readonly run: Run }): JSX.Element => {
  const [open, setOpen] = useState(false)
  const operation = useOperation()
  return <section className="af-stack">
    <h2>Reader startup consent</h2>
    <p>Separate operator permission for VoiceOver restart and preference changes. This neither starts a reader nor grants macOS permissions.</p>
    <Button aria-expanded={open} disabled={operation.busy} onClick={() => setOpen(!open)}>{open ? 'Close reader consent' : 'Inspect reader consent'}</Button>
    {open && <ConsentPanel key={`${workspaceId}:${run.runId}`} workspaceId={workspaceId} run={run} operation={operation} />}
  </section>
}

type Operation = ReturnType<typeof useOperation>

const ConsentPanel = ({ workspaceId, run, operation }: { readonly workspaceId: string; readonly run: Run; readonly operation: Operation }): JSX.Element => {
  const { client, state } = useSession()
  const owner = state.status === 'authenticated' && state.workspaces.some((membership) => membership.workspaceId === workspaceId && membership.role === 'OWNER')
  const history = useResource((signal) => readReaderConsent(client, workspaceId, run.runId, signal), [client, workspaceId, run.runId])
  const [message, setMessage] = useState<string | null>(null)
  const changed = (text: string) => { setMessage(text); history.reload() }
  const eligible = ['QUEUED', 'LEASED', 'RUNNING'].includes(run.status) && run.cancellationRequestedAt === null && !run.quarantined
  return <div className="af-stack">
    {message !== null && <Notice tone="information" heading="Consent request result" headingLevel={3} live><p>{message}</p></Notice>}
    {history.state.kind === 'problem' && history.state.problem.status === 404 ? <>
      <p>No stored reader startup consent is available for this run.</p>
      {owner && eligible ? <ConsentForm workspaceId={workspaceId} run={run} changed={changed} operation={operation} /> :
        <p>Only a workspace owner can issue consent for an eligible, approved run before its first action.</p>}
    </> : <ResourceView resource={history} what="the stored reader startup decision">
      {(consent) => consent.manifestDigest !== run.manifestDigest ?
        <Notice tone="problem" heading="Consent identity differs" headingLevel={3}><p>Reload this run before making a decision.</p></Notice> :
        <ConsentHistory key={consent.consentId} consent={consent} workspaceId={workspaceId} owner={owner} changed={changed} operation={operation} />}
    </ResourceView>}
    <Button disabled={operation.busy} onClick={history.reload}>Read stored consent again</Button>
  </div>
}

const ConsentHistory = ({ consent, workspaceId, owner, changed, operation }: {
  readonly consent: ReaderConsent; readonly workspaceId: string; readonly owner: boolean; readonly changed: (message: string) => void; readonly operation: Operation
}): JSX.Element => {
  const { client } = useSession()
  const [confirm, setConfirm] = useState(false)
  const revoke = async () => {
    const controller = operation.start(); if (controller === null) return
    const result = await revokeReaderConsent(client, workspaceId, consent, controller.signal)
    if (!operation.finish(controller)) return
    setConfirm(false)
    changed(result.kind === 'ok' && result.value.consentId === consent.consentId && result.value.revokedAt !== null ?
      'Consent revoked. Already-entered reader work is not confirmed stopped. This grant cannot be reissued.' : failure(result))
  }
  return <div className="af-stack">
    <h3>{consent.revokedAt === null ? 'Stored operator decision' : 'Revoked operator decision'}</h3>
    <p>Stored history is not current machine authority or physical readiness. Expiry and live authorization are rechecked by the native startup path.</p>
    <dl>
      <dt>Consent</dt><dd><code>{consent.consentId}</code></dd>
      <dt>Runner</dt><dd><code>{consent.runnerId}</code></dd>
      <dt>Desktop identity</dt><dd><code>{consent.desktopSessionKey}</code></dd>
      <dt>Operator</dt><dd><code>{consent.actorId}</code></dd>
      <dt>Expires (UTC)</dt><dd><time dateTime={consent.expiresAt}>{consent.expiresAt}</time></dd>
      <dt>Revoked at</dt><dd>{consent.revokedAt ?? 'No revocation recorded in this read'}</dd>
      <dt>Bound session</dt><dd><code>{consent.boundSessionId ?? 'Not yet bound'}</code></dd>
    </dl>
    {owner && consent.revokedAt === null && <Button variant="destructive" onClick={() => setConfirm(true)}>Revoke reader startup consent</Button>}
    <Dialog open={confirm && owner} heading="Revoke this reader startup consent?" onClose={() => { if (!operation.busy) setConfirm(false) }} actions={<>
      <Button disabled={operation.busy} onClick={() => setConfirm(false)}>Keep consent</Button>
      <Button variant="destructive" busy={operation.busy} onClick={() => void revoke()}>Confirm permanent revocation</Button>
    </>}><p>This exact grant cannot be restored or reissued. Already-started SDK work is not undone. Run cancellation is a separate action.</p></Dialog>
  </div>
}

const ConsentForm = ({ workspaceId, run, changed, operation }: {
  readonly workspaceId: string; readonly run: Run; readonly changed: (message: string) => void; readonly operation: Operation
}): JSX.Element => {
  const { client } = useSession()
  const runners = useResource((signal) => listRunners(client, workspaceId, signal), [client, workspaceId])
  const [runnerId, setRunnerId] = useState('')
  const [scope, setScope] = useState<ReaderConsentScope | null>(null)
  const [expires, setExpires] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expiryError, setExpiryError] = useState<string | undefined>(undefined)
  const expiryInput = useRef<HTMLInputElement>(null)
  const key = useRef(crypto.randomUUID())
  const latestRun = useRef(run); latestRun.current = run
  useEffect(() => { setScope(null); setAcknowledged(false); setError(null) }, [run.revision, run.manifestDigest])
  const review = async () => {
    const controller = operation.start(); if (controller === null) return
    setError(null); setExpiryError(undefined); setScope(null); setAcknowledged(false)
    const result = await reviewReaderConsent(client, workspaceId, run.runId, runnerId, controller.signal)
    if (!operation.finish(controller)) return
    if (result.kind !== 'ok') { setError(failure(result)); return }
    if (result.value.manifestDigest !== latestRun.current.manifestDigest || result.value.revision !== latestRun.current.revision) {
      setError('The run changed. Reload the run before reviewing consent.'); return
    }
    setScope(result.value); setExpires(result.value.maximumExpiresAt); key.current = crypto.randomUUID()
  }
  const submit = async () => {
    if (scope === null || !acknowledged || scope.revision !== run.revision || scope.manifestDigest !== run.manifestDigest) return
    const expiry = Date.parse(expires)
    if (!expires.endsWith('Z') || !Number.isFinite(expiry) || expiry <= Date.now() || expiry > Date.parse(scope.maximumExpiresAt)) {
      setExpiryError('Enter a future UTC expiry no later than the reviewed maximum.'); expiryInput.current?.focus(); return
    }
    const controller = operation.start(); if (controller === null) return
    const result = await issueReaderConsent(client, workspaceId, scope, expires, key.current, controller.signal)
    if (!operation.finish(controller)) return
    setScope(null); setAcknowledged(false)
    changed(result.kind === 'ok' && result.value.runnerId === scope.runnerId && result.value.manifestDigest === scope.manifestDigest ?
      'Operator decision stored. No reader has been started and no OS permission has been changed.' : failure(result))
  }
  return <div className="af-stack">
    <h3>Review one run and desktop</h3>
    {error !== null && <Notice tone="problem" heading="Consent needs attention" headingLevel={4} live><p>{error}</p></Notice>}
    <ResourceView resource={runners} what="available runner registrations">{(inventory) => <>
      {!inventory.complete && <p>Only part of the runner inventory was loaded. No runner is selected automatically.</p>}
      <FormField label="Runner" required>{({ id }) => <select id={id} value={runnerId} disabled={operation.busy} onChange={(event) => {
        setRunnerId(event.target.value); setScope(null); setAcknowledged(false); setError(null)
      }}><option value="">Choose a runner</option>{inventory.items.filter((runner) => runner.platform === 'darwin' && !runner.revoked).map((runner) =>
        <option key={runner.runnerId} value={runner.runnerId}>{runner.name} — {runner.runnerId}</option>)}</select>}</FormField>
    </>}</ResourceView>
    <Button busy={operation.busy} disabled={runnerId === ''} onClick={() => void review()}>Review startup scope</Button>
    {scope !== null && <form className="af-stack" onSubmit={(event) => { event.preventDefault(); void submit() }}>
      <p>{scope.effects.reader}, {scope.effects.sdk} {scope.effects.sdkVersion}: consent covers these effects only.</p>
      <ul>{scope.effects.effects.map((effect) => <li key={effect}>{EFFECTS[effect] ?? effect}</li>)}</ul>
      <p>Not authorized:</p><ul>{scope.effects.doesNotAuthorize.map((effect) => <li key={effect}>{EFFECTS[effect] ?? effect}</li>)}</ul>
      <dl><dt>Manifest digest</dt><dd><code>{scope.manifestDigest}</code></dd>
        <dt>Desktop identity</dt><dd><code>{scope.desktopSessionKey}</code></dd>
        <dt>Runner profile digest</dt><dd><code>{scope.runnerProfileDigest}</code></dd>
        <dt>Effects digest</dt><dd><code>{scope.effectsDigest}</code></dd></dl>
      <FormField label="Expiry (UTC)" required {...(expiryError === undefined ? {} : { error: expiryError })} hint={`Use an ISO timestamp ending in Z, no later than ${scope.maximumExpiresAt}.`}>
        {({ id, describedBy, invalid }) => <input ref={expiryInput} id={id} aria-describedby={describedBy} aria-invalid={invalid || undefined} required value={expires} disabled={operation.busy} onChange={(event) => { setExpires(event.target.value); setExpiryError(undefined); setAcknowledged(false) }} />}
      </FormField>
      <label className="af-consent-acknowledgement"><input type="checkbox" checked={acknowledged} disabled={operation.busy} onChange={(event) => setAcknowledged(event.target.checked)} />{' '}
        I have a dedicated desktop for this exact run and explicitly permit the listed reader restart and preference changes until this expiry.</label>
      <p>This is a one-time per-run grant, separate from RUN_EFFECTS approval. Revocation is permanent. This button stores consent; it does not launch the reader.</p>
      <Button type="submit" variant="primary" busy={operation.busy} disabled={!acknowledged}>Store reader startup consent</Button>
    </form>}
  </div>
}
