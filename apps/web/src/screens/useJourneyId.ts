import { useParams } from 'react-router-dom'

/** The journey version from the path. */
export const useJourneyId = (): string => {
  const { journeyId } = useParams()
  if (journeyId === undefined) {
    throw new Error('this screen is only mounted below /journeys/:journeyId')
  }
  return journeyId
}
