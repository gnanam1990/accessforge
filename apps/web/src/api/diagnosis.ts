/** Retained model text is data, never markup, a machine verdict or repair authority. */
export interface DiagnosisAnalysis {
  readonly support: 'SOURCE_LINKED' | 'UNSUPPORTED'
  readonly missing_information: readonly string[]
  readonly hypothesis: null | {
    readonly observed_obstacle: string
    readonly affected_task_step: string
    readonly uncertainty: string
    readonly compliance_assessment: 'NOT_ASSESSED'
    readonly supporting_evidence_ids: readonly string[]
    readonly alternative_explanations: readonly string[]
    readonly source_location: null | {
      readonly path: string
      readonly file_digest: string
      readonly line_start: number
      readonly line_end: number
    }
  }
  readonly repair_brief: null | {
    readonly allowed_files: readonly string[]
    readonly intended_behavior: string
    readonly functional_constraints: readonly string[]
    readonly protected_surfaces: readonly string[]
    readonly stop_recommendation: string | null
  }
}

export interface RetainedDiagnosis {
  readonly diagnosisId: string
  readonly runId: string
  readonly requestedBy: string
  readonly recordedAt: string
  readonly deletedAt: string | null
  readonly supersedes: string | null
  readonly evaluationDigest: string
  readonly projectionDigest: string
  readonly modelProfileDigest: string
  readonly payloadDigest: string
  readonly establishedBy: string
  readonly analysis: unknown
}

export interface DiagnosisHistory {
  readonly items: readonly RetainedDiagnosis[]
  readonly complete: boolean
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string'
const texts = (value: unknown): value is string[] => Array.isArray(value) && value.every(text)

/** Unknown/older stored schemas must not break the evidence and human-review sections. */
export const isDiagnosisAnalysis = (value: unknown): value is DiagnosisAnalysis => {
  if (!record(value) || !['SOURCE_LINKED', 'UNSUPPORTED'].includes(String(value['support'])) ||
      !texts(value['missing_information'])) return false
  const h = value['hypothesis']
  if (h !== null) {
    if (!record(h) || !text(h['observed_obstacle']) || !text(h['affected_task_step']) ||
        !text(h['uncertainty']) || h['compliance_assessment'] !== 'NOT_ASSESSED' ||
        !texts(h['supporting_evidence_ids']) || !texts(h['alternative_explanations'])) return false
    const source = h['source_location']
    if (source !== null && (!record(source) || !text(source['path']) ||
        !text(source['file_digest']) || typeof source['line_start'] !== 'number' ||
        typeof source['line_end'] !== 'number' || !Number.isSafeInteger(source['line_start']) ||
        !Number.isSafeInteger(source['line_end']) || source['line_start'] < 1 ||
        source['line_end'] < source['line_start'])) return false
  }
  const brief = value['repair_brief']
  if (brief !== null && (!record(brief) || !texts(brief['allowed_files']) ||
      !text(brief['intended_behavior']) || !texts(brief['functional_constraints']) ||
      !texts(brief['protected_surfaces']) ||
      !(brief['stop_recommendation'] === null || text(brief['stop_recommendation'])))) return false
  return value['support'] === 'SOURCE_LINKED' ? h !== null && brief !== null : brief === null
}
