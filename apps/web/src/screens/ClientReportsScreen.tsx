/**
 * Tester Desk · client reports: the redacted projection, exactly as generated.
 *
 * What the client sees is what the report *body* says — tester identity, payout routes,
 * credentials and original filenames never existed in the projection, so there is nothing
 * here to hide or mask at render time (INV-06).
 */

import { useState } from 'react'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { DataTable } from '../components/DataTable'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { listReports } from '../api/testerDesk'
import type { Report } from '../api/testerDesk'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'

const RESULT_TONE = { PASSED: 'pass', FAILED: 'fail', UNREACHABLE: 'fail', INCONCLUSIVE: 'inconclusive' } as const

export const ClientReportsScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client } = useSession()
  const reports = useResource((s) => listReports(client, workspaceId, s), [client, workspaceId])
  const [open, setOpen] = useState<string | null>(null)

  return (
    <div className="af-stack">
      <header><RouteHeading>Test reports</RouteHeading>
        <p className="af-secondary">Redacted accessibility retest reports for your releases.</p>
      </header>
      <ResourceView resource={reports} what="reports">{(value) => (
        value.items.length === 0 ? <p className="af-secondary">No reports yet.</p>
          : value.items.map((r) => (
            <article key={r.reportId} className="af-card af-stack" aria-label={`Report ${r.releaseLabel} r${r.revision}`}>
              <header>
                <h3>
                  Release {r.releaseLabel} · report r{r.revision} · task result{' '}
                  <StatusBadge tone={RESULT_TONE[r.body.taskResult as keyof typeof RESULT_TONE] ?? 'neutral'} kind="Task result">
                    {r.body.taskResult}
                  </StatusBadge>
                </h3>
                <p className="af-secondary">
                  {r.body.environment.assistiveTechnology} on {r.body.environment.browserName}{' '}
                  {r.body.environment.browserVersion} · generated {r.generatedAt.slice(0, 16)}
                </p>
              </header>
              <div>
                <button type="button" className="af-button"
                  aria-expanded={open === r.reportId}
                  onClick={() => setOpen(open === r.reportId ? null : r.reportId)}>
                  {open === r.reportId ? 'Hide findings' : 'Show findings'}
                </button>
              </div>
              {open === r.reportId && (
                <>
                  <DataTable<Report['body']['observations'][number]>
                    caption={`Observations for release ${r.releaseLabel}`}
                    rowKey={(o) => o.stepId}
                    columns={[
                      { key: 's', header: 'Step', isRowHeader: true, cell: (o) => o.stepTitle ?? o.stepId },
                      {
                        key: 'r', header: 'Result',
                        cell: (o) => <StatusBadge tone={RESULT_TONE[o.result as keyof typeof RESULT_TONE] ?? 'neutral'} kind="Result">{o.result}</StatusBadge>,
                      },
                      { key: 'n', header: 'Observation', cell: (o) => o.notes ?? '—' },
                    ]}
                    rows={r.body.observations}
                  />
                  {r.body.attachments.length > 0 && (
                    <p className="af-secondary">
                      Attachments: {r.body.attachments.map((a) => `${a.handle} (${a.kind})`).join(', ')}
                    </p>
                  )}
                  <p className="af-secondary"><code>report {r.reportHash.slice(0, 16)}…</code></p>
                </>
              )}
            </article>
          ))
      )}</ResourceView>
    </div>
  )
}
