import { useState, type JSX } from 'react'
import { readRepairOptions, type RepairOptions, type RepairScope } from '../api/repairRequests'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { ResourceView } from '../components/ResourceView'
import { useSession } from '../session/SessionProvider'

export const RepairRequestForm = ({ workspaceId, findingId, diagnosisId, busy, submit }: {
  readonly workspaceId: string; readonly findingId: string; readonly diagnosisId: string
  readonly busy: boolean; readonly submit: (scope: RepairScope) => void
}): JSX.Element => {
  const { client } = useSession()
  const options = useResource((signal) => readRepairOptions(client, workspaceId, findingId, diagnosisId, signal),
    [client, workspaceId, findingId, diagnosisId])
  return <ResourceView resource={options} what="the current repair disclosure scope">{(value) =>
    <Consent key={JSON.stringify(value)} options={value} busy={busy} submit={submit} />
  }</ResourceView>
}

const Consent = ({ options, busy, submit }: {
  readonly options: RepairOptions; readonly busy: boolean; readonly submit: (scope: RepairScope) => void
}): JSX.Element => {
  const [billable, setBillable] = useState(false)
  const [separate, setSeparate] = useState(false)
  const needsSeparate = options.separatelyReviewedPaths.length > 0
  const ready = billable && (!needsSeparate || separate) && !busy
  return <form className="af-stack" onSubmit={(event) => {
    event.preventDefault()
    if (ready) submit({ ...options.scope, billableCallAcknowledged: true, separateReviewAcknowledged: separate })
  }}>
    <h3>Review repair disclosure and scope</h3>
    <p>{options.disclosure}</p>
    <p>The complete allowed files—not just diagnosis excerpts—may be disclosed. This preview is metadata, not proof that original source and evidence are ready for generation.</p>
    <h4>Full source files in scope</h4>
    <ul>{options.sourcePaths.map((path) => <li key={path}><code>{JSON.stringify(path)}</code></li>)}</ul>
    <h4>Provider and limits</h4>
    <dl>{Object.entries(options.profile).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl>
    <p>Previous request: <code>{options.scope.supersedes ?? 'None — first repair request'}</code>. The server checks whether it is safe to create a successor; this is not a retry of that request.</p>
    <details><summary>Exact source, diagnosis and evaluation identities</summary>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(options.scope, null, 2)}</pre>
    </details>
    {needsSeparate && <>
      <h4>Dependency or build files requiring separate review</h4>
      <ul>{options.separatelyReviewedPaths.map((path) => <li key={path}><code>{JSON.stringify(path)}</code></li>)}</ul>
      <label><input type="checkbox" checked={separate} disabled={busy} onChange={(event) => setSeparate(event.target.checked)} />{' '}
        I acknowledge this separately reviewed dependency or build scope. This does not approve any generated patch.</label>
    </>}
    <label><input type="checkbox" checked={billable} disabled={busy} onChange={(event) => setBillable(event.target.checked)} />{' '}
      I authorize disclosure of these complete allowed source files and retained diagnosis to this provider, including potential provider charges.</label>
    <p>This stores a one-hour request for separate operator dispatch. It does not call the model, start a reader, approve or apply a patch, or establish verification.</p>
    <Button type="submit" busy={busy} disabled={!ready}>Record repair request</Button>
  </form>
}
