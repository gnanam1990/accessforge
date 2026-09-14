import { expect, it } from 'vitest'
import type { JourneyCapabilities } from '../api/resources'
import { functionalValidationCapability, readingOrderCapability, serializeAssertionRule, validateAssertionRules, type AssertionRow } from './AssertionEditor'

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
