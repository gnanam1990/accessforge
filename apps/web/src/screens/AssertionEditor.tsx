/** Frozen predicates are explicit controls, not inferred from the reviewer's description. */
import { useEffect, useRef, type JSX } from 'react'
import type { JourneyCapabilities } from '../api/resources'
import { FormField } from '../components/FormField'
import { Button } from '../components/Button'

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
  readonly sequencePhrases?: readonly string[]
  readonly suiteDigest?: string
  readonly focusRole?: string
  readonly focusIdentifierDigest?: string
}

export function keyboardFocusCapability(policy: JourneyCapabilities) {
  const rule = policy.evaluationRules?.EXACT_NATIVE_KEYBOARD_FOCUS
  const roles = ['AXTextField', 'AXTextArea', 'AXButton', 'AXCheckBox', 'AXRadioButton', 'AXPopUpButton', 'AXComboBox', 'AXLink']
  return rule && rule.assertionKind === 'FOCUS_BEHAVIOUR' && rule.measurementKind === 'AX_KEYBOARD_FOCUS' &&
    rule.identifierDigestDomain === 'accessforge.keyboard-focus-identifier.v1' &&
    Number.isSafeInteger(rule.maxActionSequence) && rule.maxActionSequence > 0 && rule.maxActionSequence <= 1000 &&
    Array.isArray(rule.roles) && rule.roles.length > 0 && rule.roles.length <= roles.length &&
    new Set(rule.roles).size === rule.roles.length && rule.roles.every((role) => roles.includes(role)) ? rule : null
}

export function functionalValidationCapability(policy: JourneyCapabilities) {
  const rule = policy.evaluationRules?.PROTECTED_REFERENCE_VALIDATION
  return rule && rule.assertionKind === 'FUNCTIONAL_VALIDATION' &&
    typeof rule.suiteDigest === 'string' && /^[a-f0-9]{64}$/.test(rule.suiteDigest) ? rule : null
}

/** Only the supported bounded NEXT profile is offered; absent/malformed capability is not a default. */
export function readingOrderCapability(policy: JourneyCapabilities) {
  const rule = policy.evaluationRules?.READER_NEXT_SEQUENCE
  return rule && rule.assertionKind === 'READING_ORDER' && rule.action === 'NEXT' && rule.minSteps === 2 &&
    Number.isSafeInteger(rule.maxSteps) && rule.maxSteps >= 2 && rule.maxSteps <= 20 &&
    [rule.maxActionSequence, rule.maxPhraseCharacters, rule.maxTotalPhraseBytes].every((v) => Number.isSafeInteger(v) && v > 0)
    ? rule : null
}

type FieldError = { readonly fieldId: string; readonly message: string }
export const assertionFieldId = (prefix: string, row: AssertionRow, field: string): string =>
  `${prefix}-${row.assertionId}-${field}`

