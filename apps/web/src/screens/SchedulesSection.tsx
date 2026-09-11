/**
 * Standing authorizations and the schedules that draw on them, as a settings section.
 *
 * A section rather than a route, because UI-UX section 3's route table puts schedules under
 * `/w/:workspaceId/settings` — "Membership, integrations, retention, budgets, schedules and manual
 * entitlement". The first version of this was a `/schedules` route, and the route-table test caught
 * it immediately: adding a path the specification does not list is precisely what module 21's
 * acceptance gate forbids, and the three tests that failed were the ones holding the router, the
 * sidebar and the spec together.
 *
 * A grant is the only authority in this system that acts while nobody is watching, so almost
 * everything on this screen is about making that visible rather than convenient.
 *
 * **A grant that cannot be used says why, in the server's words.** `usable` and `unusableBecause`
 * are read from the response, not re-derived here. A screen with its own idea of usability
 * eventually disagrees with the code that enforces it, and the disagreement appears as a grant this
 * page calls usable and every run refuses.
 *
 * **Revoked grants and paused schedules are listed.** The question after an incident is "what was
 * allowed to run, and when did that stop". Hiding either half answers only the first part, and a
 * paused schedule that vanished would look deleted.
 *
 * **Recovering from a restore takes two separate confirmations, and this screen keeps them
 * separate.** Reconciliation marks every restored grant as needing revalidation and moves its
 * revision, which also stops every schedule bound to it. Confirming the grant does not restart the
 * schedules: "this standing authorization is still valid" and "this recurring job should start
 * running again" are different decisions, and a single button that did both would restart work
 * nobody asked it to restart — overnight, against a real desktop, on one click during an incident.
 * So the schedule's own control stays separate and appears only once its grant is usable.
 *
 * **Nothing here creates a grant or a schedule.** Both are authorizations with bounds on every axis
 * and an expiry, and a form that collected nine digests would be a form nobody could fill in
 * correctly from a browser. The routes exist and the CLI reaches them; a creation form that produced
 * a plausible grant would be worse than its absence. The screen says so rather than leaving a reader
 * to wonder where the button is.
 */

import type { JSX } from 'react'
import { useCallback, useState } from 'react'

