import assert from 'node:assert/strict';
import { test } from 'node:test';
import { main, NOT_IMPLEMENTED_MESSAGE } from '../dist/main.js';

test('the runner reports non-implementation instead of a false success', () => {
  const lines = [];
  const code = main((l) => lines.push(l));

  // A zero exit would let a caller treat an absent runner as a working one.
  assert.notEqual(code, 0, 'runner must not exit successfully while no reader adapter exists');
  assert.equal(code, 78);
  assert.deepEqual(lines, [NOT_IMPLEMENTED_MESSAGE]);
});

test('the message distinguishes what module 07 built from what it did not', () => {
  // The distinction is the whole value of this message. Module 07 is real; the screen reader is not.
  assert.match(NOT_IMPLEMENTED_MESSAGE, /supervisor protocol from module 07 is implemented/);
  assert.match(NOT_IMPLEMENTED_MESSAGE, /VoiceOver is owned by module 08/);
  assert.match(NOT_IMPLEMENTED_MESSAGE, /NVDA by module 09/);
  assert.match(NOT_IMPLEMENTED_MESSAGE, /reports no screen-reader capability/);
});

test('the protocol the message claims to have is actually importable', async () => {
  // Otherwise the message would be the same kind of unverified claim it exists to avoid making.
  const supervisor = await import('../dist/supervisor.js');
  const journal = await import('../dist/journal.js');
  for (const name of ['Supervisor', 'inspectJournalAfterRestart', 'ALLOWED_ACTIONS']) {
    assert.ok(name in supervisor, `supervisor module is missing ${name}`);
  }
  assert.ok('FileJournal' in journal);
});
