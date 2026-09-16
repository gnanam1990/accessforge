import { useId, useState } from 'react'
import type { JSX } from 'react'
import { useSession } from '../session/SessionProvider'
import { useResource } from '../api/useResource'
import { createInvitation, listInvitations, readInvitation, revokeInvitation } from '../api/resources'
import type { MembershipInvitation, InvitationOffer } from '../api/resources'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { ResourceView } from '../components/ResourceView'
import { FormField } from '../components/FormField'
import { ErrorSummary } from '../components/ErrorSummary'
import type { FieldError } from '../components/ErrorSummary'
import { invitationQuery } from '../routes/invitationReference'

const roles = ['VIEWER', 'REVIEWER', 'MAINTAINER', 'OWNER'] as const
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const validSubject = (value: string): boolean => /^[1-9][0-9]{0,18}$/.test(value) && BigInt(value) < 2n ** 63n
const valid = (value: MembershipInvitation): boolean => value !== null && typeof value === 'object' &&
  typeof value.invitationId === 'string' && uuid.test(value.invitationId) &&
  typeof value.githubSubject === 'string' && validSubject(value.githubSubject) && roles.includes(value.role) &&
  typeof value.reason === 'string' && typeof value.createdBy === 'string' &&
  typeof value.expiresAt === 'string' && Number.isFinite(Date.parse(value.expiresAt)) &&
  typeof value.createdAt === 'string' && Number.isFinite(Date.parse(value.createdAt)) &&
  ((value.revision === 1 && ['PENDING', 'EXPIRED'].includes(value.state)) ||
   (value.revision === 2 && ['REVOKED', 'ACCEPTED'].includes(value.state)))

export const OwnerInvitations = ({ workspaceId }: { readonly workspaceId: string }): JSX.Element => {
  const { client } = useSession()
  const [after, setAfter] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [generation, setGeneration] = useState(0)
  const [receipt, setReceipt] = useState<string | null>(null)
  const inventory = useResource(signal => listInvitations(client, workspaceId, after, signal),
    [client, workspaceId, after])
  return <section className="af-panel af-stack">
    <h2>Workspace invitations</h2>
    <p>Prepare an offer for an exact GitHub numeric account ID, not an email or username.
      This does not send an email, create an account or grant access. Inspect a pending offer to
      share its link. The recipient must sign in with the invited GitHub account, read the offer
      and explicitly accept it. Their account must already be provisioned.</p>
    <p>OWNER manages people, policy and infrastructure. MAINTAINER configures projects and approves
      runs/patches. REVIEWER reads evidence and records reviews. VIEWER reads evidence.</p>
    {receipt !== null && <p role="status">{receipt}</p>}
    <OfferForm key={generation} workspaceId={workspaceId} onSaved={record => {
      const message = `Invitation ${record.invitationId} recorded as ${record.state}. No email was sent.`
      setReceipt(message); setSelected(record.invitationId)
      setGeneration(value => value + 1); inventory.reload()
    }} />
    <h3>Invitation history</h3>
    <Button onClick={() => { setAfter(null); inventory.reload() }}>Refresh invitation history</Button>
    <ResourceView resource={inventory} what="workspace invitation history">{page =>
      page === null || !Array.isArray(page.items) || !page.items.every(valid) ||
        (page.nextCursor !== null && (typeof page.nextCursor !== 'string' || !uuid.test(page.nextCursor))) ?
        <p role="alert">Invitation history is malformed; no row actions are offered.</p> : <>
        <DataTable<MembershipInvitation> caption="Invitation offers, not current workspace access"
          rows={page.items} rowKey={row => row.invitationId} columns={[
            { key: 'identity', header: 'GitHub numeric ID', isRowHeader: true, cell: row => row.githubSubject },
            { key: 'role', header: 'Offered role', cell: row => row.role },
            { key: 'state', header: 'State at last read', cell: row => row.state },
            { key: 'expires', header: 'Expires', cell: row => row.expiresAt },
            { key: 'manage', header: 'Manage', cell: row =>
              <Button onClick={() => setSelected(row.invitationId)}>Inspect {row.invitationId}</Button> },
          ]} />
        {page.nextCursor !== null && page.nextCursor !== after &&
          <Button onClick={() => setAfter(page.nextCursor)}>Next invitation page</Button>}
        {after !== null && <Button onClick={() => setAfter(null)}>First invitation page</Button>}
      </>
    }</ResourceView>
    {selected !== null && <InvitationDetail key={selected} workspaceId={workspaceId}
      invitationId={selected} onChanged={inventory.reload} />}
  </section>
}

