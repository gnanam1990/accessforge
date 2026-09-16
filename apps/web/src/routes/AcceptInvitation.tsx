import { useRef, useState } from 'react'
import { Button } from '../components/Button'
import { FormField } from '../components/FormField'
import { useSession } from '../session/SessionProvider'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const ROLES = ['OWNER', 'ENGINEER', 'REVIEWER', 'VIEWER']
type Offer = {
  workspaceId: string; invitationId: string; workspaceName: string; role: string
  reason: string; expiresAt: string; state: string; revision: number
}
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object'

export function AcceptInvitation() {
  const { client, state, refresh } = useSession()
  const [workspace, setWorkspace] = useState('')
  const [invitation, setInvitation] = useState('')
  const [offer, setOffer] = useState<Offer | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [locked, setLocked] = useState(false)
  const [message, setMessage] = useState('')
  const inFlight = useRef(false)
  const path = `/v1/invitation-offers/${encodeURIComponent(workspace)}/${encodeURIComponent(invitation)}`

  async function read() {
    if (inFlight.current || !UUID.test(workspace) || !UUID.test(invitation)) return
    inFlight.current = true; setBusy(true); setOffer(null); setConfirmed(false)
    setMessage('Reading the invitation…')
    const result = await client.request<unknown>(path)
    inFlight.current = false; setBusy(false)
    if (result.kind === 'cancelled' || result.kind === 'stale') return
    if (result.kind === 'unauthenticated') { await refresh(); return }
    const value = result.kind === 'ok' ? result.value : null
    if (object(value) && value.workspaceId === workspace && value.invitationId === invitation &&
        typeof value.workspaceName === 'string' && typeof value.reason === 'string' &&
        typeof value.role === 'string' && ROLES.includes(value.role) &&
        typeof value.expiresAt === 'string' && Number.isFinite(Date.parse(value.expiresAt)) &&
        ['PENDING', 'ACCEPTED', 'REVOKED', 'EXPIRED'].includes(String(value.state)) &&
        Number.isSafeInteger(value.revision) &&
        ((value.state === 'PENDING' && value.revision === 1) ||
         (value.state === 'EXPIRED' && value.revision === 1) ||
         (['ACCEPTED', 'REVOKED'].includes(String(value.state)) && value.revision === 2))) {
      setOffer(value as Offer); setLocked(false)
      setMessage(value.state === 'PENDING' ? 'Review the offer before accepting.' :
        'This offer cannot be accepted. Refresh workspaces to check your current access.')
    } else {
      setMessage('Invitation could not be verified. Check both references with the owner, then read again. A verified GitHub login is required.')
    }
  }

  async function accept() {
    if (inFlight.current || locked || !confirmed || offer?.state !== 'PENDING' || state.status !== 'authenticated') return
    inFlight.current = true; setBusy(true); setLocked(true); setConfirmed(false)
    setMessage('Accepting the invitation…')
    const result = await client.request<unknown>(`${path}/accept`, {
      method: 'POST', body: { accept: true }, ifMatch: offer.revision,
    })
    inFlight.current = false; setBusy(false)
    if (result.kind === 'cancelled' || result.kind === 'stale') return
    if (result.kind === 'unauthenticated') { await refresh(); return }
    const value = result.kind === 'ok' && result.status === 200 ? result.value : null
    if (object(value) && value.workspaceId === workspace && value.invitationId === invitation &&
        value.sessionRotated === true && object(value.membership) &&
        value.membership.userId === state.userId && value.membership.role === offer.role &&
        value.membership.revision === 1 && value.membership.revoked === false) {
      setOffer(null)
      setMessage('Invitation accepted. Refreshing your current workspace access…')
      client.invalidateInFlight()
      await refresh()
    } else {
      setMessage('Acceptance is not confirmed. Do not submit again. Read this invitation again; if your session ended, sign in with GitHub and check current workspace access.')
    }
  }

  return <section className="af-stack" aria-labelledby="accept-invitation-heading">
    <h2 id="accept-invitation-heading">Have a workspace invitation?</h2>
    <p className="af-secondary">Ask the owner for the workspace and invitation IDs. These are references, not passwords. Only the invited GitHub account can accept. Your account must already be provisioned; this does not create a new account.</p>
    <form className="af-stack" onSubmit={(event) => { event.preventDefault(); void read() }}>
      <fieldset disabled={busy || locked} className="af-stack">
        <legend>Invitation references</legend>
        {([['Workspace ID', workspace, setWorkspace], ['Invitation ID', invitation, setInvitation]] as const).map(([label, value, update]) =>
          <FormField key={label} label={label} required hint="Paste the full UUID supplied by the owner.">
            {({ id, describedBy }) => <input id={id} aria-describedby={describedBy} required pattern={UUID.source} value={value}
              onChange={(event) => { update(event.target.value.trim().toLowerCase()); setOffer(null); setConfirmed(false); setMessage('') }} />}
          </FormField>)}
      </fieldset>
      <Button type="submit" busy={busy} disabled={!UUID.test(workspace) || !UUID.test(invitation)}>Read invitation</Button>
    </form>
    {offer && <div className="af-stack">
      <h3>{offer.workspaceName}</h3>
      <p>Offered role: {offer.role}. State at last read: {offer.state}.</p>
      <p>{offer.reason}</p><p>Expires: {offer.expiresAt}</p>
      {offer.state === 'PENDING' && <>
        <label><input type="checkbox" checked={confirmed} disabled={busy || locked}
          onChange={(event) => setConfirmed(event.target.checked)} /> I agree to join this workspace with the displayed role.</label>
        <Button disabled={!confirmed || locked} busy={busy} onClick={() => void accept()}>Accept invitation</Button>
      </>}
    </div>}
    {message && <p role="status">{message}</p>}
    <Button disabled={busy} onClick={() => void refresh()}>Refresh workspaces</Button>
  </section>
}
