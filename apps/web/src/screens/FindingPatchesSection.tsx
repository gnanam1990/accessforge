import { useState, type JSX } from 'react'
import { Link } from 'react-router-dom'
import { listPatches } from '../api/patches'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { ResourceView } from '../components/ResourceView'
import { useSession } from '../session/SessionProvider'

export const FindingPatchesSection = ({ workspaceId, findingId }: { readonly workspaceId: string; readonly findingId: string }): JSX.Element => {
  const [open, setOpen] = useState(false)
  return <section className="af-stack"><h2>Proposed repairs</h2>
    <Button aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Close repair history' : 'Inspect proposed repairs'}</Button>
    {open && <PatchList workspaceId={workspaceId} findingId={findingId} />}
  </section>
}
const PatchList = ({ workspaceId, findingId }: { readonly workspaceId: string; readonly findingId: string }): JSX.Element => {
  const { client } = useSession()
  const resource = useResource((signal) => listPatches(client, workspaceId, findingId, signal), [client, workspaceId, findingId])
  return <ResourceView resource={resource} what="this finding's proposed repairs">{(patches) => patches.length === 0 ? <p>No repair has been proposed for this finding.</p> :
    <ul>{patches.map((p) => <li key={p.patchId}>
      <Link className="af-link" to={`/w/${encodeURIComponent(workspaceId)}/patches/${encodeURIComponent(p.patchId)}`}>Inspect patch {p.patchId}</Link> — {p.status}, revision {p.revision}
    </li>)}</ul>}
  </ResourceView>
}
