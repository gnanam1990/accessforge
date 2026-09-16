/** Offline observations are declarations, never deployment attestations. */
export const OBSERVATION_MAX_LENGTH = 131072

export function parseDirtyPaths(raw: string): string[] {
  if (raw.length > OBSERVATION_MAX_LENGTH) throw new Error('Changed paths exceed the 128 Ki-character input limit.')
  let value: unknown
  try { value = JSON.parse(raw) } catch { throw new Error('Changed source paths must be a JSON array, such as ["src/form.tsx"], or [] for a clean tree.') }
  if (!Array.isArray(value) || value.some((path: unknown) => typeof path !== 'string' || path.length === 0 || path.includes('\0')) ||
    new Set(value).size !== value.length) throw new Error('Changed source paths must contain unique, nonempty strings without NUL characters.')
  return value as string[]
}

export function parseBuildObservation(raw: string) {
  if (raw.length > OBSERVATION_MAX_LENGTH) throw new Error('Observation exceeds the 128 Ki-character input limit.')
  let value: unknown
  try { value = JSON.parse(raw) } catch { throw new Error('Paste valid JSON from the offline build-observation command.') }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('Observation must be a JSON object.')
  const data = value as Record<string, unknown>
  const keys = ['commitSha', 'treeDigest', 'artifactDigest', 'requestedRevision', 'dirty', 'dirtyPaths', 'identityObservable']
  if (Object.keys(data).length !== keys.length || keys.some((key) => !Object.hasOwn(data, key)))
    throw new Error('Observation must contain exactly the seven fields emitted by the offline command.')
  const { commitSha, treeDigest, artifactDigest, requestedRevision } = data
  if (typeof commitSha !== 'string' || !/^[a-f0-9]{40}$/.test(commitSha) ||
    typeof treeDigest !== 'string' || !/^[a-f0-9]{64}$/.test(treeDigest) ||
    typeof artifactDigest !== 'string' || !/^[a-f0-9]{64}$/.test(artifactDigest))
    throw new Error('Observation requires a full lowercase commit SHA and SHA-256 source and artifact digests.')
  if (typeof requestedRevision !== 'string' || !requestedRevision.trim() || requestedRevision.includes('\0'))
    throw new Error('Observation requires a nonempty requested source revision without NUL characters.')
  const dirtyPaths = parseDirtyPaths(JSON.stringify(data['dirtyPaths']))
  if (data['dirty'] !== (dirtyPaths.length > 0)) throw new Error('The dirty flag must agree with the changed source paths.')
  if (data['identityObservable'] !== false) throw new Error('Offline observations must set identityObservable to false. Confirm target identity separately after import.')
  return { commitSha, treeDigest, artifactDigest, requestedRevision, dirtyPaths }
}