export function validateAssertionRules(
  rows: readonly AssertionRow[], policy: JourneyCapabilities, actionBudget: number | null, prefix: string,
  selectedActions: readonly string[],
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
      const max = Math.min((actionBudget ?? policy.maxActions) - 1, rule.maxActionSequence)
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
    } else if (row.kind === 'READING_ORDER') {
      const rule = readingOrderCapability(policy)
      if (!rule) { add('enabled', 'this server cannot freeze the supported reading-order rule.'); return }
      if (!selectedActions.includes('NEXT')) add('enabled', 'enable NEXT in permitted actions before freezing this sequence.')
      const phrases = row.sequencePhrases ?? []
      if (phrases.length < rule.minSteps || phrases.length > rule.maxSteps) {
        add('action', `the sequence needs ${rule.minSteps}–${rule.maxSteps} steps.`)
      }
      const start = row.actionSequence ?? ''
      const last = Number(start) + phrases.length - 1
      const max = Math.min((actionBudget ?? policy.maxActions) - 1, rule.maxActionSequence)
      if (!/^\d+$/.test(start) || !Number.isSafeInteger(Number(start)) || Number(start) < 1 || last > max) {
        add('action', `consecutive steps must start at a whole number and finish within action ${max}, leaving one slot for STOP.`)
      }
      phrases.forEach((phrase, step) => {
        if (!phrase.trim()) add(`step-${step}`, `enter the exact reader phrase for step ${step + 1}.`)
        else if (Array.from(phrase).length > rule.maxPhraseCharacters) add(`step-${step}`, `step ${step + 1} exceeds ${rule.maxPhraseCharacters} characters.`)
      })
      if (phrases.reduce((sum, phrase) => sum + new TextEncoder().encode(phrase).length, 0) > rule.maxTotalPhraseBytes) {
        add('action', `all step phrases together exceed ${rule.maxTotalPhraseBytes} UTF-8 bytes.`)
      }
    } else if (row.kind === 'FOCUS_BEHAVIOUR') {
      const rule = keyboardFocusCapability(policy)
      if (!rule) { add('enabled', 'this server cannot freeze the supported native keyboard-focus rule.'); return }
      const position = row.actionSequence ?? ''
      const max = Math.min((actionBudget ?? policy.maxActions) - 1, rule.maxActionSequence)
      if (!/^\d+$/.test(position) || !Number.isSafeInteger(Number(position)) || Number(position) < 1 || Number(position) > max) {
        add('action', `the action sequence must be a whole number between 1 and ${max}, leaving one slot for STOP.`)
      }
      if (!rule.roles.includes(row.focusRole ?? '')) add('focus-role', 'select a supported native AX role.')
      if (!/^[a-f0-9]{64}$/.test(row.focusIdentifierDigest ?? '')) {
        add('focus-digest', 'enter the 64-character lowercase SHA-256 digest from qualified native identifier evidence, not a selector or spoken phrase.')
      }
    } else if (row.kind === 'FUNCTIONAL_VALIDATION') {
      const rule = functionalValidationCapability(policy)
      if (!rule) add('enabled', 'this server cannot freeze the protected functional-validation rule.')
      else if (row.suiteDigest !== rule.suiteDigest) {
        add('enabled', 'the protected suite changed or is missing. Turn the rule off and on to review the current suite before freezing.')
      }
    } else add('enabled', 'this assertion kind has no supported executable rule.')
  })
  return errors
}

