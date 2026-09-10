import { useParams } from 'react-router-dom'

/** The review from the path. `new` opens the queue rather than a record. */
export const useReviewId = (): string => {
  const { reviewId } = useParams()
  if (reviewId === undefined) {
    throw new Error('this screen is only mounted below /reviews/:reviewId')
  }
  return reviewId
}
