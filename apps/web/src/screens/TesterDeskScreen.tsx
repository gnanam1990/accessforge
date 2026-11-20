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
  decide, generateReport, listCommissions, listPayables, listReportable, mutationProblem,
  publishCommission, reviewAssignment, reviewAttachmentContent, reviewQueue,
} from '../api/testerDesk'
import type { Commission, Observation, Payable, Submission } from '../api/testerDesk'
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
  // An accepted assignment leaves the review queue, so the follow-up a decision enables —
  // generating the client report — has to live somewhere the card's removal cannot take it.
  // `reports/ready` is the server's own list of those assignments; a caller without
  // COMMISSION_AUTHOR gets a refusal there, which the section below honours by not existing.
  const reportable = useResource((s) => listReportable(client, workspaceId, s), [client, workspaceId])

  const publish = async (commissionId: string): Promise<void> => {
    setPublishing(commissionId); setError(null)
    const outcome = await publishCommission(client, workspaceId, commissionId, crypto.randomUUID())
    if (outcome.kind !== 'ok') setError(mutationProblem(outcome))
    else { announce('Commission published'); commissions.reload() }
    setPublishing(null)
  }

  const makeReport = async (assignmentId: string): Promise<void> => {
    setReporting(assignmentId); setError(null)
    const outcome = await generateReport(client, workspaceId, assignmentId, crypto.randomUUID())
    if (outcome.kind !== 'ok') setError(mutationProblem(outcome))
    else { announce('Client report generated'); reportable.reload() }
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
                onDecided={(decision) => {
                  if (decision === 'ACCEPTED') reportable.reload()
                  queue.reload(); payables.reload(); commissions.reload()
                }}
              />
            ))
        )}</ResourceView>
      </section>

      {!(reportable.state.kind === 'problem' && reportable.state.problem.status === 403) && (
        <section aria-labelledby="td-reports">
          <h2 id="td-reports">Client reports</h2>
          <ResourceView resource={reportable} what="accepted assignments">{(value) =>
            value.items.length === 0 ? (
            <p className="af-secondary">No accepted evidence awaiting a report.</p>
          ) : (
            <div className="af-stack">
              {value.items.map((r) => (
                <article key={r.assignmentId} className="af-card" aria-label={`Client report for ${r.reference}`}>
                  <header><h3>{r.reference}</h3>
                    <p className="af-secondary">
                      Evidence accepted.{' '}
                      {r.latestReportRevision === null
                        ? 'No report generated yet.'
                        : `Report r${r.latestReportRevision} generated.`}
                    </p>
                  </header>
                  <div className="af-row-actions">
                    {r.latestReportRevision !== null && (
                      <StatusBadge tone="pass" kind="Client report">
                        {`REPORT r${r.latestReportRevision} GENERATED`}
                      </StatusBadge>
                    )}
                    <Button
                      variant={r.latestReportRevision === null ? 'primary' : 'secondary'}
                      busy={reporting === r.assignmentId}
                      onClick={() => void makeReport(r.assignmentId)}>
                      {r.latestReportRevision === null ? 'Generate client report' : 'Regenerate client report'}
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          )}</ResourceView>
        </section>
      )}

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

const ReviewCard = ({ assignmentId, reference, onDecided }: {
  assignmentId: string; reference: string
  onDecided: (decision: 'ACCEPTED' | 'NEEDS_REVISION') => void
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
  const [downloading, setDownloading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const submission = detail.state.kind === 'ready'
    ? detail.state.value.submissions[detail.state.value.submissions.length - 1] ?? null
    : null

  const stepTitle = (stepId: string): string =>
    detail.state.kind === 'ready'
      ? detail.state.value.brief?.taskSteps.find((s) => s.stepId === stepId)?.title ?? stepId
      : stepId

  const send = async (decision: 'ACCEPTED' | 'NEEDS_REVISION'): Promise<void> => {
    if (submission === null || justification.trim() === '') return
    setBusy(true); setError(null)
    const outcome = await decide(
      client, workspaceId, assignmentId,
      submission.submissionHash, decision, justification.trim(), crypto.randomUUID(),
    )
    setBusy(false)
    if (outcome.kind !== 'ok') setError(mutationProblem(outcome))
    else { announce(`Review recorded: ${decision}`); onDecided(decision) }
  }

  const download = async (attachmentId: string): Promise<void> => {
    setDownloading(attachmentId); setError(null)
    const outcome = await reviewAttachmentContent(client, workspaceId, assignmentId, attachmentId)
    setDownloading(null)
    if (outcome.kind === 'problem') {
      setError(outcome.problem.detail ?? outcome.problem.title)
      return
    }
    if (outcome.kind !== 'ok' && outcome.kind !== 'accepted') {
      setError('The evidence could not be fetched — check the connection and try again.')
      return
    }
    const value = outcome.value
    const bytes = Uint8Array.from(atob(value.contentBase64), (ch) => ch.charCodeAt(0))
    const url = URL.createObjectURL(new Blob([bytes], { type: value.contentType }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = value.filename
    anchor.click()
    URL.revokeObjectURL(url)
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
          {/* The sealed evidence the decision binds to. A reviewer deciding without the
              observations and the manifest in front of them is signing a hash sight-unseen. */}
          <DataTable<Observation>
            caption={`Sealed observations for ${reference}`}
            rows={[...submission.observations]}
            rowKey={(o) => o.stepId}
            columns={[
              { key: 's', header: 'Step', isRowHeader: true, cell: (o) => stepTitle(o.stepId) },
              {
                key: 'r', header: 'Result',
                cell: (o) => <StatusBadge tone={RESULT_TONE[o.result] ?? 'neutral'} kind="Step result">{o.result}</StatusBadge>,
              },
              { key: 'n', header: 'Notes', cell: (o) => o.notes ?? '—' },
            ]}
          />
          {submission.attachmentManifest.length > 0 && (
            <DataTable<Submission['attachmentManifest'][number]>
              caption={`Sealed evidence manifest for ${reference}`}
              rows={[...submission.attachmentManifest]}
              rowKey={(m) => m.attachmentId}
              columns={[
                { key: 'k', header: 'Kind', isRowHeader: true, cell: (m) => <StatusBadge tone="neutral" kind="Evidence kind">{m.kind}</StatusBadge> },
                { key: 'h', header: 'Digest', cell: (m) => <code>{m.sha256.slice(0, 12)}…</code> },
                {
                  key: 'a', header: 'Evidence',
                  cell: (m) => (
                    <Button busy={downloading === m.attachmentId}
                      onClick={() => void download(m.attachmentId)}>
                      View evidence
                    </Button>
                  ),
                },
              ]}
            />
          )}
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
    </article>
  )
}
