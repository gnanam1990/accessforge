/**
 * Tester Desk · agency console: commissions, review queue and payables in one surface.
 *
 * Three money-relevant truths this screen refuses to blur:
 *
 * **A commission's status is its lifecycle, not its quality.** OPEN/ASSIGNED/IN_REVIEW/ACCEPTED
 * are shown verbatim; nothing collapses them into a traffic light.
 *
 * **Review binds to a digest.** The decision form sends the submission hash the reviewer saw —
 * the server conflicts it if the tester resealed since (T-008), so this screen can never approve
 * evidence that no longer exists.
 *
 * **A payable is a lifecycle too.** INTENT → DISPATCHED/UNKNOWN → SETTLED; a stuck payable is
 * shown as stuck ("dispatch pending"), never as a silent green (T-016).
 */

import { useState } from 'react'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import {
  decide, generateReport, listCommissions, listPayables, publishCommission,
  reviewAssignment, reviewQueue,
} from '../api/testerDesk'
import type { Commission, Payable, Submission } from '../api/testerDesk'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'

type Tone = 'neutral' | 'progress' | 'pass' | 'fail' | 'inconclusive' | 'interrupted'

const COMMISSION_TONE: Record<string, Tone> = {
  DRAFT: 'neutral', OPEN: 'progress', ASSIGNED: 'progress', SUBMITTED: 'progress',
  IN_REVIEW: 'progress', ACCEPTED: 'pass', NEEDS_REVISION: 'inconclusive',
  EXPIRED: 'interrupted', CANCELLED: 'interrupted',
}

const PAYABLE_TONE: Record<string, Tone> = {
  INTENT: 'neutral', DISPATCHED: 'progress', UNKNOWN: 'interrupted',
  SETTLED: 'pass', CANCELLED: 'interrupted',
}

const RESULT_TONE: Record<string, Tone> = {
  PASSED: 'pass', FAILED: 'fail', UNREACHABLE: 'fail', INCONCLUSIVE: 'inconclusive',
}

const money = (minor: number, currency: string): string =>
  `${(minor / 100).toFixed(2)} ${currency}`

export const TesterDeskScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client } = useSession()
  const commissions = useResource((s) => listCommissions(client, workspaceId, s), [client, workspaceId])
  const queue = useResource((s) => reviewQueue(client, workspaceId, s), [client, workspaceId])
  const payables = useResource((s) => listPayables(client, workspaceId, s), [client, workspaceId])
  const { announce } = useAnnouncer()
  const [publishing, setPublishing] = useState<string | null>(null)
  const [reporting, setReporting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const publish = async (commissionId: string): Promise<void> => {
    setPublishing(commissionId); setError(null)
    const outcome = await publishCommission(client, workspaceId, commissionId, crypto.randomUUID())
    if (outcome.kind === 'problem') setError(outcome.problem.detail ?? outcome.problem.title)
    else { announce('Commission published'); commissions.reload() }
    setPublishing(null)
  }

  const makeReport = async (assignmentId: string): Promise<void> => {
    setReporting(assignmentId); setError(null)
    const outcome = await generateReport(client, workspaceId, assignmentId, crypto.randomUUID())
    if (outcome.kind === 'problem') setError(outcome.problem.detail ?? outcome.problem.title)
    else announce('Client report generated')
    setReporting(null)
  }

  return (
    <div className="af-stack">
      <header><RouteHeading>Tester Desk</RouteHeading>
        <p className="af-secondary">Commissions, review queue and compensation for independent accessibility retests.</p>
      </header>
      {error !== null && <Notice tone="problem" heading="Action refused" headingLevel={2}><p>{error}</p></Notice>}

      <section aria-labelledby="td-commissions">
        <h2 id="td-commissions">Commissions</h2>
        <ResourceView resource={commissions} what="commissions">{(value) => (
          value.items.length === 0 ? <p className="af-secondary">No commissions yet.</p> : (
            <DataTable<Commission>
              caption="Test commissions"
              rows={value.items}
              rowKey={(c) => c.commissionId}
              columns={[
                { key: 'ref', header: 'Reference', isRowHeader: true, cell: (c) => c.reference },
                { key: 'rel', header: 'Release', cell: (c) => c.brief?.releaseLabel ?? '—' },
                { key: 'fee', header: 'Fee', cell: (c) => c.brief ? money(c.brief.feeMinor, c.brief.currency) : '—' },
                {
                  key: 'st', header: 'Status',
                  cell: (c) => <StatusBadge tone={COMMISSION_TONE[c.status] ?? 'neutral'} kind="Commission status">{c.status}</StatusBadge>,
                },
                {
                  key: 'act', header: 'Actions',
                  cell: (c) => c.status === 'DRAFT'
                    ? <Button variant="primary" busy={publishing === c.commissionId} onClick={() => void publish(c.commissionId)}>Publish</Button>
                    : null,
                },
              ]}
            />
          )
        )}</ResourceView>
      </section>

      <section aria-labelledby="td-review">
        <h2 id="td-review">Review queue</h2>
        <ResourceView resource={queue} what="review queue">{(value) => (
          value.items.length === 0 ? <p className="af-secondary">Nothing awaits review.</p>
            : value.items.map((item) => (
              <ReviewCard key={item.assignmentId}
                assignmentId={item.assignmentId} reference={item.reference}
                onDecided={() => { queue.reload(); payables.reload(); commissions.reload() }}
                onReport={(aid) => void makeReport(aid)} reporting={reporting === item.assignmentId}
              />
            ))
        )}</ResourceView>
      </section>

      <section aria-labelledby="td-payables">
        <h2 id="td-payables">Payables</h2>
        <ResourceView resource={payables} what="payables">{(value) => (
          value.items.length === 0 ? <p className="af-secondary">No payables.</p> : (
            <DataTable<Payable>
              caption="Compensation payables"
              rows={value.items}
              rowKey={(p) => p.payableId}
              columns={[
                { key: 'a', header: 'Assignment', isRowHeader: true, cell: (p) => <code>{p.assignmentId.slice(0, 8)}</code> },
                { key: 'amt', header: 'Amount', cell: (p) => money(p.amountMinor, p.currency) },
                {
                  key: 'st', header: 'Status',
                  cell: (p) => (
                    <StatusBadge tone={PAYABLE_TONE[p.status] ?? 'neutral'} kind="Payable status">
                      {p.status === 'INTENT' ? 'PENDING DISPATCH' : p.status}
                    </StatusBadge>
                  ),
                },
                { key: 'pr', header: 'Provider ref', cell: (p) => p.providerReference ?? '—' },
                { key: 'cr', header: 'Created', cell: (p) => p.createdAt.slice(0, 10) },
              ]}
            />
          )
        )}</ResourceView>
      </section>
    </div>
  )
}

