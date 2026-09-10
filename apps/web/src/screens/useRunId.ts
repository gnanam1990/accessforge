import { useParams } from 'react-router-dom'

/** The run from the path. */
export const useRunId = (): string => {
  const { runId } = useParams()
  if (runId === undefined) throw new Error('this screen is only mounted below /runs/:runId')
  return runId
}
