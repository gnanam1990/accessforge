/** Frozen predicates are explicit controls, not inferred from the reviewer's description. */
import type { JSX } from 'react'
import type { JourneyCapabilities } from '../api/resources'
import { FormField } from '../components/FormField'

export interface AssertionRow {
  readonly assertionId: string
  readonly kind: string
  readonly description: string
  readonly required: boolean
  readonly unknownReasons: readonly string[]
  readonly ruleEnabled?: boolean
  readonly phrase?: string
  readonly actionSequence?: string
  readonly effectCount?: string
}

type FieldError = { readonly fieldId: string; readonly message: string }
export const assertionFieldId = (prefix: string, row: AssertionRow, field: string): string =>
  `${prefix}-${row.assertionId}-${field}`

export function validateAssertionRules(
  rows: readonly AssertionRow[], policy: JourneyCapabilities, actionBudget: number | null, prefix: string,
): FieldError[] {
  const errors: FieldError[] = []
  rows.forEach((row, index) => {
    const add = (field: string, message: string): void => {
      errors.push({ fieldId: assertionFieldId(prefix, row, field), message: `Assertion ${index + 1}: ${message}` })
    }
    if (!row.description.trim()) add('description', 'add a description a reviewer can read.')
    if (!row.ruleEnabled) return
    if (row.kind === 'REQUIRED_ANNOUNCEMENT') {
      const rule = policy.evaluationRules?.EXACT_READER_PHRASE
      if (!rule || rule.assertionKind !== row.kind) { add('enabled', 'this server cannot freeze a reader phrase rule.'); return }
      const position = row.actionSequence ?? ''
      const max = Math.min(actionBudget ?? policy.maxActions, rule.maxActionSequence)
      if (!/^\d+$/.test(position) || !Number.isSafeInteger(Number(position)) || Number(position) < 1 || Number(position) > max) {
        add('action', `the action sequence must be a whole number between 1 and ${max}.`)
      }
      const phrase = row.phrase ?? ''
      if (!phrase.trim()) add('phrase', 'enter the exact expected reader phrase.')
      else if (Array.from(phrase).length > rule.maxPhraseCharacters || new TextEncoder().encode(phrase).length > rule.maxPhraseBytes) {
        add('phrase', `the phrase exceeds ${rule.maxPhraseCharacters} characters or ${rule.maxPhraseBytes} UTF-8 bytes.`)
      }
    } else if (row.kind === 'TASK_COMPLETION') {
      const rule = policy.evaluationRules?.EFFECT_COUNT
      if (!rule || rule.assertionKind !== row.kind || rule.effect !== 'CREATE_TEST_REQUEST') {
        add('enabled', 'this server cannot freeze the supported completion rule.'); return
      }
      const count = row.effectCount ?? ''
      if (!/^\d+$/.test(count) || !Number.isSafeInteger(Number(count)) || Number(count) > rule.maxCount) {
        add('count', `the expected count must be a whole number between 0 and ${rule.maxCount}.`)
      }
    } else add('enabled', 'this assertion kind has no supported executable rule.')
  })
  return errors
}

export function serializeAssertionRule(row: AssertionRow): Record<string, unknown> {
  if (!row.ruleEnabled) return {}
  if (row.kind === 'TASK_COMPLETION') {
    return { evaluationRule: { type: 'EFFECT_COUNT', effect: 'CREATE_TEST_REQUEST', count: Number(row.effectCount) } }
  }
  return { evaluationRule: { type: 'EXACT_READER_PHRASE', actionSequence: Number(row.actionSequence), phrase: row.phrase } }
}

