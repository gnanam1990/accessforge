import assert from 'node:assert/strict';
import { chmodSync, linkSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { FileJournal } from '../dist/journal.js';

test('journal preflight flushes the real file without inserting action evidence', async t => {
  const directory = realpathSync(mkdtempSync('/tmp/af-journal-probe-'));
  t.after(() => rmSync(directory, {recursive: true, force: true}));
  const path = join(directory, 'actions.jsonl');
  const journal = new FileJournal(path);
  assert.equal(await journal.probeWritable(), true);
  assert.equal(readFileSync(path, 'utf8'), '');
  writeFileSync(path, 'retained original bytes\n');
  assert.equal(await journal.probeWritable(), true);
  assert.equal(readFileSync(path, 'utf8'), 'retained original bytes\n');
  chmodSync(path, 0o644);
  assert.equal(await journal.probeWritable(), false);
});

for (const kind of ['symlink', 'hardlink']) {
  test(`journal ${kind} cannot pass preflight or accept action writes`, async t => {
    const directory = realpathSync(mkdtempSync('/tmp/af-journal-probe-'));
    t.after(() => rmSync(directory, {recursive: true, force: true}));
    const target = join(directory, 'original'), path = join(directory, 'actions.jsonl');
    writeFileSync(target, 'original', {mode: 0o600});
    if (kind === 'symlink') symlinkSync(target, path);
    else linkSync(target, path);
    const journal = new FileJournal(path);
    assert.equal(await journal.probeWritable(), false);
    await assert.rejects(journal.appendAndFlush({actionId: 'must-not-write'}));
    assert.equal(readFileSync(target, 'utf8'), 'original');
  });
}
