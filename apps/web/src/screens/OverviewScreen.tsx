/**
 * What needs attention in this workspace.
 *
 * Real rows or an honest empty state — never a metric. UI-UX section 1 rules out synthetic metrics
 * and decorative graphs, and section 7 requires usage to show measured units rather than invented
 * savings. Nothing here is computed from anything except rows the server returned.
 *
 * The "needs attention" list is the one piece of judgement on the screen, and it is deliberately
 * narrow: a run that ended without establishing anything, a run whose cancellation has not been
 * acknowledged, and a quarantined runner. Each is a state where somebody has to do something, and
 * each is read from a field the server owns rather than inferred.
 */

import { Link } from 'react-router-dom'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { DataTable } from '../components/DataTable'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { RunOutcomeBadge, RunStatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/states'
import { listRunners, listRuns } from '../api/resources'
import type { Run, Runner } from '../api/resources'
import type { RunOutcome, RunStatus } from '../components/StatusBadge'
import { useResource } from '../api/useResource'
import { workspacePath } from '../routes/routeMap'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'

/** Why this run needs a person, or null. Read from server-owned fields, never derived. */
const attentionFor = (run: Run): string | null => {
  if (run.cancellationRequestedAt !== null && run.stopAcknowledgedAt === null) {
    return 'Cancellation requested; the runner has not acknowledged stopping'
  }
  if (run.ambiguityReason !== null) return `Ambiguous: ${run.ambiguityReason}`
  if (run.quarantined) return 'Quarantined; the desktop needs a reset before anything else runs'
  if (run.status === 'INTERRUPTED') return 'Interrupted; a retry is a new run'
  if (run.status === 'COMPLETED' && run.outcome === 'INCONCLUSIVE') {
    return 'Inconclusive; evidence was missing or unusable'
  }
  return null
}

export const OverviewScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client } = useSession()
  const runs = useResource((signal) => listRuns(client, workspaceId, signal), [client, workspaceId])
  const runners = useResource(
    (signal) => listRunners(client, workspaceId, signal),
    [client, workspaceId],
  )

  return (
    <>
      <RouteHeading>Overview</RouteHeading>

      <section className="af-stack">
        <h2>Needs attention</h2>
        <ResourceView resource={runs} what="recent runs">
          {(page) => {
            const flagged = page.items
              .map((run) => ({ run, reason: attentionFor(run) }))
              .filter((entry): entry is { run: Run; reason: string } => entry.reason !== null)
            return flagged.length === 0 ? (
              <EmptyState heading="Nothing is waiting on a person" because="nothing-created-yet">
                <p className="af-secondary">
                  Every run the server returned is either still going or finished with a result
                  somebody can read. This is not a claim that any of them passed.
                </p>
              </EmptyState>
            ) : (
              <DataTable
                caption="Runs in a state that needs a person"
                rows={flagged}
                rowKey={(entry) => entry.run.runId}
                columns={[
                  {
                    key: 'run',
                    header: 'Run',
                    isRowHeader: true,
                    cell: (entry) => (
                      <Link
                        className="af-link"
                        to={workspacePath(workspaceId, `runs/${entry.run.runId}`)}
                      >
                        {entry.run.runId.slice(0, 8)}…
                      </Link>
                    ),
                  },
                  { key: 'why', header: 'Why', cell: (entry) => entry.reason },
                ]}
              />
            )
          }}
        </ResourceView>
      </section>

      <section className="af-stack">
        <h2>Recent runs</h2>
        <ResourceView resource={runs} what="recent runs">
          {(page) =>
            page.items.length === 0 ? (
              <EmptyState heading="No run has been requested" because="nothing-created-yet">
                <p className="af-secondary">
                  A run is requested from a frozen journey version. Nothing here is waiting to be
                  discovered.
                </p>
              </EmptyState>
            ) : (
              <DataTable<Run>
                caption="Runs in this workspace, with status and outcome as separate fields"
                rows={page.items}
                rowKey={(run) => run.runId}
                columns={[
                  {
                    key: 'run',
                    header: 'Run',
                    isRowHeader: true,
                    cell: (run) => (
                      <Link className="af-link" to={workspacePath(workspaceId, `runs/${run.runId}`)}>
                        {run.runId.slice(0, 8)}…
                      </Link>
                    ),
                  },
                  {
                    key: 'status',
                    header: 'Status',
                    cell: (run) => <RunStatusBadge status={run.status as RunStatus} />,
                  },
                  {
                    key: 'outcome',
                    header: 'Outcome',
                    // A separate column, never merged with status. A run can end cleanly having
                    // established nothing, and that distinction is what the product rests on.
                    cell: (run) => <RunOutcomeBadge outcome={run.outcome as RunOutcome} />,
                  },
                  {
                    key: 'cancellation',
                    header: 'Cancellation',
                    cell: (run) =>
                      run.cancellationRequestedAt === null ? (
                        <span className="af-secondary">—</span>
                      ) : run.stopAcknowledgedAt === null ? (
                        'Requested; not acknowledged'
                      ) : (
                        'Runner acknowledged stopping'
                      ),
                  },
                ]}
              />
            )
          }
        </ResourceView>
      </section>

      <section className="af-stack">
        <h2>Runner availability</h2>
        <ResourceView resource={runners} what="the runner inventory">
          {(inventory) => (
            <>
              <Notice tone="information" heading="What a status here means" headingLevel={3}>
                <p>{inventory.readinessMeaning}</p>
              </Notice>
              {inventory.items.length === 0 ? (
                <EmptyState heading="No runner is enrolled" because="nothing-created-yet">
                  <p className="af-secondary">
                    Nothing can be dispatched. A run requested now would wait indefinitely.
                  </p>
                </EmptyState>
              ) : (
                <DataTable<Runner>
                  caption="Enrolled runners and whether each has ever proved a reader was running"
                  rows={inventory.items}
                  rowKey={(runner) => runner.runnerId}
                  columns={[
                    { key: 'name', header: 'Runner', isRowHeader: true, cell: (r) => r.name },
                    { key: 'status', header: 'Status', cell: (r) => r.status },
                    {
                      key: 'preflight',
                      header: 'Preflight last passed',
                      cell: (r) =>
                        r.preflightPassedAt === null ? (
                          <span className="af-secondary">Never</span>
                        ) : (
                          <time dateTime={r.preflightPassedAt}>{r.preflightPassedAt}</time>
                        ),
                    },
                  ]}
                />
              )}
              <p>
                <Link className="af-link" to={workspacePath(workspaceId, 'runners')}>
                  Open the runner inventory
                </Link>
              </p>
            </>
          )}
        </ResourceView>
      </section>
    </>
  )
}