export const AssertionEditor = ({ row, index, prefix, policy, errors, disabled, onChange }: {
  readonly row: AssertionRow
  readonly index: number
  readonly prefix: string
  readonly policy: JourneyCapabilities
  readonly errors: readonly FieldError[]
  readonly disabled: boolean
  readonly onChange: (row: AssertionRow) => void
}): JSX.Element => {
  const fieldId = (field: string): string => assertionFieldId(prefix, row, field)
  const error = (field: string): { error?: string } => {
    const found = errors.find((item) => item.fieldId === fieldId(field))
    return found ? { error: found.message } : {}
  }
  const reader = row.kind === 'REQUIRED_ANNOUNCEMENT'
  const available = reader
    ? policy.evaluationRules?.EXACT_READER_PHRASE?.assertionKind === row.kind
    : row.kind === 'TASK_COMPLETION' && policy.evaluationRules?.EFFECT_COUNT?.assertionKind === row.kind &&
      policy.evaluationRules.EFFECT_COUNT.effect === 'CREATE_TEST_REQUEST'
  return (
    <fieldset className="af-panel af-stack" disabled={disabled}>
      <legend>Assertion {index + 1} — {reader ? 'Reader announcement' : 'Independent completion'}</legend>
      <FormField id={fieldId('description')} label={`Assertion ${index + 1} description`}
        hint="Describe the requirement for a reviewer. This description is not an executable matcher." required {...error('description')}>
        {({ id, describedBy, invalid }) => <input id={id} value={row.description} aria-describedby={describedBy}
          aria-invalid={invalid || undefined} onChange={(event) => onChange({ ...row, description: event.target.value })} />}
      </FormField>
      <FormField id={fieldId('enabled')} label={`Freeze an executable rule for assertion ${index + 1}`}
        hint={available ? 'The expected answer stays on the evaluator/observer side; it is not added to navigator instructions.'
          : 'This server does not advertise this rule. Upgrade the server before enabling it.'} {...error('enabled')}>
        {({ id, describedBy, invalid }) => <input id={id} type="checkbox" checked={row.ruleEnabled ?? false}
          disabled={!available} aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={(event) => onChange({ ...row, ruleEnabled: event.target.checked,
            actionSequence: row.actionSequence ?? '1', effectCount: row.effectCount ?? '1', phrase: row.phrase ?? '' })} />}
      </FormField>
      {!row.ruleEnabled && <p className="af-secondary">No executable rule: this condition remains UNKNOWN when evaluated; prose alone cannot establish it.</p>}
      {row.ruleEnabled && reader && <>
        <FormField id={fieldId('action')} label={`Assertion ${index + 1} action sequence`} required {...error('action')}
          hint="The exact action after which to compare the retained utterance. Must fit within the journey action budget.">
          {({ id, describedBy, invalid }) => <input id={id} type="text" inputMode="numeric" value={row.actionSequence ?? ''}
            aria-describedby={describedBy} aria-invalid={invalid || undefined}
            onChange={(event) => onChange({ ...row, actionSequence: event.target.value })} />}
        </FormField>
        <FormField id={fieldId('phrase')} label={`Assertion ${index + 1} exact reader phrase`} required {...error('phrase')}
          hint="Literal, case-sensitive matching. Spaces and line breaks are preserved. No regex or fuzzy matching; missing/redacted captures remain UNKNOWN.">
          {({ id, describedBy, invalid }) => <textarea id={id} rows={3} value={row.phrase ?? ''}
            aria-describedby={describedBy} aria-invalid={invalid || undefined}
            onChange={(event) => onChange({ ...row, phrase: event.target.value })} />}
        </FormField>
      </>}
      {row.ruleEnabled && !reader && <FormField id={fieldId('count')} label={`Assertion ${index + 1} expected request count`}
        hint={`Independent final observer count for CREATE_TEST_REQUEST. Zero is valid; maximum ${policy.evaluationRules?.EFFECT_COUNT?.maxCount ?? 'unavailable'}.`} required {...error('count')}>
        {({ id, describedBy, invalid }) => <input id={id} type="text" inputMode="numeric" value={row.effectCount ?? ''}
          aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={(event) => onChange({ ...row, effectCount: event.target.value })} />}
      </FormField>}
    </fieldset>
  )
}
