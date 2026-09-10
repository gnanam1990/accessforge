import { useParams } from 'react-router-dom'

/** The project from the path. Same reasoning as `useWorkspaceId`: the URL is the source. */
export const useProjectId = (): string => {
  const { projectId } = useParams()
  if (projectId === undefined) {
    throw new Error('this screen is only mounted below /projects/:projectId')
  }
  return projectId
}
