import { useId, useState } from 'react'
import type { JSX } from 'react'
import { Button } from '../components/Button'
import { ErrorSummary } from '../components/ErrorSummary'
import type { FieldError } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { configureRetention } from '../api/resources'
import type { RetentionPolicy } from '../api/resources'
import { useSession } from '../session/SessionProvider'
import { useAnnouncer } from '../a11y/Announcer'

/** A policy change is an explicit decision, never a deletion or collection consent. */
export const RetentionPolicyForm = ({ workspaceId, policy, onReload }: {
  readonly workspaceId: string
  readonly policy: RetentionPolicy
  readonly onReload: () => void
}): JSX.Element => {
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const id = useId()
  const [entries, setEntries] = useState(() => policy.classes.map((entry) => ({
    evidenceClass: entry.evidenceClass,
    days: String(entry.retainDays),
    consentRequired: entry.consentRequired,
  })))
  const [confirmed, setConfirmed] = useState(false)
  const [errors, setErrors] = useState<readonly FieldError[]>([])
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const [locked, setLocked] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault()
    if (busy || locked) return
    setAttempt((value) => value + 1)
    const found: FieldError[] = []
    entries.forEach((entry, index) => {
      if (!/^\d+$/.test(entry.days) || !Number.isSafeInteger(Number(entry.days)) ||
          Number(entry.days) > 36500) {
        found.push({ fieldId: `${id}-days-${index}`,
          message: `${entry.evidenceClass}: enter whole days from 0 to 36500.` })
      }
    })
    if (!confirmed) found.push({ fieldId: `${id}-confirm`,
      message: 'Confirm the evidence and privacy consequences before saving.' })
    setErrors(found)
    if (found.length > 0) return
    setBusy(true)
    setMessage(null)
    const outcome = await configureRetention(client, workspaceId, entries.map((entry) => ({
      evidenceClass: entry.evidenceClass, retainDays: Number(entry.days),
      consentRequired: entry.consentRequired,
    })), policy.revision)
    setBusy(false)
    if (outcome.kind === 'stale' || outcome.kind === 'unauthenticated') return
    if ((outcome.kind === 'ok' || outcome.kind === 'accepted') &&
        outcome.value.revision === policy.revision + 1) {
      announce(`Retention policy saved as revision ${outcome.value.revision}.`)
      onReload()
      return
    }
    if (outcome.kind === 'problem' && outcome.problem.status < 500 &&
        outcome.problem.code !== 'STALE_REVISION') {
      setMessage(outcome.problem.detail)
      return
    }
    setLocked(true)
    setMessage(outcome.kind === 'problem' && outcome.problem.code === 'STALE_REVISION'
      ? 'The policy changed while you were deciding. Read the current policy and review a new draft.'
      : 'Whether this policy was saved is unknown. Read the current policy before making another decision; no write will be retried automatically.')
  }
  return <details className="af-panel">
    <summary>Change retention policy</summary>
    <form className="af-stack" onSubmit={(event) => void submit(event)} noValidate>
      <h3>Review retention revision {policy.revision}</h3>
      <p>Shorter periods can make existing evidence eligible for deletion by retention processing.
        Deleted evidence cannot be recovered here. Longer periods cannot restore it. Zero keeps
        nothing beyond the run. This form supports up to 36500 days (100 years).</p>
      <p>Changing a consent requirement is not consent to collect anything. Copies already exported
        and backups are not erased by this policy. Completeness consequences above cannot be changed.</p>
      <ErrorSummary errors={errors} submissionId={attempt} headingLevel={4} />
      {message !== null && <Notice tone="warning" heading="Retention needs attention" headingLevel={4} live>
        <p>{message}</p>
      </Notice>}
      <fieldset disabled={busy || locked} className="af-stack">
        <legend>Every evidence class</legend>
        {entries.map((entry, index) => <div className="af-stack" key={entry.evidenceClass}>
          <FormField id={`${id}-days-${index}`} label={`${entry.evidenceClass} retention days`}
            {...(errors.find((error) => error.fieldId === `${id}-days-${index}`) === undefined ? {} :
              { error: errors.find((error) => error.fieldId === `${id}-days-${index}`)!.message })} required>
            {({ id: fieldId, describedBy, invalid }) => <input id={fieldId} type="number"
              min={0} max={36500} step={1} value={entry.days}
              aria-describedby={describedBy} aria-invalid={invalid || undefined}
              onChange={(event) => { setConfirmed(false); setEntries((current) => current.map((item, i) =>
                i === index ? { ...item, days: event.target.value } : item)) }} />}
          </FormField>
          <label><input type="checkbox" checked={entry.consentRequired}
            onChange={(event) => { setConfirmed(false); setEntries((current) => current.map((item, i) =>
              i === index ? { ...item, consentRequired: event.target.checked } : item)) }} />
            Require consent for {entry.evidenceClass}</label>
        </div>)}
        <FormField id={`${id}-confirm`} label="Confirm policy consequences"
          {...(errors.find((error) => error.fieldId === `${id}-confirm`) === undefined ? {} :
            { error: 'Confirm the evidence and privacy consequences before saving.' })}>
          {({ id: fieldId, describedBy, invalid }) => <input id={fieldId} type="checkbox"
            checked={confirmed} aria-describedby={describedBy} aria-invalid={invalid || undefined}
            onChange={(event) => setConfirmed(event.target.checked)} />}
        </FormField>
        <Button type="submit" variant="primary" busy={busy}>Save retention policy</Button>
      </fieldset>
      <Button disabled={busy} onClick={onReload}>Read current policy and discard draft</Button>
    </form>
  </details>
}
