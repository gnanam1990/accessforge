import type { JSX } from 'react'
import { getRunEvaluation, type Run } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'

export const RunEvaluationSection = ({ workspaceId, run }: {
  readonly workspaceId: string
  readonly run: Run
}): JSX.Element => {
  // Mount the reading component only for completed runs. No premature verdict or polling loop.
  return run.status === 'COMPLETED'
    ? <RetainedEvaluation key={`${workspaceId}:${run.runId}:${run.revision}`} workspaceId={workspaceId} run={run} />
    : <section className="af-stack"><h2>Assertion results</h2>
      <p>No final evaluation is available for this run state. Running, interrupted or cancelled is not a per-assertion result.</p>
    </section>
}

const RetainedEvaluation = ({ workspaceId, run }: { readonly workspaceId: string; readonly run: Run }): JSX.Element => {
  const { client } = useSession()
  const evaluation = useResource(
    (signal) => getRunEvaluation(client, workspaceId, run.runId, signal),
    [client, workspaceId, run.runId],
  )
  if (evaluation.state.kind === 'problem' && evaluation.state.problem.status === 404) {
    return <section className="af-stack"><h2>Assertion results</h2>
      <Notice tone="warning" heading="No retained evaluation available" headingLevel={3}>
        <p>No original evaluation snapshot is available to this request. Older runs may not have one.
          The run outcome is not being copied into invented per-assertion results.</p>
      </Notice>
      <Button onClick={evaluation.reload}>Read evaluation again</Button>
    </section>
  }
  return <section className="af-stack">
    <h2>Assertion results</h2>
    <ResourceView resource={evaluation} what="the original evaluation snapshot">
      {(result) => {
        const snapshot = result.snapshot
        if (snapshot.manifestDigest !== run.manifestDigest || snapshot.outcome !== run.outcome) {
          return <Notice tone="problem" heading="Evaluation identity differs" headingLevel={3}>
            <p>The retained snapshot does not match this run's manifest or recorded outcome. No assertion values are displayed. Reload the run to reconcile these records.</p>
          </Notice>
        }
        const kinds = [...new Set([...Object.keys(snapshot.sealedIdentities), ...Object.keys(snapshot.observedIdentities)])].sort()
        return <>
          <Notice tone="information" heading="Original evaluation, not a fresh verification" headingLevel={3}>
            <p>Recorded <time dateTime={result.recordedAt}>{result.recordedAt}</time> by evaluator {snapshot.evaluatorVersion}.</p>
            <p>This immutable snapshot describes attempt <code>{snapshot.attemptId}</code>. Changing the attempt selected below does not change this evaluation.</p>
            <p>It does not establish that artifact bytes are still available. The evidence-completeness section and private export assess current retention separately.</p>
          </Notice>
          <section className="af-stack">
            <h3>Recorded outcome reasons</h3>
            {snapshot.reasons.length === 0 ? <p>No outcome reasons were recorded.</p> :
              <ul>{snapshot.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
            <p>{snapshot.scope}</p>
          </section>
          {snapshot.assertions.length === 0 ? <p>No per-assertion values were retained in this snapshot.</p> :
            <DataTable caption="Frozen assertion values and their evidence sources" rows={snapshot.assertions}
              rowKey={(row) => row.assertionId} columns={[
                { key: 'id', header: 'Assertion', isRowHeader: true, cell: (row) => <><code>{row.assertionId}</code><p>{row.kind}</p></> },
                { key: 'value', header: 'Recorded condition', cell: (row) => <StatusBadge kind="Condition"
                  tone={row.condition === 'TRUE' ? 'pass' : row.condition === 'FALSE' ? 'fail' : 'inconclusive'}>{row.condition}</StatusBadge> },
                { key: 'source', header: 'Provenance', cell: (row) => row.provenance },
                { key: 'reason', header: 'Unknown reason', cell: (row) => row.unknownReason ?? 'Not recorded' },
                { key: 'refs', header: 'Canonical event references', cell: (row) => row.evidenceRefs.length === 0
                  ? 'No evidence reference recorded' : <ul>{row.evidenceRefs.map((ref) => <li key={ref}><code>{ref}</code></li>)}</ul> },
              ]} />}
          <p className="af-secondary">TRUE/FALSE/UNKNOWN are recorded assertion conditions, not additional run verdicts. Event references belong to the evaluated attempt; they are not download links.</p>
          <details>
            <summary>Compare sealed and observed identities</summary>
            <DataTable caption="Identities retained at evaluation time" rows={kinds} rowKey={(kind) => kind} columns={[
              { key: 'kind', header: 'Identity', isRowHeader: true, cell: (kind) => kind },
              { key: 'sealed', header: 'Sealed value', cell: (kind) => <code>{snapshot.sealedIdentities[kind] ?? 'Not sealed'}</code> },
              { key: 'observed', header: 'Observed value', cell: (kind) => <code>{snapshot.observedIdentities[kind] ?? 'Not observed'}</code> },
            ]} />
            <p className="af-secondary">A sealed value is an expectation, not an observed runtime identity. This comparison does not recompute the verdict.</p>
          </details>
          <details>
            <summary>Snapshot and artifact identities</summary>
            <dl>
              <dt>Evaluation ID</dt><dd><code>{result.evaluationId}</code></dd>
              <dt>Snapshot digest</dt><dd><code>{result.snapshotDigest}</code></dd>
              <dt>Evidence-set digest</dt><dd><code>{snapshot.evidenceSetDigest}</code></dd>
              <dt>Manifest digest</dt><dd><code>{snapshot.manifestDigest}</code></dd>
            </dl>
            <ul>{snapshot.artifacts.map((artifact) => <li key={artifact.artifactId}>
              <p>{artifact.kind} — <code>{artifact.artifactId}</code></p>
              <p>Producer: <code>{artifact.producerId}</code></p>
              <p>Content digest: <code>{artifact.digest}</code></p>
            </li>)}</ul>
          </details>
          <Button onClick={evaluation.reload}>Read evaluation again</Button>
        </>
      }}
    </ResourceView>
  </section>
}
