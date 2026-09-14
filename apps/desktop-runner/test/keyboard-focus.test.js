import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseKeyboardFocus } from '../dist/keyboard-focus.js';

const known = { measurementKind: 'AX_KEYBOARD_FOCUS', status: 'KNOWN', capturedAtUtc: '2026-09-14T12:00:00Z',
  role: 'AXTextField', identifierDigest: 'a'.repeat(64) };
test('private focus source is a closed immutable record, never a verdict', () => {
  assert.deepEqual(parseKeyboardFocus(known), known);
  assert.ok(Object.isFrozen(parseKeyboardFocus(known)));
  const unknown = { measurementKind: 'AX_KEYBOARD_FOCUS', status: 'UNKNOWN', capturedAtUtc: known.capturedAtUtc,
    reason: 'NATIVE_FOCUS_UNAVAILABLE' };
  assert.deepEqual(parseKeyboardFocus(unknown), unknown);
  for (const change of [{ role: 'AXToolbar' }, { identifierDigest: 'guess' }, { value: 'private' },
    { capturedAtUtc: 'today' }, { condition: 'TRUE' }, { status: 'PASS' }]) {
    assert.throws(() => parseKeyboardFocus({ ...known, ...change }));
  }
  assert.throws(() => parseKeyboardFocus({ ...unknown, reason: 'private native error' }));
});
