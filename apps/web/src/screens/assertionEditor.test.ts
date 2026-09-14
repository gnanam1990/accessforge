import { expect, it } from 'vitest'
import type { JourneyCapabilities } from '../api/resources'
import { functionalValidationCapability, keyboardFocusCapability, readingOrderCapability, serializeAssertionRule, validateAssertionRules, type AssertionRow } from './AssertionEditor'

it('requires a compatible native focus capability and explicit qualified identity', () => {
  const capability = { assertionKind: 'FOCUS_BEHAVIOUR', measurementKind: 'AX_KEYBOARD_FOCUS',
    identifierDigestDomain: 'accessforge.keyboard-focus-identifier.v1', maxActionSequence: 1000,
    roles: ['AXTextField', 'AXButton'] }
  const supported = { ...policy, evaluationRules: { EXACT_NATIVE_KEYBOARD_FOCUS: capability } }
  const focus = { ...row, kind: 'FOCUS_BEHAVIOUR', focusRole: 'AXTextField', focusIdentifierDigest: 'a'.repeat(64) }
  expect(keyboardFocusCapability(supported)).not.toBeNull()
  expect(validateAssertionRules([focus], supported, 40, 'test', [])).toEqual([])
  expect(serializeAssertionRule(focus)).toEqual({ evaluationRule: { type: 'EXACT_NATIVE_KEYBOARD_FOCUS',
    actionSequence: 2, role: 'AXTextField', identifierDigest: 'a'.repeat(64) } })
  expect(serializeAssertionRule({ ...focus, ruleEnabled: false })).toEqual({})
  for (const invalid of ['', 'a'.repeat(63), 'A'.repeat(64), ' ' + 'a'.repeat(64), '#email']) {
    expect(validateAssertionRules([{ ...focus, focusIdentifierDigest: invalid }], supported, 40, 'test', [])
      .some((e) => e.fieldId.endsWith('-focus-digest'))).toBe(true)
  }
  expect(validateAssertionRules([{ ...focus, actionSequence: '40' }], supported, 40, 'test', [])
    .some((e) => e.fieldId.endsWith('-action'))).toBe(true)
  expect(validateAssertionRules([{ ...focus, focusRole: 'AXWindow' }], supported, 40, 'test', [])
    .some((e) => e.fieldId.endsWith('-focus-role'))).toBe(true)
  for (const change of [{ roles: [] }, { roles: ['AXWindow'] }, { roles: ['AXButton', 'AXButton'] },
    { identifierDigestDomain: 'different' }, { maxActionSequence: 0 }, { measurementKind: 'VOICEOVER_CURSOR' }]) {
    const unavailable = { ...supported, evaluationRules: { EXACT_NATIVE_KEYBOARD_FOCUS: { ...capability, ...change } } }
    expect(keyboardFocusCapability(unavailable)).toBeNull()
    expect(validateAssertionRules([focus], unavailable, 40, 'test', [])[0]?.fieldId).toBe('test-order-enabled')
  }
})

const policy: JourneyCapabilities = {
  allowedActions: ['NEXT'], allowedEffects: [], allowedKeyChordsByPlatform: {}, assertionKinds: ['READING_ORDER'],
  unknownReasons: ['OBSERVATION_MISSING'], maxActions: 40, maxWallTimeSeconds: 300, effectsMeaning: 'fixture',
  evaluationRules: { READER_NEXT_SEQUENCE: { assertionKind: 'READING_ORDER', action: 'NEXT', minSteps: 2, maxSteps: 20,
    maxActionSequence: 1000, maxPhraseCharacters: 8192, maxTotalPhraseBytes: 32768 } },
}
const row: AssertionRow = { assertionId: 'order', kind: 'READING_ORDER', description: 'Consecutive reader order',
  required: true, unknownReasons: ['OBSERVATION_MISSING'], ruleEnabled: true, actionSequence: '2', sequencePhrases: ['Name', 'Email'] }

it('freezes only an explicitly reviewed server functional suite and detects changed capabilities', () => {
  const suiteDigest = 'f'.repeat(64)
  const supported = { ...policy, evaluationRules: { PROTECTED_REFERENCE_VALIDATION: {
    assertionKind: 'FUNCTIONAL_VALIDATION', suiteDigest,
  } } }
  const functional = { ...row, kind: 'FUNCTIONAL_VALIDATION', suiteDigest }
  expect(functionalValidationCapability(supported)?.suiteDigest).toBe(suiteDigest)
  expect(validateAssertionRules([functional], supported, 40, 'test', [])).toEqual([])
  expect(serializeAssertionRule(functional)).toEqual({ evaluationRule: { type: 'PROTECTED_REFERENCE_VALIDATION', suiteDigest } })
  expect(serializeAssertionRule({ ...functional, ruleEnabled: false })).toEqual({})
  for (const changed of [policy, { ...supported, evaluationRules: { PROTECTED_REFERENCE_VALIDATION: {
    assertionKind: 'FUNCTIONAL_VALIDATION', suiteDigest: 'a'.repeat(64),
  } } }]) {
    expect(validateAssertionRules([functional], changed, 40, 'test', [])[0]?.fieldId).toBe('test-order-enabled')
  }
  for (const invalid of ['', 'f'.repeat(63), 'F'.repeat(64), 'not-a-digest']) {
    expect(functionalValidationCapability({ ...supported, evaluationRules: { PROTECTED_REFERENCE_VALIDATION: {
      assertionKind: 'FUNCTIONAL_VALIDATION', suiteDigest: invalid,
    } } })).toBeNull()
    expect(() => serializeAssertionRule({ ...functional, suiteDigest: invalid })).toThrow()
  }
  expect(functionalValidationCapability({ ...supported, evaluationRules: { PROTECTED_REFERENCE_VALIDATION: {
    assertionKind: 'TASK_COMPLETION', suiteDigest,
  } } })).toBeNull()
  expect(() => serializeAssertionRule({ ...functional, kind: 'FORBIDDEN_EFFECT' })).toThrow()
})

it('does not enable an unadvertised or incompatible reader sequence profile', () => {
  expect(readingOrderCapability(policy)).not.toBeNull()
  const absent = { ...policy, evaluationRules: {} }
  expect(readingOrderCapability(absent)).toBeNull()
  expect(validateAssertionRules([row], absent, 40, 'test', ['NEXT'])[0]?.fieldId).toBe('test-order-enabled')
  const incompatible = { ...policy, evaluationRules: { READER_NEXT_SEQUENCE: { ...policy.evaluationRules!.READER_NEXT_SEQUENCE!, action: 'PREVIOUS' } } }
  expect(readingOrderCapability(incompatible)).toBeNull()
  expect(serializeAssertionRule({ ...row, ruleEnabled: false })).toEqual({})
})

it('uses the whole consecutive sequence budget and total UTF-8 bound', () => {
  expect(validateAssertionRules([row], policy, 4, 'test', ['NEXT'])).toEqual([])
  expect(validateAssertionRules([row], policy, 3, 'test', ['NEXT']).some((e) => e.fieldId.endsWith('-action'))).toBe(true)
  const large = { ...row, sequencePhrases: ['😀'.repeat(5000), '😀'.repeat(5000)] }
  expect(validateAssertionRules([large], policy, 40, 'test', ['NEXT']).some((e) => e.message.includes('UTF-8 bytes'))).toBe(true)
  expect(serializeAssertionRule(row)).toEqual({ evaluationRule: { type: 'READER_NEXT_SEQUENCE', steps: [
    { actionSequence: 2, phrase: 'Name' }, { actionSequence: 3, phrase: 'Email' },
  ] } })
})