const OfferForm = ({ workspaceId, onSaved }: {
  readonly workspaceId: string; readonly onSaved: (record: MembershipInvitation) => void
}): JSX.Element => {
  const { client } = useSession()
  const id = useId(), [invitationId] = useState(() => crypto.randomUUID())
  const [subject, setSubject] = useState(''), [role, setRole] = useState<InvitationOffer['role']>('VIEWER')
  const [minutes, setMinutes] = useState('60'), [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false)
  const [locked, setLocked] = useState(false), [message, setMessage] = useState<string | null>(null)
  const [errors, setErrors] = useState<readonly FieldError[]>([]), [attempt, setAttempt] = useState(0)
  const errorFor = (name: string) => {
    const found = errors.find(error => error.fieldId === `${id}-${name}`)
    return found === undefined ? {} : { error: found.message }
  }
  const matches = (record: MembershipInvitation): boolean => valid(record) && record.invitationId === invitationId &&
    record.githubSubject === subject && record.role === role && record.reason === reason.trim()
  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault()
    if (busy || locked) return
    setAttempt(value => value + 1)
    const found: FieldError[] = []
    if (!validSubject(subject)) found.push({ fieldId: `${id}-subject`, message: 'Enter the exact positive numeric GitHub ID, not a username or email.' })
    const ttl = Number(minutes)
    if (!/^[0-9]+$/.test(minutes) || !Number.isSafeInteger(ttl) || ttl < 1 || ttl > 10080)
      found.push({ fieldId: `${id}-minutes`, message: 'Choose 1–10080 whole minutes (up to seven days).' })
    if (!reason.trim() || reason.length > 1000) found.push({ fieldId: `${id}-reason`, message: 'Enter a reason of 1–1000 characters.' })
    if (!confirmed) found.push({ fieldId: `${id}-confirm`, message: 'Confirm the numeric identity and offered role.' })
    setErrors(found)
    if (found.length) return
    setBusy(true); setMessage(null)
    const outcome = await createInvitation(client, workspaceId, invitationId,
      { githubSubject: subject, role, ttlSeconds: ttl * 60, reason: reason.trim() })
    setBusy(false); setLocked(true)
    if (outcome.kind === 'ok' && matches(outcome.value) && outcome.value.revision === 1 && outcome.value.state === 'PENDING') {
      onSaved(outcome.value); return
    }
    setMessage('The invitation save is not confirmed. Read this exact invitation before another decision; no write is retried automatically.')
  }
  const recover = async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    const outcome = await readInvitation(client, workspaceId, invitationId)
    setBusy(false)
    if (outcome.kind === 'ok' && matches(outcome.value)) { onSaved(outcome.value); return }
    if (outcome.kind === 'problem' && outcome.problem.status === 404) {
      setLocked(false); setConfirmed(false)
      setMessage('No invitation exists at this ID. Review and confirm the draft before explicitly saving again.'); return
    }
    setMessage('The current invitation could not be confirmed. The draft remains locked; try the read again after checking your session.')
  }
  return <form className="af-stack" onSubmit={event => void submit(event)} noValidate>
    <h3>Prepare an invitation offer</h3>
    <ErrorSummary errors={errors} submissionId={attempt} headingLevel={4} />
    {message !== null && <p role="alert">{message}</p>}
    <fieldset disabled={busy || locked} className="af-stack"><legend>Identity and scope</legend>
      <FormField id={`${id}-subject`} label="GitHub numeric account ID" {...errorFor('subject')} required>
        {({ id: fieldId, describedBy, invalid }) => <input id={fieldId} value={subject} inputMode="numeric"
          aria-describedby={describedBy} aria-invalid={invalid || undefined} maxLength={19}
          onChange={event => { setSubject(event.target.value); setConfirmed(false) }} />}
      </FormField>
      <FormField id={`${id}-role`} label="Invitation role">
        {({ id: fieldId }) => <select id={fieldId} value={role} onChange={event => {
          setRole(event.target.value as typeof role); setConfirmed(false)
        }}>{roles.map(value => <option key={value}>{value}</option>)}</select>}
      </FormField>
      <FormField id={`${id}-minutes`} label="Invitation lifetime in minutes" {...errorFor('minutes')} required>
        {({ id: fieldId, describedBy, invalid }) => <input id={fieldId} value={minutes} inputMode="numeric"
          aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={event => { setMinutes(event.target.value); setConfirmed(false) }} />}
      </FormField>
      <FormField id={`${id}-reason`} label="Reason for invitation" {...errorFor('reason')} required>
        {({ id: fieldId, describedBy, invalid }) => <input id={fieldId} value={reason} maxLength={1000}
          aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={event => { setReason(event.target.value); setConfirmed(false) }} />}
      </FormField>
      <FormField id={`${id}-confirm`} label="Confirm numeric identity and offered role" {...errorFor('confirm')}>
        {({ id: fieldId, describedBy, invalid }) => <input id={fieldId} type="checkbox" checked={confirmed}
          aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={event => setConfirmed(event.target.checked)} />}
      </FormField>
      <Button type="submit" variant="primary" busy={busy}>Create invitation offer</Button>
    </fieldset>
    {locked && <><p>Recovery reference: <code>{invitationId}</code></p>
      <Button busy={busy} onClick={() => void recover()}>Read invitation save outcome</Button></>}
  </form>
}

