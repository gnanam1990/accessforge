import type { ApiClient, ApiOutcome } from './client'
import type { Patch } from './patches'

export interface SourceSide {
  readonly text: string; readonly sha256: string; readonly byteLength: number; readonly mode: string
}
export interface ComparisonFile {
  readonly path: string; readonly before: SourceSide | null; readonly after: SourceSide | null
  readonly operation: 'ADD' | 'MODIFY' | 'DELETE'; readonly changed: boolean; readonly unifiedDiff: string
}
export interface PatchComparison {
  readonly comparisonId: string; readonly comparisonDigest: string; readonly preparedBy: string
  readonly recordedAt: string; readonly retiredAt: string | null
  readonly comparison: {
    readonly patchRevision: number; readonly baseCommitSha: string; readonly baseArchiveDigest: string
    readonly files: readonly ComparisonFile[]
  } | null
}
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0
const hash = (v: unknown): boolean => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v)
const time = (v: unknown): boolean => text(v) && Number.isFinite(Date.parse(v))
const side = (v: unknown): v is SourceSide | null => v === null || (object(v) && typeof v.text === 'string' &&
  hash(v.sha256) && typeof v.byteLength === 'number' && Number.isSafeInteger(v.byteLength) && v.byteLength >= 0 &&
  v.byteLength <= 262144 && new TextEncoder().encode(v.text).length === v.byteLength && ['100644', '100755'].includes(String(v.mode)))

export const parsePatchComparison = (v: unknown, ws: string, patch: Patch): PatchComparison | null => {
  if (!object(v) || !text(v.comparisonId) || !hash(v.comparisonDigest) || !text(v.preparedBy) ||
      !time(v.recordedAt) || !(v.retiredAt === null || time(v.retiredAt)) || v.patchId !== patch.patchId ||
      v.patchDigest !== patch.patchDigest || v.baseSourceDigest !== patch.baseSourceDigest ||
      v.meaning !== 'RETAINED_SOURCE_COMPARISON_NOT_APPROVAL_OR_VERIFICATION') return null
  // A tombstone always suppresses source, even if a stale/malformed response also contains it.
  if (v.retiredAt !== null) return { ...v, comparison: null } as unknown as PatchComparison
  const c = v.comparison
  if (!object(c) || c.schemaVersion !== 1 || c.workspaceId !== ws || c.patchId !== patch.patchId ||
      c.patchDigest !== patch.patchDigest || c.baseSourceDigest !== patch.baseSourceDigest ||
      c.baseManifestDigest !== patch.baseManifestDigest || c.requestedBy !== v.preparedBy ||
      !text(c.projectId) || !text(c.sourceSnapshotId) || !hash(c.baseArchiveDigest) ||
      typeof c.baseCommitSha !== 'string' || !/^[0-9a-f]{40}$/.test(c.baseCommitSha) ||
      typeof c.patchRevision !== 'number' || !Number.isSafeInteger(c.patchRevision) || c.patchRevision < 1 ||
      c.patchRevision > patch.revision || c.meaning !== 'ORIGINAL_SOURCE_COMPARISON_NOT_APPLICATION_OR_VERIFICATION' ||
      !Array.isArray(c.files) || c.files.length !== patch.changes.length || c.files.length > 20) return null
  let bytes = 0
  for (const [index, file] of c.files.entries()) {
    const change = patch.changes[index]!
    if (!object(file) || file.path !== change.path || !side(file.before) || !side(file.after) ||
        typeof file.unifiedDiff !== 'string' || typeof file.changed !== 'boolean' ||
        (file.before === null && file.after === null) || change.binary ||
        (file.after?.text ?? null) !== change.content ||
        file.operation !== (file.before === null ? 'ADD' : file.after === null ? 'DELETE' : 'MODIFY')) return null
    if (file.after !== null && file.after.mode !== (change.mode ?? file.before?.mode ?? '100644')) return null
    if (file.changed !== (file.before?.text !== file.after?.text || file.before?.mode !== file.after?.mode)) return null
    bytes += (file.before?.byteLength ?? 0) + (file.after?.byteLength ?? 0)
  }
  return bytes <= 2097152 ? v as unknown as PatchComparison : null
}

export const getPatchComparison = async (client: ApiClient, ws: string, patch: Patch, signal: AbortSignal): Promise<ApiOutcome<PatchComparison>> => {
  const result = await client.request(`/v1/workspaces/${encodeURIComponent(ws)}/patches/${encodeURIComponent(patch.patchId)}/source-comparison`, { signal })
  if (result.kind !== 'ok' && result.kind !== 'accepted') return result
  const value = parsePatchComparison(result.value, ws, patch)
  return value === null ? { kind: 'problem', problem: { code: 'UNRECOGNISED', status: 502, requestId: null,
    title: 'Original-source comparison unavailable', detail: 'This response does not match the exact proposal and base. No source comparison or repair proof is inferred.' } } : { ...result, value }
}
