import { useId, useState } from 'react'
import type { JSX } from 'react'
import { useSession } from '../session/SessionProvider'
import { useResource } from '../api/useResource'
import { readManagedMember, changeManagedMember } from '../api/resources'
import type { ManagedMember, Member } from '../api/resources'
import { ResourceView } from '../components/ResourceView'
import { FormField } from '../components/FormField'
import { ErrorSummary } from '../components/ErrorSummary'
import type { FieldError } from '../components/ErrorSummary'
import { Button } from '../components/Button'
import { useAnnouncer } from '../a11y/Announcer'

const roles = ['OWNER', 'MAINTAINER', 'REVIEWER', 'VIEWER'] as const
const valid = (record: ManagedMember, userId: string): boolean => record !== null && typeof record === 'object' &&
  record.userId === userId && roles.includes(record.role) && typeof record.revoked === 'boolean' &&
  Number.isSafeInteger(record.revision) && record.revision >= 1

export const MemberManagement = ({ workspaceId, member, onSaved }: {
  readonly workspaceId: string; readonly member: Member; readonly onSaved: () => void
}): JSX.Element => {
  const { client, state, refresh } = useSession()
  const { announce } = useAnnouncer()
  const [receipt, setReceipt] = useState<string | null>(null)
  const resource = useResource((signal) => readManagedMember(client, workspaceId, member.userId, signal),
    [client, workspaceId, member.userId])
  return <section className="af-panel af-stack">
    <h3>Manage access for {member.email}</h3>
    <p>Role changes take effect on the next request. OWNER can manage people, infrastructure and
      workspace policy; MAINTAINER can configure projects and approve runs/patch application;
      REVIEWER can read evidence and record reviews; VIEWER can read evidence.</p>
    <p>The server protects the last active owner. Removing your own owner access can close this panel.
      These controls manage an existing membership; they do not invite a new account.</p>
    {receipt !== null && <p role="status">{receipt}</p>}
    <ResourceView resource={resource} what="the current membership revision">{record =>
      !valid(record, member.userId) ? <p role="alert">The membership record is malformed; no change is offered.</p> :
        <MemberForm key={`${member.userId}-${record.revision}`} workspaceId={workspaceId} record={record}
          onRead={resource.reload} onSaved={async (result) => {
            const message = `Membership ${result.revoked ? 'revoked' : `set to ${result.role}`} at revision ${result.revision}.`
            setReceipt(message); announce(message)
            onSaved()
            if (state.status === 'authenticated' && state.userId === member.userId) await refresh()
            else resource.reload()
          }} />
    }</ResourceView>
  </section>
}

const MemberForm = ({ workspaceId, record, onRead, onSaved }: {
  readonly workspaceId: string; readonly record: ManagedMember; readonly onRead: () => void
  readonly onSaved: (record: ManagedMember) => Promise<void>
}): JSX.Element => {
  const { client } = useSession()
  const id = useId()
  const [role, setRole] = useState<ManagedMember['role'] | 'REVOKE'>(record.role)
  const [reason, setReason] = useState(''), [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false), [locked, setLocked] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [errors, setErrors] = useState<readonly FieldError[]>([]), [attempt, setAttempt] = useState(0)
  const errorFor = (field: string) => {
    const error = errors.find(value => value.fieldId === `${id}-${field}`)
    return error === undefined ? {} : { error: error.message }
  }
  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault()
    if (busy || locked) return
    setAttempt(value => value + 1)
    const found: FieldError[] = []
    if ((!record.revoked && role === record.role) || (record.revoked && role === 'REVOKE'))
      found.push({ fieldId: `${id}-role`, message: 'Choose a change to the current membership.' })
    if (!reason.trim() || reason.length > 1000)
      found.push({ fieldId: `${id}-reason`, message: 'Enter a reason of 1–1000 characters.' })
    if (!confirmed) found.push({ fieldId: `${id}-confirm`, message: 'Confirm this access change.' })
    setErrors(found)
    if (found.length) return
    setBusy(true); setMessage(null)
    const desired = role === 'REVOKE' ? null : role
    const outcome = await changeManagedMember(client, workspaceId, record.userId,
      { role: desired, reason: reason.trim() }, record.revision)
    setBusy(false)
    if (outcome.kind === 'stale' || outcome.kind === 'unauthenticated') return
    if ((outcome.kind === 'ok' || outcome.kind === 'accepted') && valid(outcome.value, record.userId) &&
        outcome.value.revision === record.revision + 1 && outcome.value.revoked === (desired === null) &&
        outcome.value.role === (desired ?? record.role)) {
      setLocked(true); await onSaved(outcome.value); return
    }
    setLocked(true)
    setMessage(outcome.kind === 'problem' ? `${outcome.problem.detail} Read the current membership before another decision.` :
      'Whether access changed is unknown. Read the current membership; no write will be retried automatically.')
  }
  return <form className="af-stack" onSubmit={event => void submit(event)} noValidate>
    <p>Current state: {record.revoked ? 'REVOKED' : record.role}; revision {record.revision}.</p>
    <ErrorSummary errors={errors} submissionId={attempt} headingLevel={4} />
    {message !== null && <p role="alert">{message}</p>}
    <fieldset disabled={busy || locked} className="af-stack">
      <legend>Explicit access decision</legend>
      <FormField id={`${id}-role`} label="New membership role" {...errorFor('role')}>
        {({ id: fieldId, describedBy, invalid }) => <select id={fieldId} value={role}
          aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={event => { setRole(event.target.value as typeof role); setConfirmed(false) }}>
          {roles.map(value => <option key={value}>{value}</option>)}
          <option value="REVOKE">Revoke workspace access</option>
        </select>}
      </FormField>
      <FormField id={`${id}-reason`} label="Reason for access change" {...errorFor('reason')} required>
        {({ id: fieldId, describedBy, invalid }) => <input id={fieldId} value={reason} maxLength={1000}
          aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={event => { setReason(event.target.value); setConfirmed(false) }} />}
      </FormField>
      <FormField id={`${id}-confirm`} label="Confirm this access change" {...errorFor('confirm')}>
        {({ id: fieldId, describedBy, invalid }) => <input id={fieldId} type="checkbox" checked={confirmed}
          aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={event => setConfirmed(event.target.checked)} />}
      </FormField>
      <Button type="submit" variant={role === 'REVOKE' ? 'destructive' : 'primary'} busy={busy}>Save membership change</Button>
    </fieldset>
    <Button disabled={busy} onClick={onRead}>Read current membership and discard draft</Button>
  </form>
}