export function serializeAssertionRule(row: AssertionRow): Record<string, unknown> {
  if (!row.ruleEnabled) return {}
  if (row.kind === 'FOCUS_BEHAVIOUR') return { evaluationRule: {
    type: 'EXACT_NATIVE_KEYBOARD_FOCUS', actionSequence: Number(row.actionSequence),
    role: row.focusRole, identifierDigest: row.focusIdentifierDigest,
  } }
  if (row.kind === 'FUNCTIONAL_VALIDATION') {
    if (!row.suiteDigest || !/^[a-f0-9]{64}$/.test(row.suiteDigest)) throw new Error('Original protected suite unavailable')
    return { evaluationRule: { type: 'PROTECTED_REFERENCE_VALIDATION', suiteDigest: row.suiteDigest } }
  }
  if (row.kind === 'TASK_COMPLETION') {
    return { evaluationRule: { type: 'EFFECT_COUNT', effect: 'CREATE_TEST_REQUEST', count: Number(row.effectCount) } }
  }
  if (row.kind === 'READING_ORDER') return { evaluationRule: { type: 'READER_NEXT_SEQUENCE',
    steps: (row.sequencePhrases ?? []).map((phrase, index) => ({ actionSequence: Number(row.actionSequence) + index, phrase })),
  } }
  if (row.kind === 'REQUIRED_ANNOUNCEMENT') return { evaluationRule: { type: 'EXACT_READER_PHRASE', actionSequence: Number(row.actionSequence), phrase: row.phrase } }
  throw new Error('Unsupported executable assertion kind')
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
  const order = row.kind === 'READING_ORDER'
  const functional = row.kind === 'FUNCTIONAL_VALIDATION'
  const focus = row.kind === 'FOCUS_BEHAVIOUR'
  const focusRule = keyboardFocusCapability(policy)
  const functionalRule = functionalValidationCapability(policy)
  const sequenceRule = readingOrderCapability(policy)
  const pendingStepFocus = useRef<number | null>(null)
  useEffect(() => {
    if (pendingStepFocus.current !== null) {
      document.getElementById(assertionFieldId(prefix, row, `step-${pendingStepFocus.current}`))?.focus()
      pendingStepFocus.current = null
    }
  }, [prefix, row.assertionId, row.sequencePhrases?.length])
  const available = reader
    ? policy.evaluationRules?.EXACT_READER_PHRASE?.assertionKind === row.kind
    : order ? sequenceRule !== null
    : functional ? functionalRule !== null
    : focus ? focusRule !== null
    : row.kind === 'TASK_COMPLETION' && policy.evaluationRules?.EFFECT_COUNT?.assertionKind === row.kind &&
      policy.evaluationRules.EFFECT_COUNT.effect === 'CREATE_TEST_REQUEST'
  return (
    <fieldset className="af-panel af-stack" disabled={disabled}>
      <legend>Assertion {index + 1} — {reader ? 'Reader announcement' : order ? 'Consecutive NEXT reading order' : functional ? 'Protected functional validation' : focus ? 'Native keyboard focus' : 'Independent completion'}</legend>
      <FormField id={fieldId('description')} label={`Assertion ${index + 1} description`}
        hint="Describe the requirement for a reviewer. This description is not an executable matcher." required {...error('description')}>
        {({ id, describedBy, invalid }) => <input id={id} value={row.description} aria-describedby={describedBy}
          aria-invalid={invalid || undefined} onChange={(event) => onChange({ ...row, description: event.target.value })} />}
      </FormField>
      <FormField id={fieldId('enabled')} label={`Freeze an executable rule for assertion ${index + 1}`}
        hint={available ? 'The expected answer stays on the evaluator/observer side; it is not added to navigator instructions.'
          : 'This server does not advertise this rule. Upgrade the server before enabling it.'} {...error('enabled')}>
        {({ id, describedBy, invalid }) => <input id={id} type="checkbox" checked={row.ruleEnabled ?? false}
          disabled={!available && !row.ruleEnabled} aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={(event) => onChange({ ...row, ruleEnabled: event.target.checked,
            actionSequence: row.actionSequence ?? '1', effectCount: row.effectCount ?? '1', phrase: row.phrase ?? '',
            sequencePhrases: row.sequencePhrases ?? ['', ''],
            ...(functional && event.target.checked && functionalRule ? { suiteDigest: functionalRule.suiteDigest } : {}) })} />}
      </FormField>
      {!row.ruleEnabled && <p className="af-secondary">No executable rule: this condition remains UNKNOWN when evaluated; prose alone cannot establish it.</p>}
      {row.ruleEnabled && focus && <>
        <p>Checks AX keyboard focus, not the VoiceOver cursor. Use an identifier digest from separately qualified native evidence; hashes do not prove identity uniqueness or stability. Missing capture remains UNKNOWN.</p>
        <FormField id={fieldId('action')} label={`Assertion ${index + 1} action sequence`} required {...error('action')}
          hint="The successful non-STOP action after which to compare native focus. Leave one action-budget slot for STOP.">
          {({ id, describedBy, invalid }) => <input id={id} type="text" inputMode="numeric" value={row.actionSequence ?? ''}
            aria-describedby={describedBy} aria-invalid={invalid || undefined}
            onChange={(event) => onChange({ ...row, actionSequence: event.target.value })} />}
        </FormField>
        <FormField id={fieldId('focus-role')} label={`Assertion ${index + 1} native AX role`} required {...error('focus-role')}>
          {({ id, describedBy, invalid }) => <select id={id} value={row.focusRole ?? ''}
            aria-describedby={describedBy} aria-invalid={invalid || undefined}
            onChange={(event) => onChange({ ...row, focusRole: event.target.value })}>
            <option value="">Select a native role</option>
            {row.focusRole && !focusRule?.roles.includes(row.focusRole) && <option value={row.focusRole}>Unavailable: {row.focusRole}</option>}
            {focusRule?.roles.map((role) => <option key={role} value={role}>{role}</option>)}
          </select>}
        </FormField>
        <FormField id={fieldId('focus-digest')} label={`Assertion ${index + 1} native identifier digest`} required {...error('focus-digest')}
          hint="Exactly 64 lowercase hexadecimal characters. Do not paste the raw identifier, input value, CSS selector or reader text. This form does not derive the digest or qualify the native target.">
          {({ id, describedBy, invalid }) => <textarea id={id} rows={2} spellCheck={false} value={row.focusIdentifierDigest ?? ''}
            aria-describedby={describedBy} aria-invalid={invalid || undefined}
            onChange={(event) => onChange({ ...row, focusIdentifierDigest: event.target.value })} />}
        </FormField>
      </>}
      {row.ruleEnabled && functional && <FormField id={fieldId('suite')} label={`Assertion ${index + 1} protected suite digest`}
        hint="Fixed by the server, not editable here. The protected worker must prove that invalid reference-form submissions are rejected without writes. Missing evidence remains UNKNOWN; this is not reader or task-completion proof.">
        {({ id, describedBy }) => <textarea id={id} rows={2} readOnly value={row.suiteDigest ?? ''} aria-describedby={describedBy} />}
      </FormField>}
      {row.ruleEnabled && reader && <>
        <FormField id={fieldId('action')} label={`Assertion ${index + 1} action sequence`} required {...error('action')}
          hint="The exact action after which to compare the retained utterance. Leave one action-budget slot for the required STOP.">
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
      {row.ruleEnabled && order && <div className="af-stack">
        <p>Checks literal reader phrases after consecutive successful NEXT actions, not DOM/tab order or focus coordinates. Missing or redacted capture remains UNKNOWN. NEXT must be permitted.</p>
        <FormField id={fieldId('action')} label={`Assertion ${index + 1} starting NEXT action`} required {...error('action')}
          hint={`Leave one action-budget slot for STOP. ${sequenceRule?.minSteps ?? 2}–${sequenceRule?.maxSteps ?? 'unavailable'} steps; spaces and line breaks are preserved.`}>
          {({ id, describedBy, invalid }) => <input id={id} type="text" inputMode="numeric" value={row.actionSequence ?? ''}
            aria-describedby={describedBy} aria-invalid={invalid || undefined}
            onChange={(event) => onChange({ ...row, actionSequence: event.target.value })} />}
        </FormField>
        {(row.sequencePhrases ?? []).map((phrase, step) => <FormField key={step} id={fieldId(`step-${step}`)}
          label={`Assertion ${index + 1} step ${step + 1} exact reader phrase`} required {...error(`step-${step}`)}
          hint={`NEXT offset ${step} from the starting action. Literal, case-sensitive; no regex or inferred wording.`}>
          {({ id, describedBy, invalid }) => <textarea id={id} rows={2} value={phrase} aria-describedby={describedBy}
            aria-invalid={invalid || undefined} onChange={(event) => onChange({ ...row,
              sequencePhrases: (row.sequencePhrases ?? []).map((text, position) => position === step ? event.target.value : text) })} />}
        </FormField>)}
        <div className="af-row">
          <Button disabled={sequenceRule === null || (row.sequencePhrases?.length ?? 0) >= sequenceRule.maxSteps}
            onClick={() => {
              const phrases = row.sequencePhrases ?? []
              pendingStepFocus.current = phrases.length
              onChange({ ...row, sequencePhrases: [...phrases, ''] })
            }}>Add NEXT step to assertion {index + 1}</Button>
          <Button disabled={sequenceRule === null || (row.sequencePhrases?.length ?? 0) <= sequenceRule.minSteps}
            onClick={() => {
              const phrases = row.sequencePhrases ?? []
              pendingStepFocus.current = phrases.length - 2
              onChange({ ...row, sequencePhrases: phrases.slice(0, -1) })
            }}>Remove last NEXT step from assertion {index + 1}</Button>
        </div>
      </div>}
      {row.ruleEnabled && row.kind === 'TASK_COMPLETION' && <FormField id={fieldId('count')} label={`Assertion ${index + 1} expected request count`}
        hint={`Independent final observer count for CREATE_TEST_REQUEST. Zero is valid; maximum ${policy.evaluationRules?.EFFECT_COUNT?.maxCount ?? 'unavailable'}.`} required {...error('count')}>
        {({ id, describedBy, invalid }) => <input id={id} type="text" inputMode="numeric" value={row.effectCount ?? ''}
          aria-describedby={describedBy} aria-invalid={invalid || undefined}
          onChange={(event) => onChange({ ...row, effectCount: event.target.value })} />}
      </FormField>}
    </fieldset>
  )
}
