import { useParams } from 'react-router-dom'

/** The finding from the path. */
export const useFindingId = (): string => {
  const { findingId } = useParams()
  if (findingId === undefined) {
    throw new Error('this screen is only mounted below /findings/:findingId')
  }
  return findingId
}
