import { useEffect, useRef, useState, type JSX } from 'react'
import { readEffectRecovery, type DeliveryPhase, type EffectDelivery } from '../api/effectRecovery'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { membershipFor, useSession } from '../session/SessionProvider'

const PHASE: Record<DeliveryPhase, string> = {
  OPEN: 'Permission was open at observation time. This is not permission to act now.',
  CLOSED_UNUSED: 'Permission expired without a recorded consumption. This does not prove zero application effects.',
  UNCONFIRMED: 'Permission was consumed, but no transport response was retained. Investigate before any further action.',
  DELIVERY_RECORD_MISSING: 'Permission was consumed, but its delivery record is missing. Investigate the incomplete history.',
  RESPONSE_RETAINED: 'A transport response was retained. This does not establish application success or journey completion.',
}

export const EffectRecoverySection = ({ workspaceId, runId }: {
  readonly workspaceId: string; readonly runId: string
}): JSX.Element | null => {
  const { state } = useSession()
  const membership = membershipFor(state, workspaceId)
  if (state.status !== 'authenticated' || membership === null) return null
  // Hide old tenant/actor/role data during navigation, before the next read's effect runs.
  return <RecoveryBrowser key={`${state.userId}:${membership.role}:${workspaceId}:${runId}`}
    workspaceId={workspaceId} runId={runId} />
}

const RecoveryBrowser = ({ workspaceId, runId }: { readonly workspaceId: string; readonly runId: string }): JSX.Element => {
  const [page, setPage] = useState({ after: null as string | null, number: 1, generation: 0 })
  return <section className="af-stack af-effect-recovery" aria-label="Form transport recovery history">
    <h2>Form transport recovery history</h2>
    <Notice tone="information" heading="Read-only history, not effect proof" headingLevel={3}>
      <p>These records do not authorize a retry, reset, lease release or new dispatch. A response is not a successful task.</p>
      <p>Pages are ordered by permission ID, not event time. Each page is a separate observation; earlier pages can change while a run is active. Start again to re-read them.</p>
    </Notice>
    <RecoveryPage key={page.generation} workspaceId={workspaceId} runId={runId} after={page.after}
      pageNumber={page.number} focusHeading={page.generation > 0}
      next={(after) => setPage((p) => ({ after, number: p.number + 1, generation: p.generation + 1 }))}
      restart={() => setPage((p) => ({ after: null, number: 1, generation: p.generation + 1 }))} />
  </section>
}

const RecoveryPage = ({ workspaceId, runId, after, pageNumber, focusHeading, next, restart }: {
  readonly workspaceId: string; readonly runId: string; readonly after: string | null
  readonly pageNumber: number; readonly focusHeading: boolean
  readonly next: (cursor: string) => void; readonly restart: () => void
}): JSX.Element => {
  const { client } = useSession()
  const heading = useRef<HTMLHeadingElement>(null)
  const report = useResource((signal) => readEffectRecovery(client, workspaceId, runId, after, signal), [client, workspaceId, runId, after])
  useEffect(() => { if (focusHeading) heading.current?.focus() }, [focusHeading])
  return <div className="af-stack">
    <h3 ref={heading} tabIndex={-1}>Transport history page {pageNumber}</h3>
    <div className="af-row">
      <Button busy={report.state.kind === 'loading'} onClick={report.reload}>Refresh this history page</Button>
      {after !== null && <Button onClick={restart}>Start history again</Button>}
    </div>
    <ResourceView resource={report} what="read-only form transport history">
      {(value) => <>
        <p role="status">Page {pageNumber}: {value.items.length} records. Observed <time dateTime={value.observedAt}>{value.observedAt}</time>.</p>
        <p>Run status at observation: {value.runStatus}. Recorded outcome: {value.runOutcome}. Run quarantined: {value.runQuarantined ? 'Yes' : 'No'}.</p>
        {value.items.length === 0
          ? <p>No permission records on this page. This does not prove that no application effects occurred.</p>
          : <ul className="af-stack">{value.items.map((item) => <li key={item.permitId} className="af-panel"><Delivery item={item} /></li>)}</ul>}
        {value.nextCursor === null ? <p>End of this history page sequence as observed. This is not an effect-completeness assessment.</p>
          : <Button onClick={() => next(value.nextCursor!)}>Read next history page</Button>}
      </>}
    </ResourceView>
  </div>
}

const Delivery = ({ item }: { readonly item: EffectDelivery }): JSX.Element => <article className="af-stack">
  <h4>Action {item.actionSequence}: {item.action}</h4>
  <p>Transport phase: <strong>{item.phase}</strong>. {PHASE[item.phase]}</p>
  {item.requiresInvestigation && <p><strong>Investigation required.</strong> Do not automatically resend this action.</p>}
  <p>Recorded action result: {item.actionResult ?? 'Not recorded'}. HTTP response: {item.responseStatus ?? 'Not retained'}.</p>
  <p>Runner quarantined: {item.runnerQuarantined ? 'Yes' : 'No'}. Endpoint state: {item.endpointState ?? 'Not recorded'}.
    {' '}Endpoint cleanup confirmed: {item.endpointCleanupConfirmed === null ? 'Unknown' : item.endpointCleanupConfirmed ? 'Yes' : 'No'}.</p>
  <p>Lease released: {item.leaseReleasedAt ?? 'Not recorded'}. Stop acknowledged: {item.stopAcknowledgedAt ?? 'Not recorded'}.</p>
  <details>
    <summary>Original identities and timestamps for action {item.actionSequence}</summary>
    <dl>
      <dt>Permission ID</dt><dd><code>{item.permitId}</code></dd>
      <dt>Action ID</dt><dd><code>{item.actionId}</code></dd>
      <dt>Attempt ID</dt><dd><code>{item.attemptId}</code></dd>
      <dt>Lease ID</dt><dd><code>{item.leaseId}</code></dd>
      <dt>Granted</dt><dd>{item.grantedAt}</dd>
      <dt>Expired / expires</dt><dd>{item.expiresAt}</dd>
      <dt>Consumed</dt><dd>{item.consumedAt ?? 'Not recorded'}</dd>
      <dt>Response recorded</dt><dd>{item.responseRecordedAt ?? 'Not recorded'}</dd>
      <dt>Action result recorded</dt><dd>{item.actionResultAt ?? 'Not recorded'}</dd>
      <dt>Request digest</dt><dd><code>{item.requestDigest ?? 'Not recorded'}</code></dd>
      <dt>Response digest</dt><dd><code>{item.responseDigest ?? 'Not retained'}</code></dd>
    </dl>
  </details>
</article>
