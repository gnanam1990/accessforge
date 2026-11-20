/**
 * Tester Desk · capture: per-step observations with explicit save, submission and outcomes.
 *
 * The two outcome fields are never merged (T-015):
 * **task result** — what the product did (derived from the step observations);
 * **deliverable decision** — what the reviewer accepted about the evidence.
 * A FAILED task with ACCEPTED evidence is the normal, paid outcome here.
 *
 * Save is a deliberate act (the draft is autosaved server-side, but the button makes the
 * tester's intent explicit and announces it); submit seals a revision and moves it to review.
 */

import { useEffect, useRef, useState } from 'react'
import type { JSX } from 'react'
import { useParams } from 'react-router-dom'

import { RouteHeading } from '../a11y/RouteHeading'
import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import {
  getAssignment, getDraft, getPayable, getProgress,
  listAttachments, listSubmissions, saveDraft, submit, uploadAttachment,
} from '../api/testerDesk'
import type { Attachment, Observation, Submission } from '../api/testerDesk'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'

type Result = Observation['result']
const RESULTS: Result[] = ['PASSED', 'FAILED', 'UNREACHABLE', 'INCONCLUSIVE']
const RESULT_TONE = { PASSED: 'pass', FAILED: 'fail', UNREACHABLE: 'fail', INCONCLUSIVE: 'inconclusive' } as const