const ReviewCard = ({ assignmentId, reference, onDecided, onReport, reporting }: {
  assignmentId: string; reference: string
  onDecided: () => void; onReport: (assignmentId: string) => void; reporting: boolean
}): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const detail = useResource(
    (s) => reviewAssignment(client, workspaceId, assignmentId, s),
    [client, workspaceId, assignmentId],
  )
  const [justification, setJustification] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submission = detail.state.kind === 'ready'
    ? detail.state.value.submissions[detail.state.value.submissions.length - 1] ?? null
    : null

  const send = async (decision: 'ACCEPTED' | 'NEEDS_REVISION'): Promise<void> => {
    if (submission === null || justification.trim() === '') return
    setBusy(true); setError(null)
    const outcome = await decide(
      client, workspaceId, assignmentId,
      submission.submissionHash, decision, justification.trim(), crypto.randomUUID(),
    )
    setBusy(false)
    if (outcome.kind === 'problem') setError(outcome.problem.detail ?? outcome.problem.title)
    else { announce(`Review recorded: ${decision}`); onDecided() }
  }

  return (
    <article className="af-card af-stack" aria-label={`Review ${reference}`}>
      <header><h3>{reference}</h3>
        {submission !== null && <p className="af-secondary">
          sealed revision {submission.revision} · task result{' '}
          <StatusBadge tone={RESULT_TONE[submission.taskResult] ?? 'neutral'} kind="Task result">
            {submission.taskResult}
          </StatusBadge>
        </p>}
      </header>
      <ResourceView resource={detail} what="submission detail">{() => null}</ResourceView>
      {error !== null && <Notice tone="problem" heading="Decision refused" headingLevel={4}><p>{error}</p></Notice>}
      {submission !== null && (
        <>
          <FormField id={`j-${assignmentId}`} label="Justification" required>
            {({ id, describedBy, invalid }) => (
              <input id={id} value={justification} aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(e) => setJustification(e.target.value)}
                placeholder="required — what the evidence shows" />
            )}
          </FormField>
          <div className="af-row-actions">
            <Button variant="primary" busy={busy} disabled={justification.trim() === ''}
              onClick={() => void send('ACCEPTED')}>Accept evidence</Button>
            <Button busy={busy} disabled={justification.trim() === ''}
              onClick={() => void send('NEEDS_REVISION')}>Request revision</Button>
          </div>
        </>
      )}
      <div className="af-row-actions">
        <Button busy={reporting} onClick={() => onReport(assignmentId)}>Generate client report</Button>
      </div>
    </article>
  )
}
