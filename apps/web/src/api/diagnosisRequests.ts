import type { ApiClient, ApiOutcome } from './client'

export interface DiagnosisScope {
  readonly manifestDigest: string
  readonly evaluationDigest: string
  readonly modelProfileDigest: string
  readonly assertionId: string
  readonly componentPath: string
  readonly componentName: string
  readonly excerpts: readonly { readonly path: string; readonly lineStart: number; readonly lineEnd: number }[]
  readonly supersedes: string | null
  readonly billableCallAcknowledged: true
}
export interface DiagnosisProfile {
  readonly modelProfileDigest: string
  readonly meaning: string
  readonly profile: Readonly<Record<string, string | number>>
}
export interface DiagnosisDecision {
  readonly requestId: string
  readonly runId: string
  readonly requestedBy: string
  readonly scope: DiagnosisScope
  readonly scopeDigest: string
  readonly createdAt: string
  readonly expiresAt: string
  readonly revokedAt: string | null
  readonly invocationState?: string
  readonly findingId?: string | null
}
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const hash = (value: unknown): boolean => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const utc = (value: unknown): boolean => text(value) && value.endsWith('Z') && Number.isFinite(Date.parse(value))
export const sameDiagnosisScope = (a: DiagnosisScope, b: DiagnosisScope): boolean =>
  a.manifestDigest === b.manifestDigest && a.evaluationDigest === b.evaluationDigest &&
  a.modelProfileDigest === b.modelProfileDigest && a.assertionId === b.assertionId &&
  a.componentPath === b.componentPath && a.componentName === b.componentName && a.supersedes === b.supersedes &&
  a.excerpts.length === b.excerpts.length && a.excerpts.every((row, i) =>
    row.path === b.excerpts[i]?.path && row.lineStart === b.excerpts[i]?.lineStart && row.lineEnd === b.excerpts[i]?.lineEnd)

const parseScope = (value: unknown): value is DiagnosisScope => object(value) &&
  hash(value.manifestDigest) && hash(value.evaluationDigest) && hash(value.modelProfileDigest) &&
  text(value.assertionId) && text(value.componentPath) && text(value.componentName) &&
  (value.supersedes === null || text(value.supersedes)) && value.billableCallAcknowledged === true &&
  Array.isArray(value.excerpts) && value.excerpts.length > 0 && value.excerpts.length <= 40 &&
  value.excerpts.every((row: unknown) => object(row) && text(row.path) &&
    typeof row.lineStart === 'number' && Number.isSafeInteger(row.lineStart) && row.lineStart >= 1 &&
    typeof row.lineEnd === 'number' && Number.isSafeInteger(row.lineEnd) && row.lineEnd >= row.lineStart &&
    row.lineEnd < row.lineStart + 200)

const checked = <T,>(result: ApiOutcome<unknown>, parse: (value: unknown) => T | null): ApiOutcome<T> => {
  if (result.kind !== 'ok' && result.kind !== 'accepted') return result
  const value = parse(result.value)
  return value === null ? { kind: 'problem', problem: { code: 'UNRECOGNISED', status: 502,
    title: 'Diagnosis response unavailable', requestId: null,
    detail: 'The response does not match the requested identity or contract. Reconcile this operation; do not create a replacement request.' } }
    : { ...result, value }
}
const decision = (value: unknown, runId: string, userId: string, readback: boolean): DiagnosisDecision | null => {
  if (!object(value) || !text(value.requestId) || value.runId !== runId || value.requestedBy !== userId ||
      !parseScope(value.scope) || !hash(value.scopeDigest) || !utc(value.createdAt) || !utc(value.expiresAt) ||
      !(value.revokedAt === null || utc(value.revokedAt)) ||
      value.meaning !== 'HUMAN_REQUEST_NOT_MODEL_COMPLETION' || value.deliveryMode !== 'EXPLICIT_OPERATOR_DISPATCH') return null
  if (readback && (!text(value.invocationState) || !(value.findingId === null || text(value.findingId)))) return null
  return value as unknown as DiagnosisDecision
}
const base = (workspaceId: string): string => `/v1/workspaces/${encodeURIComponent(workspaceId)}`
export const readDiagnosisProfile = async (client: ApiClient, workspaceId: string, signal: AbortSignal): Promise<ApiOutcome<DiagnosisProfile>> =>
  checked(await client.request(`${base(workspaceId)}/diagnosis-profile`, { signal }), (value) => {
    if (!object(value) || !hash(value.modelProfileDigest) || !text(value.meaning) || !object(value.profile)) return null
    const p = value.profile
    if (!['sdk_version', 'model_id', 'region_name'].every((key) => text(p[key])) ||
        !['provider_max_tokens', 'invocation_output_tokens', 'invocation_total_tokens', 'max_context_characters', 'call_timeout_seconds']
          .every((key) => typeof p[key] === 'number' && Number.isFinite(p[key]) && p[key] > 0) ||
        !Object.values(p).every((item) => typeof item === 'string' || typeof item === 'number')) return null
    return value as unknown as DiagnosisProfile
  })
export const requestDiagnosis = async (client: ApiClient, workspaceId: string, runId: string, userId: string,
  scope: DiagnosisScope, operationKey: string, signal: AbortSignal): Promise<ApiOutcome<DiagnosisDecision>> =>
  checked(await client.request(`${base(workspaceId)}/runs/${encodeURIComponent(runId)}/diagnosis-requests`,
    { method: 'POST', body: scope, idempotencyKey: operationKey, signal }), (value) => {
    const parsed = decision(value, runId, userId, false)
    return parsed !== null && sameDiagnosisScope(parsed.scope, scope) ? parsed : null
  })
export const recoverDiagnosis = async (client: ApiClient, workspaceId: string, runId: string, userId: string,
  operationKey: string, signal: AbortSignal): Promise<ApiOutcome<DiagnosisDecision>> =>
  checked(await client.request(`${base(workspaceId)}/runs/${encodeURIComponent(runId)}/diagnosis-requests/operation?operationKey=${encodeURIComponent(operationKey)}`,
    { signal }), (value) => decision(value, runId, userId, true))
export const revokeDiagnosis = async (client: ApiClient, workspaceId: string, value: DiagnosisDecision,
  signal: AbortSignal): Promise<ApiOutcome<DiagnosisDecision>> =>
  checked(await client.request(`${base(workspaceId)}/diagnosis-requests/${encodeURIComponent(value.requestId)}/revocation`,
    { method: 'POST', signal }), (raw) => {
    const parsed = decision(raw, value.runId, value.requestedBy, true)
    return parsed?.requestId === value.requestId && parsed.revokedAt !== null ? parsed : null
  })