const InvitationDetail = ({ workspaceId, invitationId, onChanged }: {
  readonly workspaceId: string; readonly invitationId: string; readonly onChanged: () => void
}): JSX.Element => {
  const { client } = useSession()
  const resource = useResource(signal => readInvitation(client, workspaceId, invitationId, signal),
    [client, workspaceId, invitationId])
  return <section className="af-stack"><h3>Selected invitation</h3>
    <ResourceView resource={resource} what="the selected invitation">{record =>
      !valid(record) || record.invitationId !== invitationId ? <p role="alert">The invitation record is malformed; no change is offered.</p> :
      <RevokeOffer key={`${record.invitationId}-${record.revision}`} workspaceId={workspaceId} record={record}
        onRead={resource.reload} onChanged={() => { resource.reload(); onChanged() }} />
    }</ResourceView>
  </section>
}

const RevokeOffer = ({ workspaceId, record, onRead, onChanged }: {
  readonly workspaceId: string; readonly record: MembershipInvitation
  readonly onRead: () => void; readonly onChanged: () => void
}): JSX.Element => {
  const { client } = useSession()
  const id = useId()
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false)
  const [locked, setLocked] = useState(false), [message, setMessage] = useState<string | null>(null)
  const revoke = async (): Promise<void> => {
    if (!confirmed || busy || locked) return
    setBusy(true)
    const outcome = await revokeInvitation(client, workspaceId, record.invitationId, record.revision)
    setBusy(false); setLocked(true)
    if (outcome.kind === 'ok' && valid(outcome.value) && outcome.value.invitationId === record.invitationId &&
        outcome.value.revision === record.revision + 1 && outcome.value.state === 'REVOKED') { onChanged(); return }
    setMessage('Revocation is not confirmed. Read the current invitation before another decision; no automatic retry.')
  }
  return <div className="af-stack">
    <p>GitHub ID {record.githubSubject}; {record.role}; state {record.state}; revision {record.revision}.</p>
    <p>Reason: {record.reason}. Expires: {record.expiresAt}.</p>
    {record.state === 'PENDING' && uuid.test(workspaceId) && <FormField
      label="Invitation link" hint="Copy and share with the invited person. This is a reference, not an access token; sharing it does not send an email or grant membership.">
      {({ id: fieldId, describedBy }) => <input id={fieldId} aria-describedby={describedBy}
        readOnly value={`${window.location.origin}/workspaces?${invitationQuery({
          workspaceId: workspaceId.toLowerCase(), invitationId: record.invitationId.toLowerCase(),
        })}`} onFocus={event => event.currentTarget.select()} />}
    </FormField>}
    <p>Revoking an offer does not remove an accepted membership. Use membership controls for existing access.</p>
    {message !== null && <p role="alert">{message}</p>}
    {['PENDING', 'EXPIRED'].includes(record.state) && <fieldset disabled={busy || locked}>
      <legend>Revoke this invitation offer</legend>
      <label htmlFor={id}><input id={id} type="checkbox" checked={confirmed}
        onChange={event => setConfirmed(event.target.checked)} /> Confirm invitation revocation</label>
      <Button variant="destructive" disabled={!confirmed} busy={busy} onClick={() => void revoke()}>Revoke invitation offer</Button>
    </fieldset>}
    <Button busy={busy} onClick={onRead}>Read current invitation</Button>
  </div>
}
