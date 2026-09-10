/**
 * One finding: what the evidence established, what people said about it, and how it got here.
 *
 * The screen's whole job is to keep two claims apart (INV-12). The **machine outcome** is what
 * deterministic evaluation of recorded evidence established. A **human assessment** is what a person
 * judged, attributable to them. They are rendered in separate sections, each carrying the server's
 * own sentence about how it was established, because a single merged "status" would let a
 * reviewer's ACCEPT read as the system having verified something.
 *
 * The consequence stated on the page: an accepted review cannot make an inconclusive run pass. That
 * is not a rule this screen enforces — the server does — but it is a rule a reader has to know in
 * order to read the two sections correctly.
 *
 * **REPRODUCED is not offered here.** Reproduction requires a complete, valid, failed run, and
 * reviewer commentary is not a substitute. Transitions belong to module 24's review screen with the
 * evidence in front of the reviewer; a status control on a read-only inspection page would be a way
 * to acquire that badge by opinion.
 */

import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { getFinding } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { useFindingId } from './useFindingId'

/** What each status means, in the words a reader needs rather than the enum's. */
const STATUS_MEANING: Record<string, string> = {
  CANDIDATE:
    'Proposed from evidence and not yet reproduced. It describes one execution, and nothing has ' +
    'established that it happens again.',
  REPRODUCED:
    'Seen again in a complete, valid, failed run. This is the status that required evidence rather ' +
    'than agreement.',
  DISMISSED: 'Judged not to be a defect. The evidence it was raised from is unchanged.',
  RESOLVED:
    'Accepted as repaired, which required an accepting review. That is a decision about a repair, ' +
    'not a claim that the application is accessible.',
}

export const FindingScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const findingId = useFindingId()
  const { client } = useSession()
  const finding = useResource(
    (signal) => getFinding(client, workspaceId, findingId, signal),
    [client, workspaceId, findingId],
  )

  return (
    <ResourceView resource={finding} what="this finding">
      {(detail) => (
        <>
          <RouteHeading>{detail.summary}</RouteHeading>

          <p className="af-row">
            <StatusBadge tone="neutral" kind="Finding status">
              {detail.findingStatus}
            </StatusBadge>
          </p>
          <p className="af-secondary">
            {STATUS_MEANING[detail.findingStatus] ??
              'This build does not recognise that status, so it is shown as reported.'}
          </p>

          <section className="af-stack">
            <h2>What the evidence established</h2>
            <dl>
              <dt>Run</dt>
              <dd>
                <code>{detail.machineOutcome.runId}</code>
              </dd>
              <dt>Run status</dt>
              <dd>{detail.machineOutcome.runStatus}</dd>
              <dt>Run outcome</dt>
              <dd>{detail.machineOutcome.runOutcome}</dd>
              <dt>Assertion</dt>
              <dd>
                <code>{detail.machineOutcome.assertionId}</code>
              </dd>
              <dt>Established by</dt>
              <dd>{detail.machineOutcome.establishedBy}</dd>
            </dl>
          </section>

          <section className="af-stack">
            <h2>What people said</h2>
            {detail.humanAssessments.length === 0 ? (
              <p className="af-secondary">
                No review has been submitted. The machine outcome above stands on its own and is not
                waiting for agreement.
              </p>
            ) : (
              <ul className="af-stack">
                {detail.humanAssessments.map((assessment) => (
                  <li key={assessment.reviewId} className="af-panel af-stack">
                    <h3 style={{ margin: 0, fontSize: 'var(--af-text-body)' }}>
                      {assessment.verdict}
                    </h3>
                    <dl>
                      <dt>Reviewer</dt>
                      <dd>
                        <code>{assessment.reviewerId}</code>
                      </dd>
                      <dt>Observations</dt>
                      <dd>{assessment.observations}</dd>
                      <dt>Limitations the reviewer recorded</dt>
                      <dd>
                        {assessment.limitations === '' ? (
                          <span className="af-secondary">None recorded</span>
                        ) : (
                          assessment.limitations
                        )}
                      </dd>
                      <dt>Used assistive technology</dt>
                      <dd>{assessment.usedAssistiveTechnology ? 'Yes' : 'No'}</dd>
                      <dt>Established by</dt>
                      <dd>{assessment.establishedBy}</dd>
                    </dl>
                  </li>
                ))}
              </ul>
            )}

            <Notice tone="information" heading="How to read these two sections" headingLevel={3}>
              <p>
                They are different kinds of claim and neither overrides the other. An accepted review
                cannot make an inconclusive run pass, and a machine outcome does not settle whether a
                repair is acceptable to a person.
              </p>
            </Notice>
          </section>

          <section className="af-stack">
            <h2>How this finding got here</h2>
            {detail.history.length === 0 ? (
              <p className="af-secondary">No transition has been recorded.</p>
            ) : (
              <ol className="af-stack">
                {detail.history.map((entry) => (
                  <li key={`${entry.occurredAt}-${entry.toStatus}`}>
                    <strong>
                      {entry.fromStatus ?? 'raised'} → {entry.toStatus}
                    </strong>{' '}
                    by <code>{entry.actorId}</code> at{' '}
                    <time dateTime={entry.occurredAt}>{entry.occurredAt}</time>
                    <div className="af-secondary">{entry.reason}</div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </>
      )}
    </ResourceView>
  )
}
