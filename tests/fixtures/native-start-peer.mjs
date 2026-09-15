// Synthetic qualification/execution only. The actual listener and wire parser are unchanged.
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { mock } from 'node:test';

const reader = createInterface({input: process.stdin});
const [line] = await once(reader, 'line');
const input = JSON.parse(line);
const adapterUrl = new URL('../../packages/at-adapters/voiceover/dist/index.js', import.meta.url);
const adapter = await import(adapterUrl.href);
mock.module(adapterUrl.href, {namedExports: {...adapter, assertRealReaderProven: () => {}}});
let delivered;
let finish;
const finishing = new Promise(resolve => { finish = resolve; });
mock.module(new URL('../../apps/desktop-runner/dist/execution-bootstrap.js', import.meta.url).href, {
  namedExports: {runProvisionedNavigatorExecution: async options => {
    delivered = options.dispatchEnvelope;
    await finishing;
    return {syntheticExecutionClosed: true};
  }},
});
const { startNativeDispatchListener } = await import('../../apps/desktop-runner/dist/native-start-listener.js');
const deadlineMonotonic = performance.now() + 10000;
const host = await startNativeDispatchListener(input.directory,
  {receiver: {localReference: input.reference}, lease: {deadlineMonotonic}},
  {reference: input.reference, deadlineMonotonic, allowBillableModelCalls: true,
    signal: new AbortController().signal});
try {
  process.stdout.write(JSON.stringify(host.privateReference) + '\n');
  await once(reader, 'line');
  assert.deepEqual(delivered, {reference: input.reference, ticket: input.ticket});
  finish();
  assert.deepEqual(await host.completion, {syntheticExecutionClosed: true});
  process.stdout.write('SYNTHETIC_EXECUTION_CLOSED\n');
} finally {
  host.close();
  reader.close();
}
