import assert from 'node:assert/strict';
import { test } from 'node:test';
import { main, NOT_IMPLEMENTED_MESSAGE } from '../dist/main.js';

test('the runner reports non-implementation instead of a false success', () => {
  const lines = [];
  const code = main((l) => lines.push(l));

  // A zero exit would let a caller treat an absent runner as a working one.
  assert.notEqual(code, 0, 'runner must not exit successfully while unimplemented');
  assert.equal(code, 78);
  assert.deepEqual(lines, [NOT_IMPLEMENTED_MESSAGE]);
});

test('the message names the modules that actually own runner behaviour', () => {
  assert.match(NOT_IMPLEMENTED_MESSAGE, /modules 07/);
  assert.match(NOT_IMPLEMENTED_MESSAGE, /08/);
  assert.match(NOT_IMPLEMENTED_MESSAGE, /reports no capability/);
});