import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/states'
import {
  listGrants,
  listSchedules,
  pauseSchedule,
  reapproveSchedule,
  resumeSchedule,
  revalidateGrant,
  revokeGrant,
} from '../api/resources'
import type { ExecutionGrant, GrantInventory, Schedule } from '../api/resources'
import type { ApiOutcome } from '../api/client'
import { useResource } from '../api/useResource'
import type { Resource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'

/**
 * What a grant's state is called, and the tone it is shown in.
 *
 * Three states, not two. "Awaiting revalidation" is neither usable nor revoked: nobody withdrew it,
 * and nobody can currently vouch for it either. Collapsing it into "revoked" would lose the
 * difference between a decision somebody made and the absence of one.
 */
const grantState = (
  grant: ExecutionGrant,
): { readonly label: string; readonly tone: 'pass' | 'fail' | 'interrupted' | 'neutral' } => {
  if (grant.revoked) return { label: 'Revoked', tone: 'neutral' }
  if (grant.revalidationRequired) return { label: 'Awaiting revalidation', tone: 'interrupted' }
  if (!grant.usable) return { label: 'Not usable', tone: 'fail' }
  return { label: 'Usable', tone: 'pass' }
}

const scheduleState = (
  schedule: Schedule,
  grant: ExecutionGrant | undefined,
): { readonly label: string; readonly tone: 'pass' | 'fail' | 'interrupted' | 'neutral' } => {
  if (schedule.pausedAt !== null) return { label: 'Paused', tone: 'neutral' }
  if (grant === undefined) return { label: 'Grant not visible', tone: 'fail' }
  if (!grant.usable) return { label: 'Stopped — grant unusable', tone: 'fail' }
  // The comparison that explains a schedule which has quietly stopped. Every occurrence is rechecked
  // against the grant as it stands now, so a revision that has moved is a schedule that skips.
  if (schedule.grantRevisionAtApproval !== grant.revision) {
    return { label: 'Stopped — grant moved', tone: 'interrupted' }
  }
  return { label: 'Active', tone: 'pass' }
}

export const SchedulesSection = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client } = useSession()
  const { announce } = useAnnouncer()

  const grants = useResource(
    (signal) => listGrants(client, workspaceId, signal),
    [client, workspaceId],
  )
  const schedules = useResource(
    (signal) => listSchedules(client, workspaceId, signal),
    [client, workspaceId],
  )

  const [busyId, setBusyId] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)

  /**
   * Run one mutation, then reload both listings.
   *
   * Both, always, because they are coupled: revoking a grant stops its schedules, and revalidating
   * one moves a revision that every schedule bound to it is compared against. Reloading only the
   * list that was acted on would leave the other showing a state that is no longer true, on the same
   * screen, which is worse than showing nothing.
   */
  const act = useCallback(
    async <T,>(id: string, what: string, run: () => Promise<ApiOutcome<T>>): Promise<void> => {
      setBusyId(id)
      setRefusal(null)
      const outcome = await run()
      setBusyId(null)

      switch (outcome.kind) {
        case 'ok':
        case 'accepted':
          announce(`${what} succeeded.`)
          grants.reload()
          schedules.reload()
          return
        case 'problem':
          // The server's own words. A paraphrase would be a second, worse explanation of a refusal
          // the server already described precisely -- and these refusals are the useful ones: a
          // stale revision means re-read and decide again, which the detail says.
          setRefusal(outcome.problem.detail)
          grants.reload()
          schedules.reload()
          return
        case 'offline':
          setRefusal(
            'The server did not answer, so whether this took effect is unknown. Reload before ' +
              'trying again.',
          )
          return
        case 'cancelled':
        case 'stale':
        case 'unauthenticated':
          // The shell handles the last of those; the other two mean this request was superseded.
          // Saying nothing is correct, and saying "failed" would be wrong.
          return
      }
    },
    [announce, grants, schedules],
  )

  return (
    <section className="af-stack" aria-labelledby="standing-authorizations">
      <h2 id="standing-authorizations">Schedules and standing authorizations</h2>

      <Notice tone="information" heading="What a standing grant is" headingLevel={3}>
        <p>
          A grant is the only authority here that acts while nobody is watching. It permits runs of
          the journey versions it names, in the environment it names, within a bounded number of
          actions and seconds, until it expires. It never authorizes a repair or a publication,
          however many runs it has already permitted.
        </p>
        <p>
          Grants and schedules are created through the API or the <code>accessforge</code> command,
          not from this screen. Both carry bounds on every axis and nine input digests; a form that
          collected those in a browser would produce a plausible authorization rather than a correct
          one.
        </p>
      </Notice>

      {refusal !== null && (
        <Notice tone="warning" heading="That was refused" headingLevel={3}>
          <p>{refusal}</p>
        </Notice>
      )}

      <ResourceView resource={grants} what="the standing authorizations">
        {(inventory) => (
          <div className="af-stack" aria-labelledby="grants-heading">
            <h3 id="grants-heading">Standing authorizations</h3>
            <p>{inventory.meaning}</p>

            {inventory.items.length === 0 ? (
              <EmptyState heading="No standing authorizations" because="nothing-created-yet">
                <p>
                  The server answered and there are none. No schedule can run without one, so
                  nothing in this workspace is firing on a timer.
                </p>
              </EmptyState>
            ) : (
              <DataTable
                caption="Standing execution grants, with what each permits and whether it is currently usable"
                rowKey={(grant) => grant.grantId}
                rows={[...inventory.items]}
                columns={[
                  {
                    key: 'grant',
                    header: 'Grant',
                    isRowHeader: true,
                    cell: (grant) => <code>{grant.grantId.slice(0, 8)}…</code>,
                  },
                  {
                    key: 'state',
                    header: 'State',
                    cell: (grant) => {
                      const state = grantState(grant)
                      return (
                        <StatusBadge tone={state.tone} kind="State">
                          {state.label}
                        </StatusBadge>
                      )
                    },
                  },
                  { key: 'environment', header: 'Environment', cell: (grant) => grant.environment },
                  {
                    key: 'bounds',
                    header: 'Bounds',
                    cell: (grant) => (
                      <>
                        {grant.actionBudget} actions, {grant.wallTimeBudgetSeconds}s
                      </>
                    ),
                  },
                  { key: 'revision', header: 'Revision', cell: (grant) => grant.revision },
                  { key: 'expires', header: 'Expires', cell: (grant) => grant.expiresAt },
                  {
                    key: 'why',
                    header: 'Why not usable',
                    // An em dash rather than a blank. A blank cell in a column named "why not
                    // usable" reads as an unanswered question.
                    cell: (grant) => grant.unusableBecause ?? '—',
                  },
                  {
                    key: 'act',
                    header: 'Action',
                    cell: (grant) => (
                      <div className="af-row">
                        {grant.revalidationRequired && !grant.revoked && (
                          <Button
                            variant="primary"
                            busy={busyId === grant.grantId}
                            onClick={() =>
                              void act(grant.grantId, 'Revalidating this grant', () =>
                                revalidateGrant(
                                  client,
                                  workspaceId,
                                  grant.grantId,
                                  grant.revision,
                                ),
                              )
                            }
                          >
                            Confirm still authorized
                          </Button>
                        )}
                        {!grant.revoked && (
                          <Button
                            variant="destructive"
                            busy={busyId === grant.grantId}
                            onClick={() =>
                              void act(grant.grantId, 'Revoking this grant', () =>
                                revokeGrant(client, workspaceId, grant.grantId, grant.revision),
                              )
                            }
                          >
                            Revoke
                          </Button>
                        )}
                      </div>
                    ),
                  },
                ]}
              />
            )}

            {inventory.items.some((grant) => grant.revalidationRequired) && (
              <Notice tone="warning" heading="A restore brought these grants back" headingLevel={4}>
                <p>
                  A grant revoked after the snapshot is live in restored data and revoked in the
                  world, and nothing in that data can tell the difference. Each one below is unusable
                  until a person confirms it is still authorized.
                </p>
                <p>
                  Confirming a grant does not restart the schedules that draw on it. That is a
                  separate decision, and it is made on each schedule below.
                </p>
              </Notice>
            )}
          </div>
        )}
      </ResourceView>

      <ResourceView resource={schedules} what="the schedules">
        {(inventory) => (
          <div className="af-stack" aria-labelledby="schedules-heading">
            <h3 id="schedules-heading">Schedules</h3>
            <p>{inventory.meaning}</p>

            {inventory.items.length === 0 ? (
              <EmptyState heading="No schedules" because="nothing-created-yet">
                <p>The server answered and there are none. Nothing is firing on a timer.</p>
              </EmptyState>
            ) : (
              <DataTable
                caption="Schedules, the grant each draws on, and whether it is currently admitting occurrences"
                rowKey={(schedule) => schedule.scheduleId}
                rows={[...inventory.items]}
                columns={[
                  { key: 'name', header: 'Name', isRowHeader: true, cell: (s) => s.name },
                  {
                    key: 'state',
                    header: 'State',
                    cell: (schedule) => {
                      const grant = grantFor(grants, schedule)
                      const state = scheduleState(schedule, grant)
                      return (
                        <StatusBadge tone={state.tone} kind="State">
                          {state.label}
                        </StatusBadge>
                      )
                    },
                  },
                  { key: 'cron', header: 'When', cell: (s) => `${s.cronExpression} (${s.timezone})` },
                  { key: 'ref', header: 'Source', cell: (s) => s.sourceRef },
                  {
                    key: 'grant',
                    header: 'Grant revision',
                    cell: (schedule) => {
                      const grant = grantFor(grants, schedule)
                      const current = grant?.revision
                      // Both numbers, always. Showing only the approved one hides the comparison
                      // that explains why a schedule stopped, and showing only the current one
                      // hides what was actually approved.
                      return current === undefined
                        ? `approved at ${schedule.grantRevisionAtApproval}`
                        : `approved at ${schedule.grantRevisionAtApproval}, now ${current}`
                    },
                  },
                  { key: 'expires', header: 'Expires', cell: (s) => s.expiresAt },
                  {
                    key: 'act',
                    header: 'Action',
                    cell: (schedule) => {
                      const grant = grantFor(grants, schedule)
                      const needsReapproval =
                        grant !== undefined &&
                        grant.usable &&
                        schedule.grantRevisionAtApproval !== grant.revision
                      return (
                        <div className="af-row">
                          {needsReapproval && (
                            <Button
                              variant="primary"
                              busy={busyId === schedule.scheduleId}
                              onClick={() =>
                                void act(schedule.scheduleId, 'Re-approving this schedule', () =>
                                  reapproveSchedule(
                                    client,
                                    workspaceId,
                                    schedule.scheduleId,
                                    schedule.revision,
                                  ),
                                )
                              }
                            >
                              Re-approve
                            </Button>
                          )}
                          {schedule.pausedAt === null ? (
                            <Button
                              variant="secondary"
                              busy={busyId === schedule.scheduleId}
                              onClick={() =>
                                void act(schedule.scheduleId, 'Pausing this schedule', () =>
                                  pauseSchedule(
                                    client,
                                    workspaceId,
                                    schedule.scheduleId,
                                    schedule.revision,
                                  ),
                                )
                              }
                            >
                              Pause
                            </Button>
                          ) : (
                            <Button
                              variant="secondary"
                              busy={busyId === schedule.scheduleId}
                              onClick={() =>
                                void act(schedule.scheduleId, 'Resuming this schedule', () =>
                                  resumeSchedule(
                                    client,
                                    workspaceId,
                                    schedule.scheduleId,
                                    schedule.revision,
                                  ),
                                )
                              }
                            >
                              Resume
                            </Button>
                          )}
                        </div>
                      )
                    },
                  },
                ]}
              />
            )}

            <Notice tone="information" heading="Resuming is not the same as running" headingLevel={4}>
              <p>
                Every occurrence is rechecked against the grant as it stands at that moment. A
                schedule resumed under a revoked, expired or unrevalidated grant still skips, and the
                skip is recorded with its reason rather than omitted — a schedule silenced by its
                grant would otherwise look like one nobody configured.
              </p>
              <p>
                An occurrence also needs a runner. Nothing on this screen, and nothing about a
                schedule, makes a desktop available.
              </p>
            </Notice>
          </div>
        )}
      </ResourceView>
    </section>
  )
}

/**
 * The grant a schedule draws on, if this reader can see it.
 *
 * Returns undefined rather than throwing when the grants listing has not loaded or the grant is not
 * visible. A schedule whose grant cannot be read is shown as such — "grant not visible" — which is
 * different from a schedule whose grant is revoked, and the screen must not present the second as
 * the first.
 */
const grantFor = (
  grants: Resource<GrantInventory>,
  schedule: Schedule,
): ExecutionGrant | undefined =>
  grants.state.kind === 'ready'
    ? grants.state.value.items.find((grant) => grant.grantId === schedule.grantId)
    : undefined
