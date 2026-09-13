import { expect, it } from 'vitest'
import type { JourneyCapabilities } from '../api/resources'
import { readingOrderCapability, serializeAssertionRule, validateAssertionRules, type AssertionRow } from './AssertionEditor'

const policy: JourneyCapabilities = {
  allowedActions: ['NEXT'], allowedEffects: [], allowedKeyChordsByPlatform: {}, assertionKinds: ['READING_ORDER'],
  unknownReasons: ['OBSERVATION_MISSING'], maxActions: 40, maxWallTimeSeconds: 300, effectsMeaning: 'fixture',
  evaluationRules: { READER_NEXT_SEQUENCE: { assertionKind: 'READING_ORDER', action: 'NEXT', minSteps: 2, maxSteps: 20,
    maxActionSequence: 1000, maxPhraseCharacters: 8192, maxTotalPhraseBytes: 32768 } },
}
const row: AssertionRow = { assertionId: 'order', kind: 'READING_ORDER', description: 'Consecutive reader order',
  required: true, unknownReasons: ['OBSERVATION_MISSING'], ruleEnabled: true, actionSequence: '2', sequencePhrases: ['Name', 'Email'] }

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
