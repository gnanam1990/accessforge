/**
 * Tester Desk · tester inbox: offers that match this tester's enrollment, and their assignments.
 *
 * **Consent is read before the claim.** The claim button stays off until the consent checkbox is
 * ticked — "I read the terms" is an action the tester takes, not a default the UI smuggles in.
 *
 * **Offer terms are the pinned brief's terms.** Fee, release, environment and steps come from the
 * offer itself; the tester sees exactly what accepting binds them to.
 */

import { useState } from 'react'
import type { JSX } from 'react'
import { Link } from 'react-router-dom'

import { RouteHeading } from '../a11y/RouteHeading'
import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { claimOffer, listAssignments, listOffers } from '../api/testerDesk'
import type { Assignment, Offer } from '../api/testerDesk'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { workspacePath } from '../routes/routeMap'

export const TesterInboxScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const offers = useResource((s) => listOffers(client, workspaceId, s), [client, workspaceId])
  const assignments = useResource((s) => listAssignments(client, workspaceId, s), [client, workspaceId])
  const [consent, setConsent] = useState<Record<string, boolean>>({})
  const [claiming, setClaiming] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const claim = async (offer: Offer): Promise<void> => {
    setClaiming(offer.commissionId); setError(null)
    const outcome = await claimOffer(
      client, workspaceId, offer.commissionId,
      offer.brief.consentTermsVersion, crypto.randomUUID(),
    )
    if (outcome.kind === 'problem') setError(outcome.problem.detail ?? outcome.problem.title)
    else { announce('Assignment claimed'); assignments.reload(); offers.reload() }
    setClaiming(null)
  }

  return (
    <div className="af-stack">
      <header><RouteHeading>My test work</RouteHeading>
        <p className="af-secondary">Offers matching your declared browser and assistive technology, and the assignments you hold.</p>
      </header>
      {error !== null && <Notice tone="problem" heading="Claim refused" headingLevel={2}><p>{error}</p></Notice>}

      <section aria-labelledby="td-offers">
        <h2 id="td-offers">Open offers</h2>
        <ResourceView resource={offers} what="offers">{(value) => (
          value.items.length === 0 ? <p className="af-secondary">No offers right now.</p>
            : value.items.map((o) => (
              <article key={o.commissionId} className="af-card af-stack" aria-label={`Offer ${o.reference}`}>
                <header><h3>{o.reference} · {o.brief.releaseLabel}</h3>
                  <p className="af-secondary">
                    {(o.brief.feeMinor / 100).toFixed(2)} {o.brief.currency} ·{' '}
                    {o.brief.environment.assistiveTechnology} on {o.brief.environment.browserName}{' '}
                    {o.brief.environment.browserVersion} ·{' '}
                    {o.offerExpiresAt !== null ? `expires ${o.offerExpiresAt.slice(0, 16)}` : 'no expiry'}
                  </p>
                </header>
                <ol>{o.brief.taskSteps.map((s) => <li key={s.stepId}>{s.title}</li>)}</ol>
                {o.consentTerms !== undefined && (
                  <details><summary>Consent terms (v{o.brief.consentTermsVersion})</summary>
                    <p>{o.consentTerms.body}</p>
                  </details>
                )}
                <label>
                  <input type="checkbox"
                    checked={consent[o.commissionId] === true}
                    onChange={(e) => setConsent({ ...consent, [o.commissionId]: e.target.checked })} />
                  {' '}I read and accept the consent terms for this assignment
                </label>
                <div>
                  <Button variant="primary"
                    busy={claiming === o.commissionId}
                    disabled={consent[o.commissionId] !== true}
                    onClick={() => void claim(o)}>Claim this assignment</Button>
                </div>
              </article>
            ))
        )}</ResourceView>
      </section>

      <section aria-labelledby="td-my">
        <h2 id="td-my">My assignments</h2>
        <ResourceView resource={assignments} what="your assignments">{(value) => (
          value.items.length === 0 ? <p className="af-secondary">No assignments yet.</p> : (
            <DataTable<Assignment>
              caption="Your assignments"
              rows={value.items}
              rowKey={(a) => a.assignmentId}
              columns={[
                {
                  key: 'id', header: 'Assignment', isRowHeader: true,
                  cell: (a) => (
                    <Link to={workspacePath(workspaceId, `tester-desk/tester/assignments/${a.assignmentId}`)}>
                      <code>{a.assignmentId.slice(0, 8)}</code>
                    </Link>
                  ),
                },
                { key: 'rel', header: 'Release', cell: (a) => a.brief?.releaseLabel ?? '—' },
                {
                  key: 'st', header: 'Status',
                  cell: (a) => <StatusBadge tone={a.status === 'COMPLETED' ? 'pass' : 'progress'} kind="Assignment status">{a.status}</StatusBadge>,
                },
                { key: 'exp', header: 'Expires', cell: (a) => a.expiresAt?.slice(0, 10) ?? '—' },
              ]}
            />
          )
        )}</ResourceView>
      </section>
    </div>
  )
}
