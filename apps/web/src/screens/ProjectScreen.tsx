/**
 * One project: its authorized scope, its environments, and its journey versions.
 *
 * The version list is the part with a rule attached. A version that has been superseded is marked
 * as superseded, and the mark is computed across the whole project rather than within the page —
 * because a successor on a later page would otherwise leave a stale version looking current, and
 * current is what an operator runs.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { DataTable } from '../components/DataTable'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/states'
import { EnvironmentSection } from './EnvironmentSection'
import { JourneyAuthoringSection } from './JourneyAuthoringSection'
import { getProject, listJourneyVersions } from '../api/resources'
import type { JourneyVersion } from '../api/resources'
import { useResource } from '../api/useResource'
import { workspacePath } from '../routes/routeMap'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { useProjectId } from './useProjectId'

export const ProjectScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const projectId = useProjectId()
  const { client } = useSession()
  const [freezeCount, setFreezeCount] = useState(0)

  const project = useResource(
    (signal) => getProject(client, workspaceId, projectId, signal),
    [client, workspaceId, projectId],
  )
  const versions = useResource(
    (signal) => listJourneyVersions(client, workspaceId, projectId, signal),
    [client, workspaceId, projectId, freezeCount],
  )

  return (
    <>
      <ResourceView resource={project} what="this project">
        {(value) => (
          <>
            <RouteHeading>{value.name}</RouteHeading>
            <dl>
              <dt>Repository</dt>
              <dd>
                {value.repositoryUrl === null ? (
                  <span className="af-secondary">Not recorded</span>
                ) : (
                  <code>{value.repositoryUrl}</code>
                )}
              </dd>
              <dt>Added</dt>
              <dd>
                <time dateTime={value.createdAt}>{value.createdAt}</time>
              </dd>
            </dl>
          </>
        )}
      </ResourceView>

      <EnvironmentSection workspaceId={workspaceId} projectId={projectId} />

      <section className="af-stack">
        <h2>Journey versions</h2>
        <ResourceView resource={versions} what="this project's journey versions">
          {(page) =>
            page.items.length === 0 ? (
              <EmptyState heading="No journey version is frozen" because="nothing-created-yet">
                <p className="af-secondary">
                  A journey version is immutable once frozen. Nothing can run until one exists.
                </p>
              </EmptyState>
            ) : (
              <DataTable<JourneyVersion>
                caption="Frozen journey versions, newest identity last, with their lineage"
                rows={page.items}
                rowKey={(version) => version.journeyVersionId}
                columns={[
                  {
                    key: 'name',
                    header: 'Journey',
                    isRowHeader: true,
                    cell: (version) => (
                      <Link
                        className="af-link"
                        to={workspacePath(
                          workspaceId,
                          `projects/${projectId}/journeys/${version.journeyVersionId}`,
                        )}
                      >
                        {version.name}
                      </Link>
                    ),
                  },
                  { key: 'platform', header: 'Platform', cell: (version) => version.platform },
                  {
                    key: 'digest',
                    header: 'Journey digest',
                    cell: (version) => <code>{version.journeyDigest.slice(0, 16)}…</code>,
                  },
                  {
                    key: 'lineage',
                    header: 'Lineage',
                    cell: (version) =>
                      version.supersededBy === null ? (
                        <StatusBadge tone="pass" kind="Version">
                          Current
                        </StatusBadge>
                      ) : (
                        <StatusBadge tone="neutral" kind="Version">
                          Superseded
                        </StatusBadge>
                      ),
                  },
                  {
                    key: 'created',
                    header: 'Frozen',
                    cell: (version) => (
                      <time dateTime={version.createdAt}>{version.createdAt}</time>
                    ),
                  },
                ]}
              />
            )
          }
        </ResourceView>
      </section>

      <JourneyAuthoringSection
        workspaceId={workspaceId}
        projectId={projectId}
        onFrozen={() => setFreezeCount((current) => current + 1)}
      />
    </>
  )
}
