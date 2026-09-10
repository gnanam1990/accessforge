/**
 * Choosing which workspace to work in.
 *
 * A `<select>` with a real `<label>`, not a custom listbox. A native select brings type-ahead, the
 * platform's own picker on a touch device, and correct announcement, and this is a case where none
 * of that is worth reimplementing for a visual preference.
 *
 * Switching is a **navigation**, so it goes through the router. That matters more than it looks: the
 * route change is what unmounts the previous workspace's screens, which is what discards their
 * state. A switcher that only updated a context value would leave the previous tenant's components
 * mounted with their data still in them.
 *
 * The list contains only workspaces the server said this person is a member of, and it is re-read
 * rather than remembered.
 */

import type { JSX } from 'react'

import { useNavigate } from 'react-router-dom'

import type { WorkspaceMembership } from '../api/session'
import { workspacePath } from '../routes/routeMap'

export interface WorkspaceSwitcherProps {
  readonly workspaces: readonly WorkspaceMembership[]
  readonly current: string | null
  readonly onSwitch: (workspaceId: string) => void
}

export const WorkspaceSwitcher = ({
  workspaces,
  current,
  onSwitch,
}: WorkspaceSwitcherProps): JSX.Element | null => {
  const navigate = useNavigate()
  if (workspaces.length === 0) return null

  return (
    <div className="af-field" style={{ margin: 0 }}>
      <label htmlFor="af-workspace-switcher">Workspace</label>
      <select
        id="af-workspace-switcher"
        value={current ?? ''}
        onChange={(event) => {
          const next = event.target.value
          if (next === '' || next === current) return
          onSwitch(next)
          // Straight to the workspace's own overview rather than the equivalent path in the new
          // workspace. The same path in another tenant is a different resource, and following it
          // would produce a 404 that looks like the switch failed.
          void navigate(workspacePath(next), { replace: false })
        }}
      >
        {current === null && <option value="">Choose a workspace</option>}
        {workspaces.map((workspace) => (
          <option key={workspace.workspaceId} value={workspace.workspaceId}>
            {workspace.name} ({workspace.role.toLowerCase()})
          </option>
        ))}
      </select>
    </div>
  )
}
