/**
 * The runner inventory.
 *
 * Two things this screen must never do, and both are things the obvious version does.
 *
 * **It must not present a row as readiness.** A runner process being reachable is not proof that a
 * screen reader is running on it, that the reader is the one enrolled, or that anything was ever
 * read back from a real desktop. So `preflightPassedAt` is displayed as its own column, `null` is
 * rendered as "never" rather than as a blank, and the server's own statement about what READY means
 * is shown on the page rather than summarised away. INV-02 turns on this: a missing reader
 * capability must never resolve to a pass, and a list is the first place that inference gets made.
 *
 * **Implementation is not qualification.** Reader adapter code does not establish that a particular
 * enrolled desktop can execute a journey. Runtime evidence belongs to each runner and run.
 */

import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { DataTable } from '../components/DataTable'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/states'
import { listRunners } from '../api/resources'
import type { Runner } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { RunnerEnrollment } from './RunnerEnrollment'

const TONE: Record<string, 'neutral' | 'progress' | 'pass' | 'fail' | 'interrupted'> = {
  OFFLINE: 'neutral',
  PREFLIGHT_REQUIRED: 'neutral',
  READY: 'pass',
  BUSY: 'progress',
  QUARANTINED: 'interrupted',
}

/**
 * Reader profiles and what is actually known about each.
 *
 * Kept as data on this screen rather than fetched, because it is not a property of any runner: it is
 * the state of this project's verification of each matrix, which lives in the adapters and in
 * docs/capabilities.md. A screen that silently offered every combination would be claiming they all
 * work.
 */
const MATRIX: readonly {
  readonly platform: string
  readonly reader: string
  readonly state: string
  readonly detail: string
}[] = [
  {
    platform: 'macOS',
    reader: 'VoiceOver',
    state: 'Requires desktop qualification',
    detail:
      'Requires a dedicated macOS desktop, explicit reader consent and a supported profile. ' +
      'Check the enrolled runner’s preflight evidence; this website does not verify VoiceOver.',
  },
  {
    platform: 'Windows',
    reader: 'NVDA',
    state: 'Requires desktop qualification',
    detail:
      'Requires a Windows desktop with the supported NVDA adapter and explicit reader consent. ' +
      'Adapter implementation alone is not proof of a qualified runner or a passing result.',
  },
  {
    platform: 'Mobile and PDF',
    reader: '—',
    state: 'Out of scope',
    detail: 'Not a runnable option in E0 or R1. It is listed so its absence is not a surprise.',
  },
]

export const RunnersScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client } = useSession()
  const runners = useResource(
    (signal) => listRunners(client, workspaceId, signal),
    [client, workspaceId],
  )

  return (
    <>
      <RouteHeading>Runners</RouteHeading>
      <RunnerEnrollment workspaceId={workspaceId} onEnrolled={runners.reload} />

      <ResourceView resource={runners} what="the runner inventory">
        {(inventory) => (
          <>
            <Notice tone="information" heading="What a status here means" headingLevel={2}>
              <p>{inventory.readinessMeaning}</p>
            </Notice>

            {!inventory.complete && (
              <Notice tone="warning" heading="This list is not complete" headingLevel={2}>
                <p>
                  There are more runners than this screen read. What is below is a prefix, not the
                  inventory, and a runner that is not shown may still be holding a desktop.
                </p>
              </Notice>
            )}

            {inventory.items.length === 0 ? (
              <EmptyState heading="No runner is enrolled" because="nothing-created-yet">
                <p className="af-secondary">
                  Collect identity on the desktop it will drive. A workspace owner can review that
                  observation and register it with a single-use token above. Enrollment alone does
                  not qualify the desktop, and a runner without successful preflight cannot be given work.
                </p>
              </EmptyState>
            ) : (
              <DataTable<Runner>
                caption="Enrolled runners, their profiles and the evidence behind each status"
                rows={inventory.items}
                rowKey={(runner) => runner.runnerId}
                columns={[
                  {
                    key: 'name',
                    header: 'Runner',
                    isRowHeader: true,
                    cell: (runner) => runner.name,
                  },
                  {
                    key: 'status',
                    header: 'Status',
                    cell: (runner) => (
                      <StatusBadge tone={TONE[runner.status] ?? 'neutral'} kind="Runner status">
                        {runner.status}
                      </StatusBadge>
                    ),
                  },
                  {
                    key: 'profile',
                    header: 'Profile',
                    cell: (runner) => (
                      <>
                        <div>{runner.platform}</div>
                        <div className="af-secondary">
                          {runner.profile.readerName ?? 'no reader recorded'}
                          {/* Nullish, not `=== undefined`. A payload carrying `readerVersion: null`
                              rendered the literal text " null" beside the reader's name. */}
                          {runner.profile.readerVersion == null
                            ? ''
                            : ` ${runner.profile.readerVersion}`}
                        </div>
                      </>
                    ),
                  },
                  {
                    key: 'preflight',
                    header: 'Preflight last passed',
                    cell: (runner) =>
                      runner.preflightPassedAt === null ? (
                        // "Never" in words. A blank cell reads as missing data rather than as the
                        // fact that this desktop has never proved a reader was running on it.
                        <span className="af-secondary">Never</span>
                      ) : (
                        <time dateTime={runner.preflightPassedAt}>{runner.preflightPassedAt}</time>
                      ),
                  },
                  {
                    key: 'quarantine',
                    header: 'Quarantine reason',
                    cell: (runner) =>
                      runner.quarantineReason ?? <span className="af-secondary">—</span>,
                  },
                  {
                    key: 'resets',
                    header: 'Resets',
                    cell: (runner) => String(runner.resetCount),
                  },
                ]}
              />
            )}
          </>
        )}
      </ResourceView>

      <section className="af-stack">
        <h2>Supported reader and platform combinations</h2>
        <p className="af-secondary">
          Listed whether or not they work. A screen offering three profiles without saying which are
          verified invites an operator to choose one that cannot run.
        </p>
        <DataTable
          caption="Reader and platform matrix, and what is known about each combination"
          rows={MATRIX}
          rowKey={(row) => `${row.platform}-${row.reader}`}
          columns={[
            { key: 'platform', header: 'Platform', isRowHeader: true, cell: (r) => r.platform },
            { key: 'reader', header: 'Reader', cell: (r) => r.reader },
            {
              key: 'state',
              header: 'State',
              cell: (r) => (
                <StatusBadge tone="neutral" kind="Support">
                  {r.state}
                </StatusBadge>
              ),
            },
            { key: 'detail', header: 'What that means', cell: (r) => r.detail },
          ]}
        />
      </section>
    </>
  )
}