export const TesterCaptureScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { assignmentId = '' } = useParams()
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const assignment = useResource((s) => getAssignment(client, workspaceId, assignmentId, s), [client, workspaceId, assignmentId])
  const draft = useResource((s) => getDraft(client, workspaceId, assignmentId, s), [client, workspaceId, assignmentId])
  const progress = useResource((s) => getProgress(client, workspaceId, assignmentId, s), [client, workspaceId, assignmentId])
  const submissions = useResource((s) => listSubmissions(client, workspaceId, assignmentId, s), [client, workspaceId, assignmentId])
  const attachments = useResource((s) => listAttachments(client, workspaceId, assignmentId, s), [client, workspaceId, assignmentId])
  const payable = useResource((s) => getPayable(client, workspaceId, assignmentId, s), [client, workspaceId, assignmentId])

  const [rows, setRows] = useState<Record<string, { result: Result | ''; notes: string }>>({})
  const [baseRevision, setBaseRevision] = useState(0)
  const [busy, setBusy] = useState<'save' | 'submit' | 'attach' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [evidenceKind, setEvidenceKind] = useState('')
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null)
  // Set on the first edit. A delayed draft GET that resolves afterwards must not replace what
  // the tester has already typed — the revision is still adopted, so the next save's
  // optimistic-concurrency base stays honest.
  const editedRef = useRef(false)

  const patchRow = (stepId: string, next: { result: Result | ''; notes: string }): void => {
    editedRef.current = true
    setRows((current) => ({ ...current, [stepId]: next }))
  }

  // Hydrate the form from the persisted draft — a reload recovers exactly what was stored (T-005).
  useEffect(() => {
    if (draft.state.kind !== 'ready') return
    setBaseRevision(draft.state.value.draftRevision)
    if (editedRef.current) return
    const next: typeof rows = {}
    for (const o of draft.state.value.observations) {
      next[o.stepId] = { result: o.result, notes: o.notes ?? '' }
    }
    setRows(next)
  }, [draft.state.kind === 'ready' ? draft.state.value : null]) // eslint-disable-line react-hooks/exhaustive-deps

  const steps = assignment.state.kind === 'ready' ? assignment.state.value.brief?.taskSteps ?? [] : []

  // The write every save/submit path shares. Returns whether the draft now holds what is on
  // screen — the seal must never bind anything older than the rows the tester is looking at.
  const persistDraft = async (): Promise<boolean> => {
    const observations: Observation[] = Object.entries(rows)
      .filter(([, r]) => r.result !== '')
      .map(([stepId, r]) => ({
        stepId, result: r.result as Result,
        notes: r.notes === '' ? null : r.notes,
        source: 'HUMAN_MANUAL',
        observedAt: new Date().toISOString(),
      }))
    const outcome = await saveDraft(client, workspaceId, assignmentId, observations, baseRevision, crypto.randomUUID())
    if (outcome.kind === 'ok' || outcome.kind === 'accepted') {
      setBaseRevision(outcome.value.draftRevision)
      progress.reload()
      return true
    }
    setError(
      outcome.kind === 'problem'
        ? (outcome.problem.detail ?? outcome.problem.title)
        : 'The draft could not be saved — check the connection and try again.',
    )
    return false
  }

  const save = async (): Promise<void> => {
    setBusy('save'); setError(null)
    if (await persistDraft()) announce('Draft saved')
    setBusy(null)
  }

  const doSubmit = async (): Promise<void> => {
    setBusy('submit'); setError(null)
    // Save the current rows first: sealing the previously stored draft would sign off evidence
    // that no longer matches what the tester sees. A refused save means nothing is sealed.
    if (await persistDraft()) {
      const outcome = await submit(client, workspaceId, assignmentId, crypto.randomUUID())
      if (outcome.kind === 'problem') setError(outcome.problem.detail ?? outcome.problem.title)
      else { announce('Submission sealed and sent to review'); submissions.reload(); assignment.reload(); payable.reload() }
    }
    setBusy(null)
  }

  const doAttach = async (): Promise<void> => {
    if (evidenceFile === null || evidenceKind === '') return
    setBusy('attach'); setError(null)
    const bytes = new Uint8Array(await evidenceFile.arrayBuffer())
    let binary = ''
    for (const b of bytes) binary += String.fromCharCode(b)
    const outcome = await uploadAttachment(
      client, workspaceId, assignmentId,
      evidenceKind, evidenceFile.type || 'application/octet-stream',
      evidenceFile.name, btoa(binary), crypto.randomUUID(),
    )
    if (outcome.kind === 'problem') setError(outcome.problem.detail ?? outcome.problem.title)
    else {
      announce('Evidence attached')
      setEvidenceFile(null)
      attachments.reload()
      progress.reload()
    }
    setBusy(null)
  }

  const isActive = assignment.state.kind === 'ready' && assignment.state.value.status === 'ACTIVE'

  return (
    <div className="af-stack">
      <ResourceView resource={assignment} what="the assignment">{(a) => (
        <>
          <header><RouteHeading>Capture · {a.brief?.releaseLabel}</RouteHeading>
            <p className="af-secondary">
              {a.brief?.environment.assistiveTechnology} on {a.brief?.environment.browserName}{' '}
              {a.brief?.environment.browserVersion} · status{' '}
              <StatusBadge tone={a.status === 'COMPLETED' ? 'pass' : 'progress'} kind="Assignment status">{a.status}</StatusBadge>
            </p>
          </header>
          {error !== null && <Notice tone="problem" heading="Refused" headingLevel={2}><p>{error}</p></Notice>}

          <ResourceView resource={progress} what="rubric progress">{(p) => (
            p.missing.length > 0 && isActive ? (
              <Notice tone="warning" heading="Not yet submittable" headingLevel={2}>
                <ul>{p.missing.map((m) => <li key={m}>{m}</li>)}</ul>
              </Notice>
            ) : null
          )}</ResourceView>

          {isActive ? (
            <section aria-labelledby="cap-steps">
              <h2 id="cap-steps">Step observations</h2>
              <DataTable<{ stepId: string; title: string }>
                caption="Observations per journey step"
                rowKey={(s) => s.stepId}
                columns={[
                  { key: 't', header: 'Step', isRowHeader: true, cell: (s) => s.title },
                  {
                    key: 'r', header: 'Result',
                    cell: (s) => (
                      <fieldset className="af-segmented" aria-label={`Result for ${s.title}`}>
                        {RESULTS.map((r) => (
                          <label key={r}>
                            <input type="radio" name={`res-${s.stepId}`} value={r}
                              checked={rows[s.stepId]?.result === r}
                              onChange={() => patchRow(s.stepId, { result: r, notes: rows[s.stepId]?.notes ?? '' })} />
                            {' '}{r}
                          </label>
                        ))}
                      </fieldset>
                    ),
                  },
                  {
                    key: 'n', header: 'Notes',
                    cell: (s) => (
                      <input aria-label={`Notes for ${s.title}`}
                        value={rows[s.stepId]?.notes ?? ''}
                        onChange={(e) => patchRow(s.stepId, { result: rows[s.stepId]?.result ?? '', notes: e.target.value })} />
                    ),
                  },
                ]}
                rows={steps}
              />
              <div className="af-row-actions">
                <Button variant="primary" busy={busy === 'save'} onClick={() => void save()}>Save draft</Button>
                <Button variant="destructive" busy={busy === 'submit'} onClick={() => void doSubmit()}>Seal and submit for review</Button>
              </div>

              <fieldset className="af-fieldset">
                <legend>Attach evidence</legend>
                <label>
                  Evidence kind{' '}
                  <select aria-label="Evidence kind" value={evidenceKind}
                    onChange={(e) => setEvidenceKind(e.target.value)}>
                    <option value="">Choose…</option>
                    {(a.brief?.permittedEvidence ?? []).map((k) => <option key={k} value={k}>{k}</option>)}
                  </select>
                </label>
                {' '}
                <label>
                  File{' '}
                  <input type="file" aria-label="Evidence file"
                    onChange={(e) => setEvidenceFile(e.target.files?.[0] ?? null)} />
                </label>
                {' '}
                <Button variant="primary" busy={busy === 'attach'}
                  disabled={evidenceFile === null || evidenceKind === ''}
                  onClick={() => void doAttach()}>
                  Attach evidence
                </Button>
              </fieldset>
            </section>
          ) : null}

          <section aria-labelledby="cap-ev">
            <h2 id="cap-ev">Evidence attachments</h2>
            <ResourceView resource={attachments} what="evidence attachments">{(v) => (
              v.items.length === 0 ? <p className="af-secondary">Nothing attached yet.</p> : (
                <DataTable<Attachment>
                  caption="Digest-backed evidence attached to the current draft"
                  rowKey={(a) => a.attachmentId}
                  columns={[
                    { key: 'f', header: 'File', isRowHeader: true, cell: (a) => a.filename },
                    { key: 'k', header: 'Kind', cell: (a) => <StatusBadge tone="neutral" kind="Evidence kind">{a.kind}</StatusBadge> },
                    { key: 's', header: 'Bytes', cell: (a) => String(a.byteSize) },
                    { key: 'h', header: 'Digest', cell: (a) => <code>{a.sha256.slice(0, 12)}…</code> },
                  ]}
                  rows={v.items}
                />
              )
            )}</ResourceView>
          </section>

          <section aria-labelledby="cap-subs">
            <h2 id="cap-subs">Submissions and outcomes</h2>
            <ResourceView resource={submissions} what="submissions">{(v) => (
              v.items.length === 0 ? <p className="af-secondary">Nothing submitted yet.</p> : (
                <DataTable<Submission>
                  caption="Sealed submissions with task result and review decision"
                  rowKey={(s) => s.submissionId}
                  columns={[
                    { key: 'r', header: 'Revision', isRowHeader: true, cell: (s) => `r${s.revision}` },
                    {
                      key: 'tr', header: 'Task result',
                      cell: (s) => <StatusBadge tone={RESULT_TONE[s.taskResult as Result] ?? 'neutral'} kind="Task result">{s.taskResult}</StatusBadge>,
                    },
                    {
                      key: 'dd', header: 'Evidence decision',
                      cell: (s) => s.decision === null
                        ? <StatusBadge tone="progress" kind="Evidence decision">AWAITING REVIEW</StatusBadge>
                        : (
                          <StatusBadge tone={s.decision.decision === 'ACCEPTED' ? 'pass' : 'inconclusive'} kind="Evidence decision">
                            {s.decision.decision}
                          </StatusBadge>
                        ),
                    },
                    { key: 'j', header: 'Reviewer said', cell: (s) => s.decision?.justification ?? '—' },
                    { key: 'h', header: 'Digest', cell: (s) => <code>{s.submissionHash.slice(0, 12)}…</code> },
                  ]}
                  rows={v.items}
                />
              )
            )}</ResourceView>
          </section>

          <section aria-labelledby="cap-pay">
            <h2 id="cap-pay">Compensation</h2>
            <ResourceView resource={payable} what="payable">{(v) => (
              v.payable === null
                ? <p className="af-secondary">No payable — it is created when the evidence is accepted.</p>
                : (
                  <p>
                    {(v.payable.amountMinor / 100).toFixed(2)} {v.payable.currency} ·{' '}
                    <StatusBadge tone={v.payable.status === 'SETTLED' ? 'pass' : 'progress'} kind="Payable status">
                      {v.payable.status === 'INTENT' ? 'PENDING DISPATCH' : v.payable.status}
                    </StatusBadge>
                  </p>
                )
            )}</ResourceView>
          </section>
        </>
      )}</ResourceView>
    </div>
  )
}
