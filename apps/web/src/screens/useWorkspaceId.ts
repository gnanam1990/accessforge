/**
 * The workspace from the path.
 *
 * From the path and nowhere else. Authority comes from the authenticated session and the route; a
 * workspace held in a context and carried between screens is one that can drift out of step with the
 * URL a person is actually looking at, and the server would then be answering about a different
 * tenant from the one on screen.
 */

import { useParams } from 'react-router-dom'

export const useWorkspaceId = (): string => {
  const { workspaceId } = useParams()
  if (workspaceId === undefined) {
    throw new Error('this screen is only mounted below /w/:workspaceId')
  }
  return workspaceId
}
