import assert from 'node:assert/strict';
import { test } from 'node:test';
import { main, READER_UNAVAILABLE_MESSAGE } from '../dist/main.js';

const blockedHost = {
  pathExists: () => false,
  readPreference: () => undefined,
  processRunning: () => false,
  auditSessionId: () => undefined,
  screenLocked: () => undefined,
  hasPermission: () => undefined,
};

test('the runner reports an unavailable real-reader profile instead of false success', () => {
  const lines = [];
  const code = main((l) => lines.push(l), blockedHost);

  // A zero exit would let a caller treat a compiled adapter as a proven reader runtime.
  assert.notEqual(code, 0, 'runner must not exit successfully while no reader profile is proven');
  assert.equal(code, 78);
  assert.equal(lines.length, 2);
  assert.equal(lines[0], READER_UNAVAILABLE_MESSAGE);
  const report = JSON.parse(lines[1]);
  assert.equal(report.realReaderAvailable, false);
  assert.equal(report.checks.READER_ACTIVE.condition, 'FALSE');
});

test('the message distinguishes implemented code from missing actual-reader proof', () => {
  assert.match(READER_UNAVAILABLE_MESSAGE, /Guidepup VoiceOver adapter is implemented/);
  assert.match(READER_UNAVAILABLE_MESSAGE, /verified matrix is empty/);
  assert.match(READER_UNAVAILABLE_MESSAGE, /no reader capability is advertised/);
});

test('the protocol the message claims to have is actually importable', async () => {
  // Otherwise the message would be the same kind of unverified claim it exists to avoid making.
  const supervisor = await import('../dist/supervisor.js');
  const journal = await import('../dist/journal.js');
  const voiceover = await import('../dist/voiceover.js');
  for (const name of ['Supervisor', 'inspectJournalAfterRestart', 'ALLOWED_ACTIONS']) {
    assert.ok(name in supervisor, `supervisor module is missing ${name}`);
  }
  assert.ok('FileJournal' in journal);
  assert.ok('createVoiceOverDispatch' in voiceover);
});
