import { useState, type JSX } from 'react'
import { getPatchComparison, type PatchComparison, type SourceSide } from '../api/patchComparison'
import type { Patch } from '../api/patches'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { ResourceView } from '../components/ResourceView'
import { useSession } from '../session/SessionProvider'

const escaped = (text: string): string => JSON.stringify(text).replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,
  (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`)

export const PatchComparisonSection = ({ workspaceId, patch }: { readonly workspaceId: string; readonly patch: Patch }): JSX.Element => {
  const { client } = useSession()
  const resource = useResource((signal) => getPatchComparison(client, workspaceId, patch, signal), [client, workspaceId, patch.patchId, patch.patchDigest, patch.revision])
  return <section className="af-stack"><h2>Original-source comparison</h2>
    <p>A retained comparison comes from the authorized original Git commit, not an uploaded before-file. It is not application, approval or verification. Read again to check for retirement; this view does not poll.</p>
    {resource.state.kind === 'problem' && resource.state.problem.status === 404
      ? <p>No accessible retained comparison is available. A trusted operator must prepare it; inspect the exact base separately before approving. No source is inferred from the replacement text below.</p>
      : <ResourceView resource={resource} what="the retained original-source comparison">{(record) =>
        record.retiredAt !== null || record.comparison === null
          ? <p>The retained source copy was retired at {record.retiredAt ?? 'an unavailable time'}. It is not shown or recreated. Repository files and proposal bytes are unchanged.</p>
          : <ComparisonFiles key={record.comparisonDigest} record={record} />
      }</ResourceView>}
    <Button onClick={resource.reload}>Read original-source comparison again</Button>
  </section>
}

const ComparisonFiles = ({ record }: { readonly record: PatchComparison }): JSX.Element | null => {
  const [index, setIndex] = useState(0)
  const comparison = record.comparison
  if (comparison === null) return null
  const file = comparison.files[index]!
  return <div className="af-stack">
    <dl><dt>Comparison ID</dt><dd><code>{record.comparisonId}</code></dd>
      <dt>Comparison digest</dt><dd><code>{record.comparisonDigest}</code></dd>
      <dt>Original commit</dt><dd><code>{comparison.baseCommitSha}</code></dd>
      <dt>Original archive digest</dt><dd><code>{comparison.baseArchiveDigest}</code></dd>
      <dt>Prepared proposal revision (not current approval authority)</dt><dd>{comparison.patchRevision}</dd>
      <dt>Prepared by</dt><dd>{record.preparedBy}</dd><dt>Recorded (UTC)</dt><dd><time dateTime={record.recordedAt}>{record.recordedAt}</time></dd></dl>
    <label>Comparison file<select value={index} onChange={(event) => setIndex(Number(event.target.value))}>
      {comparison.files.map((f, i) => <option key={f.path} value={i}>{JSON.stringify(f.path)} — {f.operation}</option>)}
    </select></label>
    <p>{file.operation}. {file.changed ? 'Content or file mode changes.' : 'No content or file-mode change.'}</p>
    <label>Unified diff for {JSON.stringify(file.path)}<textarea readOnly rows={14} spellCheck={false} value={file.unifiedDiff} /></label>
    <p>Minus lines are original; plus lines are proposed. File-mode changes and missing final line endings are explicit. Text controls can normalize line endings; use the escaped source alternatives for exact characters.</p>
    <details><summary>Plain original and proposed source alternatives</summary>
      <SourceText side={file.before} label="Original" path={file.path} />
      <SourceText side={file.after} label="Proposed" path={file.path} />
    </details>
  </div>
}
const SourceText = ({ side, label, path }: { readonly side: SourceSide | null; readonly label: string; readonly path: string }): JSX.Element =>
  <section className="af-stack"><h3>{label} source</h3>{side === null ? <p>File absent in this side of the comparison.</p> : <>
    <p>Mode <code>{side.mode}</code>; {side.byteLength} UTF-8 bytes; SHA-256 <code>{side.sha256}</code>.</p>
    <label>{label} text for {JSON.stringify(path)}<textarea readOnly rows={10} value={side.text} spellCheck={false} /></label>
    <details><summary>{label} escaped text and exact line endings</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{escaped(side.text)}</pre></details>
  </>}</section>
