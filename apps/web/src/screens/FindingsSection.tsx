import type { JSX } from 'react'
import { Link } from 'react-router-dom'
import { listFindings } from '../api/resources'
import { useResource } from '../api/useResource'
import { DataTable } from '../components/DataTable'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { workspacePath } from '../routes/routeMap'
import { useSession } from '../session/SessionProvider'

/** Read-only entry point to existing findings; opening this never generates a diagnosis. */
export const FindingsSection = ({ workspaceId }: { readonly workspaceId: string }): JSX.Element => {
  const { client } = useSession()
  const findings = useResource(
    (signal) => listFindings(client, workspaceId, signal), [client, workspaceId],
  )
  return (
    <section className="af-stack">
      <h2>Findings</h2>
      <ResourceView resource={findings} what="this workspace's findings">
        {(page) => <>
          {!page.complete && <Notice tone="warning" heading="Finding list is not complete" headingLevel={3}>
            <p>The server has more findings than this bounded view could retrieve.</p>
          </Notice>}
          {page.items.length === 0 ? <p>No findings were returned for this workspace.</p> : <DataTable
            caption="Findings with their original evidence, model analysis and human assessments"
            rows={page.items}
            rowKey={(item) => item.findingId}
            columns={[
              { key: 'finding', header: 'Finding', isRowHeader: true, cell: (item) =>
                <Link className="af-link" to={workspacePath(workspaceId, `findings/${encodeURIComponent(item.findingId)}`)}>
                  {item.summary} ({item.findingId.slice(0, 8)})
                </Link> },
              { key: 'status', header: 'Finding status', cell: (item) => item.status },
              { key: 'assertion', header: 'Assertion', cell: (item) => item.assertionId },
              { key: 'run', header: 'Run', cell: (item) =>
                <Link className="af-link" to={workspacePath(workspaceId, `runs/${encodeURIComponent(item.runId)}`)}>
                  {item.runId}
                </Link> },
            ]}
          />}
        </>}
      </ResourceView>
    </section>
  )
}
