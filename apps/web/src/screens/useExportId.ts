import { useParams } from 'react-router-dom'

/** The export from the path. */
export const useExportId = (): string => {
  const { exportId } = useParams()
  if (exportId === undefined) {
    throw new Error('this screen is only mounted below /exports/:exportId')
  }
  return exportId
}
