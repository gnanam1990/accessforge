/**
 * One run: what happened, what it established, what evidence is missing, and what to do next.
 *
 * The screen answers those four questions in that order, because that is the order a reviewer needs
 * them in and because putting the outcome first invites reading it before knowing whether the
 * evidence supported it.
 *
 * **Status and outcome are separate fields and are never merged.** A run can end cleanly having
 * established nothing. `COMPLETED` with `FAIL` is a valid, useful result; `INTERRUPTED` is neither
 * an accessible journey nor a confirmed defect, and this screen says so in words rather than
 * leaving the reader to infer it from a colour.
 *
 * **Nothing here derives a verdict.** The situation copy is chosen from server-owned fields, and
 * `PASS` appears in exactly one branch — reached only when the server has already said both
 * `COMPLETED` and `PASS`. There is no path by which a green-looking last event, a completion
 * receipt, or a full evidence set becomes a pass on this screen.
 *
 * **Assertion results are absent, and the absence is stated.** The evaluator is a pure function in
 * `packages/domain/.../evaluation`; nothing assembles its inputs from a stored attempt and no route
 * serves a per-assertion result. Rendering the run's outcome as though it were a list of assertion
 * outcomes would be the interface inventing the very thing it exists to report.
 *
 * **Cancellation is reported as what the server proved.** "Requested; waiting for runner
 * acknowledgement" until a stop is acknowledged, and never "cancelled" before it is.
 */

import { useState } from 'react'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { RunOutcomeBadge, RunStatusBadge, StatusBadge } from '../components/StatusBadge'
import type { RunOutcome, RunStatus } from '../components/StatusBadge'
import { EvidenceTimeline } from './EvidenceTimeline'
import {
  getRun,
  listAttempts,
  readCompleteness,
  readTimeline,
  requestCancellation,
} from '../api/resources'
import type { Run } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { situationFor } from './runStatus'
import { useWorkspaceId } from './useWorkspaceId'
import { useRunId } from './useRunId'

const NON_TERMINAL = new Set(['QUEUED', 'RUNNING', 'FINALIZING'])

