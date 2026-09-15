import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, realpathSync, readdirSync, rmSync } from 'node:fs';
import { test } from 'node:test';
import { startNativeDispatchListener } from '../dist/native-start-listener.js';

test('unqualified actual-reader profile cannot advertise a native start socket', async t => {
  const directory = realpathSync(mkdtempSync('/tmp/af-start-gate-'));
  t.after(() => rmSync(directory, {recursive: true, force: true}));
  const reference = {workspaceId: randomUUID(), runId: randomUUID(), attemptId: randomUUID(),
    runnerId: randomUUID(), leaseId: randomUUID(), epoch: 1};
  const deadlineMonotonic = performance.now() + 10000;
  await assert.rejects(startNativeDispatchListener(directory,
    {receiver: {localReference: reference}, lease: {deadlineMonotonic}},
    {reference, deadlineMonotonic, signal: new AbortController().signal, allowBillableModelCalls: true}));
  assert.deepEqual(readdirSync(directory), []);
});
