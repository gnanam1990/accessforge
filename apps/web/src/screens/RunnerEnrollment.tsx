import { useEffect, useRef, useState } from 'react'
import type { JSX } from 'react'
import { Button } from '../components/Button'
import { FormField } from '../components/FormField'
import { membershipFor, useSession } from '../session/SessionProvider'
import { enrollObservedRunner, issueRunnerEnrollmentToken } from '../api/resources'
import { parseEnrollmentObservation } from './enrollmentObservation'
import type { EnrollmentObservation } from './enrollmentObservation'

export const RunnerEnrollment = ({ workspaceId, onEnrolled }: {
  readonly workspaceId: string; readonly onEnrolled: () => void
}): JSX.Element | null => {
  const { state } = useSession()
  if (membershipFor(state, workspaceId)?.role !== 'OWNER') return null
  return <EnrollmentForm key={workspaceId} workspaceId={workspaceId} onEnrolled={onEnrolled} />
}

const EnrollmentForm = ({ workspaceId, onEnrolled }: {
  readonly workspaceId: string; readonly onEnrolled: () => void
}): JSX.Element => {
  const { client } = useSession()
  const [raw, setRaw] = useState(''), [name, setName] = useState('')
  const [reviewed, setReviewed] = useState<EnrollmentObservation | null>(null)
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false)
  const [issuanceStarted, setIssuanceStarted] = useState(false)
  const [token, setToken] = useState<{ token: string; expiresAt: string } | null>(null)
  const [pending, setPending] = useState<{ body: Record<string, unknown>; key: string } | null>(null)
  const [message, setMessage] = useState<string | null>(null), [runnerId, setRunnerId] = useState<string | null>(null)
  const errorRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (message !== null) errorRef.current?.focus() }, [message])
  const locked = busy || issuanceStarted
  return <details><summary>Enroll an observed desktop</summary><section className="af-panel af-stack">
    <p>Owner only. On the dedicated desktop, run <code>accessforge-runner --enrollment-observation</code>.
      Paste its private output here. This website does not observe the desktop or start a reader.</p>
    <p>Draft and enrollment credentials stay in this page’s memory, not browser storage. Stay on this page until the outcome is known.</p>
    {message !== null && <p role="alert" tabIndex={-1} ref={errorRef}>{message}</p>}
    <fieldset disabled={locked}><legend>Review desktop identity</legend>
      <FormField label="Runner name" required>{({ id }) => <input id={id} value={name} onChange={(event) => {
        setName(event.target.value); setReviewed(null); setConfirmed(false)
      }} />}</FormField>
      <FormField label="Desktop enrollment observation JSON" required>{({ id }) => <textarea id={id} rows={8} value={raw} onChange={(event) => {
        setRaw(event.target.value); setReviewed(null); setConfirmed(false)
      }} />}</FormField>
      <Button onClick={() => {
        if (!name.trim()) { setMessage('Enter a runner name before reviewing.'); return }
        setBusy(true); setMessage(null)
        void parseEnrollmentObservation(raw).then(setReviewed).catch((error: unknown) => {
          setReviewed(null); setMessage(error instanceof Error ? error.message : 'Observation could not be checked.')
        }).finally(() => setBusy(false))
      }}>Review enrollment draft</Button>
    </fieldset>
    {reviewed && <>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(reviewed, null, 2)}</pre>
      <p>Local declarations only. Registration remains PREFLIGHT_REQUIRED; no execution is approved.</p>
      <label><input type="checkbox" disabled={locked} checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
        I reviewed this actual dedicated desktop’s current identity and authorize its registration.</label>
      {!issuanceStarted && <Button disabled={!confirmed || busy} onClick={() => {
        if (!confirmed || busy) return
        setIssuanceStarted(true); setBusy(true); setMessage(null)
        void issueRunnerEnrollmentToken(client, workspaceId).then((result) => {
          if (result.kind === 'ok' && result.value !== null && typeof result.value === 'object' &&
            typeof result.value.token === 'string' && /^[A-Za-z0-9_-]{32,128}$/.test(result.value.token) &&
            typeof result.value.expiresAt === 'string' && Date.parse(result.value.expiresAt) > Date.now()) {
            setToken(result.value)
          } else if (result.kind === 'problem' && result.problem.status < 500) {
            setIssuanceStarted(false); setMessage('Token issuance was refused. Check your current owner access and try again explicitly.')
          } else {
            setMessage('Token issuance outcome is unknown. Do not issue another token or reload to retry. Ask the operator to reconcile or confirm expiry first.')
          }
        }).finally(() => setBusy(false))
      }}>Issue single-use enrollment token</Button>}
    </>}
    {token && runnerId === null && <>
      <p>Token held privately in memory. Expires: {token.expiresAt}. It is not a run credential.</p>
      <Button busy={busy} onClick={() => {
        if (busy || reviewed === null) return
        if (pending === null && Date.parse(token.expiresAt) <= Date.now()) {
          setMessage('The token expired before enrollment. It cannot be used for a new request; confirm expiry before starting over.'); return
        }
        const operation = pending ?? { key: crypto.randomUUID(), body: { token: token.token, name: name.trim(), session: reviewed.session, profile: reviewed.profile } }
        setPending(operation); setBusy(true); setMessage(null)
        void enrollObservedRunner(client, workspaceId, operation.body, operation.key).then((result) => {
          if (result.kind === 'ok' && result.value !== null && typeof result.value === 'object' &&
            typeof result.value.runnerId === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(result.value.runnerId) &&
            result.value.profileDigest === reviewed.profileDigest && result.value.status === 'PREFLIGHT_REQUIRED') {
            setRunnerId(result.value.runnerId); setToken(null); setPending(null); setRaw(''); setReviewed(null); onEnrolled()
          } else {
            setMessage('Enrollment was not confirmed. Inputs and key remain locked. Retry sends the exact original request; do not create a new token or runner. Reconcile with the operator if retry is refused.')
          }
        }).finally(() => setBusy(false))
      }}>{pending === null ? 'Enroll reviewed desktop' : 'Retry same enrollment'}</Button>
    </>}
    {runnerId !== null && <p role="status">Enrollment receipt recorded: <code>{runnerId}</code>. Preflight is still required; the inventory shows current status.</p>}
  </section></details>
}
