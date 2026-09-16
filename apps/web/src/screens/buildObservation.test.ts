import { describe, expect, it } from 'vitest'
import { OBSERVATION_MAX_LENGTH, parseBuildObservation, parseDirtyPaths } from './buildObservation'

const observation = { commitSha: 'a'.repeat(40), treeDigest: 'b'.repeat(64), artifactDigest: 'c'.repeat(64),
  requestedRevision: 'HEAD', dirty: true, dirtyPaths: [' space ', 'line\nbreak'], identityObservable: false }

describe('offline build observation', () => {
  it('preserves exact path strings without interpreting JSON as authority', () => {
    expect(parseBuildObservation(JSON.stringify(observation)).dirtyPaths).toEqual(observation.dirtyPaths)
    expect(parseDirtyPaths('[]')).toEqual([])
  })
  it.each([
    { identityObservable: true }, { identityObservable: 'false' }, { dirty: false }, { dirty: 'true' },
    { dirtyPaths: ['same', 'same'] }, { dirtyPaths: [''] }, { dirtyPaths: ['bad\0path'] },
    { commitSha: 'fake' }, { treeDigest: 123 }, { artifactDigest: 'C'.repeat(64) },
    { requestedRevision: ' ' }, { unexpected: 'field' }, { dirtyPaths: null },
  ])('refuses inconsistent or malformed observations: %j', (change) => {
    expect(() => parseBuildObservation(JSON.stringify({ ...observation, ...change }))).toThrow()
  })
  it('refuses invalid JSON, non-objects, missing fields and oversized input', () => {
    for (const raw of ['{', 'null', '[]', '{}', ' '.repeat(OBSERVATION_MAX_LENGTH + 1)])
      expect(() => parseBuildObservation(raw)).toThrow()
  })
})