const CompletenessSection = ({
  workspaceId,
  runId,
  attemptId,
}: {
  readonly workspaceId: string
  readonly runId: string
  readonly attemptId: string
}): JSX.Element => {
  const { client } = useSession()
  const completeness = useResource(
    (signal) => readCompleteness(client, workspaceId, runId, attemptId, signal),
    [client, workspaceId, runId, attemptId],
  )

  return (
    <section className="af-stack">
      <h2>Evidence completeness</h2>
      <ResourceView resource={completeness} what="what is missing from this attempt">
        {(report) => (
          <>
            <Notice tone="information" heading="What this section is" headingLevel={3}>
              <p>{report.meaning}</p>
            </Notice>

            {report.reasons.length === 0 ? (
              <p>
                Nothing is recorded as missing from this attempt. That is a statement about the
                evidence and not about the run: a complete set can still describe a failure.
              </p>
            ) : (
              <ul>
                {report.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}

            <dl>
              <dt>Chain contiguous</dt>
              <dd>
                <StatusBadge tone={report.contiguous ? 'pass' : 'fail'} kind="Chain">
                  {report.contiguous ? 'No gaps' : 'Has gaps'}
                </StatusBadge>
              </dd>
              <dt>Producers closed</dt>
              <dd>
                <StatusBadge tone={report.producersClosed ? 'pass' : 'fail'} kind="Producers">
                  {report.producersClosed ? 'All closed' : 'Some still open'}
                </StatusBadge>
                <p className="af-secondary">
                  Separate from the chain on purpose. A producer that stopped halfway leaves a
                  perfect contiguous chain covering half the attempt.
                </p>
              </dd>
              <dt>Required artifacts</dt>
              <dd>
                <StatusBadge tone={report.artifactsPresent ? 'pass' : 'fail'} kind="Artifacts">
                  {report.artifactsPresent ? 'Present' : 'Missing'}
                </StatusBadge>
              </dd>
              <dt>Attempt extent</dt>
              <dd>
                <StatusBadge tone={report.lifecycleBounded ? 'pass' : 'fail'} kind="Extent">
                  {report.lifecycleBounded ? 'Bounded' : 'Undefined'}
                </StatusBadge>
              </dd>
            </dl>

            <h3>Producers</h3>
            {report.producers.length === 0 ? (
              <p className="af-secondary">
                No producer submitted anything for this attempt. That is missing evidence, not an
                uneventful run.
              </p>
            ) : (
              <ul>
                {report.producers.map((producer) => (
                  <li key={producer.producerId}>
                    <code>{producer.producerId}</code> — admitted through{' '}
                    {producer.admittedThrough}
                    {producer.closedAt === null ? (
                      /* "Never closed" and "closed at position zero" are different statements, and
                         only one of them means the producer finished. */
                      <strong> · never closed its stream</strong>
                    ) : (
                      <span className="af-secondary"> · closed at {producer.closedAt}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </ResourceView>
    </section>
  )
}

const TimelineSection = ({
  workspaceId,
  runId,
  attemptId,
}: {
  readonly workspaceId: string
  readonly runId: string
  readonly attemptId: string
}): JSX.Element => {
  const { client } = useSession()
  // The page cursor, and the trail of cursors behind it. Keeping the trail is what makes "previous"
  // exact: a keyset cursor is not reversible, and computing a previous position by subtracting a
  // page size would silently skip or repeat events whenever a page was short.
  const [after, setAfter] = useState(0)
  const [history, setHistory] = useState<readonly number[]>([])

  const timeline = useResource(
    (signal) => readTimeline(client, workspaceId, runId, attemptId, after, signal),
    [client, workspaceId, runId, attemptId, after],
  )

  return (
    <ResourceView resource={timeline} what="this attempt's evidence">
      {(page) => (
        <EvidenceTimeline
          timeline={page}
          pageStart={after}
          onPrevious={
            history.length === 0
              ? null
              : () => {
                  setAfter(history[history.length - 1] ?? 0)
                  setHistory((current) => current.slice(0, -1))
                }
          }
          onNext={
            page.exhausted
              ? null
              : () => {
                  setHistory((current) => [...current, after])
                  setAfter(page.nextAfterSequence)
                }
          }
        />
      )}
    </ResourceView>
  )
}

export const RunScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const runId = useRunId()
  const { client } = useSession()
  const { announce } = useAnnouncer()

  const run = useResource(
    (signal) => getRun(client, workspaceId, runId, signal),
    [client, workspaceId, runId],
  )
  const attempts = useResource(
    (signal) => listAttempts(client, workspaceId, runId, signal),
    [client, workspaceId, runId],
  )
  const [selectedAttempt, setSelectedAttempt] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [cancelNotice, setCancelNotice] = useState<string | null>(null)

  const cancel = async (current: Run): Promise<void> => {
    setCancelling(true)
    const outcome = await requestCancellation(client, workspaceId, runId, current.revision)
    setCancelling(false)
    switch (outcome.kind) {
      case 'ok':
      case 'accepted':
        // "Requested", never "cancelled". Nothing has established that the desktop stopped.
        setCancelNotice(outcome.value.meaning)
        announce('Cancellation requested. The runner has not acknowledged stopping.')
        run.reload()
        break
      case 'problem':
        setCancelNotice(outcome.problem.detail)
        break
      case 'offline':
        setCancelNotice(
          'The server did not answer, so whether the cancellation was recorded is unknown. ' +
            'Reading this run again will say.',
        )
        break
      case 'cancelled':
      case 'stale':
      case 'unauthenticated':
        break
    }
  }

  return (
    <ResourceView resource={run} what="this run">
      {(value) => {
        const situation = situationFor(value)
        return (
          <>
            <RouteHeading>Run {value.runId.slice(0, 8)}</RouteHeading>

            <section className="af-stack">
              <h2>What happened</h2>
              <p className="af-row">
                <RunStatusBadge status={value.status as RunStatus} />
                <RunOutcomeBadge outcome={value.outcome as RunOutcome} />
              </p>
              <Notice tone="information" heading={situation.headline} headingLevel={3}>
                <p>{situation.detail}</p>
              </Notice>
              <p className="af-secondary">
                Status and outcome are separate fields. Status says how this run ended; outcome says
                what it established, and a run can end cleanly having established nothing.
              </p>

              {value.ambiguityReason !== null && (
                <Notice tone="warning" heading="An effect is ambiguous" headingLevel={3}>
                  <p>{value.ambiguityReason}</p>
                  <p className="af-secondary">
                    An action was dispatched and its result never arrived. It may have taken effect.
                    Nothing here undoes it, and a retry is a new run with fresh fixtures.
                  </p>
                </Notice>
              )}

              {value.quarantined && (
                <Notice tone="warning" heading="The desktop is quarantined" headingLevel={3}>
                  <p>
                    Nothing else will be dispatched to it until a trusted reset and a fresh preflight
                    establish that the previous actor cannot act.
                  </p>
                </Notice>
              )}

              {value.retryOf !== null && (
                <p>
                  This run is a retry of <code>{value.retryOf}</code>. The earlier run is unchanged;
                  a retry never resumes one.
                </p>
              )}
            </section>

            <section className="af-stack">
              <h2>Identity</h2>
              <dl>
                <dt>Run</dt>
                <dd>
                  <code>{value.runId}</code>
                </dd>
                <dt>Sealed manifest digest</dt>
                <dd>
                  <code>{value.manifestDigest}</code>
                </dd>
                <dt>Revision</dt>
                <dd>{value.revision}</dd>
                <dt>Lease epoch</dt>
                <dd>{value.leaseEpoch}</dd>
              </dl>
              <p className="af-secondary">
                The manifest digest is this run’s identity. It covers the source, the build, the
                environment, the journey, its assertions and fixture, the runner profile, the
                evaluator and the model configuration together.
              </p>
            </section>

            <section className="af-stack">
              <h2>Assertion results</h2>
              <Notice tone="warning" heading="Not available in this build" headingLevel={3}>
                <p>
                  This run’s outcome is shown above as the server reported it. The per-assertion
                  results behind it are not: the evaluator exists as a pure function, and nothing
                  assembles its inputs from a stored attempt or serves a per-assertion result.
                </p>
                <p className="af-secondary">
                  Listing the run’s outcome once per assertion would be this interface inventing the
                  thing it exists to report. Until a route serves the evaluator’s own output, a
                  reviewer cannot see which exact assertion failed.
                </p>
              </Notice>
            </section>

            {NON_TERMINAL.has(value.status) && (
              <section className="af-stack">
                <h2>Stop this run</h2>
                <p className="af-secondary">
                  Requesting a stop records the request. It does not stop a desktop, and the run
                  stays in its current state until a runner acknowledges.
                </p>
                {cancelNotice !== null && (
                  <Notice tone="warning" heading="Cancellation" headingLevel={3} live>
                    <p>{cancelNotice}</p>
                  </Notice>
                )}
                <Button
                  variant="destructive"
                  busy={cancelling}
                  onClick={() => void cancel(value)}
                >
                  Request cancellation
                </Button>
              </section>
            )}

            <section className="af-stack">
              <h2>Attempts</h2>
              <p className="af-secondary">
                Each attempt has its own canonical chain. Mixing two attempts into one timeline would
                produce a sequence that never happened, so one is chosen at a time.
              </p>
              <ResourceView resource={attempts} what="this run's attempts">
                {(page) => {
                  const chosen = selectedAttempt ?? page.items[0]?.attemptId ?? null
                  return page.items.length === 0 ? (
                    <Notice tone="warning" heading="No attempt was ever started" headingLevel={3}>
                      <p>
                        Nothing was dispatched to a desktop, so there is no evidence to read. This is
                        not the same as an attempt that ran and recorded nothing.
                      </p>
                    </Notice>
                  ) : (
                    <>
                      <ul className="af-nav-list">
                        {page.items.map((item) => (
                          <li key={item.attemptId}>
                            <Button
                              variant={item.attemptId === chosen ? 'primary' : 'secondary'}
                              aria-current={item.attemptId === chosen ? 'true' : undefined}
                              onClick={() => setSelectedAttempt(item.attemptId)}
                            >
                              Epoch {item.leaseEpoch}, started {item.startedAt}
                              {item.endedAt === null ? ' (no recorded end)' : ''}
                            </Button>
                          </li>
                        ))}
                      </ul>
                      {chosen !== null && (
                        <>
                          <CompletenessSection
                            workspaceId={workspaceId}
                            runId={runId}
                            attemptId={chosen}
                          />
                          <TimelineSection
                            workspaceId={workspaceId}
                            runId={runId}
                            attemptId={chosen}
                          />
                        </>
                      )}
                    </>
                  )
                }}
              </ResourceView>
            </section>
          </>
        )
      }}
    </ResourceView>
  )
}
