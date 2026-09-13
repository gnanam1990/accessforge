/** Original server evaluation, not a client-side rerun of the verdict algorithm. */
export interface EvaluatedAssertion {
  readonly assertionId: string
  readonly kind: string
  readonly condition: 'TRUE' | 'FALSE' | 'UNKNOWN'
  readonly provenance: 'OBSERVER_AUTHORED' | 'EVALUATOR_DERIVED' | 'ABSENT'
  readonly evidenceRefs: readonly string[]
  readonly unknownReason: string | null
}

export interface RunEvaluation {
  readonly evaluationId: string
  readonly snapshotDigest: string
  readonly recordedAt: string
  readonly meaning: 'ORIGINAL_EVALUATION_SNAPSHOT'
  readonly snapshot: {
    readonly schemaVersion: 1
    readonly runId: string
    readonly attemptId: string
    readonly manifestDigest: string
    readonly evidenceSetDigest: string
    readonly evaluatorVersion: string
    readonly outcome: 'PASS' | 'FAIL' | 'INCONCLUSIVE'
    readonly reasons: readonly string[]
    readonly scope: string
    readonly sealedIdentities: Readonly<Record<string, string>>
    readonly observedIdentities: Readonly<Record<string, string>>
    readonly assertions: readonly EvaluatedAssertion[]
    readonly artifacts: readonly {
      readonly artifactId: string
      readonly kind: string
      readonly producerId: string
      readonly digest: string
    }[]
  }
}

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const hash = (value: unknown): boolean => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text)
const identities = (value: unknown): boolean => object(value) && Object.values(value).every(text)

/** Validate display shape and run binding. The API, not this parser, checks the canonical digest. */
export function parseRunEvaluation(value: unknown, runId: string): RunEvaluation | null {
  if (!object(value) || !text(value.evaluationId) || !hash(value.snapshotDigest) ||
      value.meaning !== 'ORIGINAL_EVALUATION_SNAPSHOT' || !text(value.recordedAt) ||
      !Number.isFinite(Date.parse(value.recordedAt)) || !object(value.snapshot)) return null
  const snapshot = value.snapshot
  if (snapshot.schemaVersion !== 1 || snapshot.runId !== runId || !text(snapshot.attemptId) ||
      !hash(snapshot.manifestDigest) || !hash(snapshot.evidenceSetDigest) || !text(snapshot.evaluatorVersion) ||
      !text(snapshot.outcome) || !['PASS', 'FAIL', 'INCONCLUSIVE'].includes(snapshot.outcome) ||
      !strings(snapshot.reasons) || !text(snapshot.scope) || !identities(snapshot.sealedIdentities) ||
      !identities(snapshot.observedIdentities) || !Array.isArray(snapshot.assertions) ||
      !Array.isArray(snapshot.artifacts)) return null
  const seen = new Set<string>()
  for (const assertion of snapshot.assertions) {
    if (!object(assertion) || !text(assertion.assertionId) || seen.has(assertion.assertionId) ||
        !text(assertion.kind) || !text(assertion.condition) || !['TRUE', 'FALSE', 'UNKNOWN'].includes(assertion.condition) ||
        !text(assertion.provenance) || !['OBSERVER_AUTHORED', 'EVALUATOR_DERIVED', 'ABSENT'].includes(assertion.provenance) ||
        !strings(assertion.evidenceRefs) || !(assertion.unknownReason === null || text(assertion.unknownReason)) ||
        (assertion.condition === 'UNKNOWN' && !text(assertion.unknownReason)) ||
        (assertion.condition !== 'UNKNOWN' && assertion.evidenceRefs.length === 0) ||
        (assertion.provenance === 'ABSENT' && assertion.condition !== 'UNKNOWN')) return null
    seen.add(assertion.assertionId)
  }
  const artifactIds = new Set<string>()
  for (const artifact of snapshot.artifacts) {
    if (!object(artifact) || !text(artifact.artifactId) || artifactIds.has(artifact.artifactId) || !text(artifact.kind) ||
        !text(artifact.producerId) || !hash(artifact.digest)) return null
    artifactIds.add(artifact.artifactId)
  }
  return value as unknown as RunEvaluation
}
