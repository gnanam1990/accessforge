import assert from 'node:assert/strict';
import { test } from 'node:test';
import { NvdaAdapter, createGuidepupNvdaAdapter, RealReaderUnavailable } from '../dist/index.js';

function fixture() {
  const calls = [], speech = ['synthetic initial'];
  const emit = name => async () => { calls.push(name); speech.push(`synthetic ${name}`); };
  const client = {
    detect: () => true, start: emit('start'), stop: emit('stop'), next: emit('next'),
    previous: emit('previous'), act: emit('act'), type: value => emit(`type:${value}`)(),
    press: value => emit(`press:${value}`)(), reportCurrentFocus: emit('reportCurrentFocus'),
    sayAll: emit('sayAll'), spokenPhraseLog: async () => speech,
  };
  const controller = new AbortController();
  const context = { signal: controller.signal, assertReady: async () => {} };
  return { client, calls, speech, controller, context, adapter: new NvdaAdapter(client, ['Fixture']) };
}

test('NVDA maps only the closed Windows command surface and captures raw log deltas', async () => {
  const h = fixture();
  await h.adapter.start(h.context);
  for (const [request, expected] of [
    [{action:'NEXT'}, 'next'], [{action:'PREVIOUS'}, 'previous'], [{action:'ACTIVATE'}, 'act'],
    [{action:'TYPE_TEXT', text:'Fixture'}, 'type:Fixture'],
    [{action:'KEY_CHORD', keyChord:'DOWN'}, 'press:ArrowDown'],
    [{action:'KEY_CHORD', keyChord:'NVDA+DOWN'}, 'sayAll'],
    [{action:'READ_CURRENT'}, 'reportCurrentFocus'],
  ]) {
    const capture = await h.adapter.perform(request, h.context);
    assert.equal(h.calls.at(-1), expected);
    assert.equal(capture.status, 'LOG_DELTA');
    assert.equal(capture.meaning, 'SDK_LOG_DELTA_NOT_ACOUSTIC_ATTESTATION');
  }
  const count = h.calls.length;
  assert.equal((await h.adapter.perform({action:'WAIT_FOR_READER_IDLE'}, h.context)).status, 'UNKNOWN');
  assert.equal(h.calls.length, count);
  await h.adapter.perform({action:'STOP'}, h.context);
  assert.equal(h.calls.at(-1), 'stop');
  await assert.rejects(() => h.adapter.start(h.context), /one-shot/);
});

test('forbidden keys and unapproved typing are refused before input', async () => {
  const h = fixture(); await h.adapter.start(h.context);
  for (const request of [{action:'KEY_CHORD', keyChord:'CTRL+L'},
    {action:'KEY_CHORD', keyChord:'CTRL+OPT+RIGHT'}, {action:'TYPE_TEXT', text:'private'}]) {
    await assert.rejects(() => h.adapter.perform(request, h.context));
  }
  assert.deepEqual(h.calls, ['start']);
});

test('cached, duplicate, reset and empty capture never become announcements', async () => {
  for (const change of [log => {}, log => log.push(log.at(-1)),
    log => { log.length = 0; log.push('replacement'); }, log => log.push('')]) {
    const h = fixture(); await h.adapter.start(h.context);
    h.client.next = async () => { change(h.speech); };
    assert.equal((await h.adapter.perform({action:'NEXT'}, h.context)).status, 'UNKNOWN');
  }
});

test('cancellation during readiness prevents input and leaves cleanup separately available', async () => {
  const h = fixture(); await h.adapter.start(h.context);
  h.context.assertReady = async () => { h.controller.abort(); };
  await assert.rejects(() => h.adapter.perform({action:'NEXT'}, h.context));
  assert.deepEqual(h.calls, ['start']);
  await h.adapter.stop();
  assert.deepEqual(h.calls, ['start', 'stop']);
});

test('overlap fences late action and never races STOP with an unresolved call', async () => {
  const h = fixture(); await h.adapter.start(h.context);
  let release;
  h.context.assertReady = () => new Promise(resolve => { release = resolve; });
  const pending = h.adapter.perform({action:'NEXT'}, h.context);
  await assert.rejects(() => h.adapter.stop(), /cleanup unavailable/);
  await assert.rejects(() => h.adapter.perform({action:'NEXT'}, h.context), /overlapping/);
  release();
  await assert.rejects(() => pending, /fenced/);
  assert.deepEqual(h.calls, ['start']);
});

test('production NVDA construction refuses before SDK import while matrix is empty', () => {
  assert.throws(() => createGuidepupNvdaAdapter([]), RealReaderUnavailable);
});

test('refused startup cannot stop an unrelated reader and failed STOP is not replayed', async () => {
  const first = fixture();
  first.context.assertReady = async () => { throw new Error('no authority'); };
  await assert.rejects(() => first.adapter.start(first.context));
  await assert.rejects(() => first.adapter.stop());
  assert.deepEqual(first.calls, []);
  const second = fixture(); await second.adapter.start(second.context);
  second.client.stop = async () => { second.calls.push('failed-stop'); throw new Error('unknown'); };
  await assert.rejects(() => second.adapter.perform({action:'STOP'}, second.context));
  await assert.rejects(() => second.adapter.stop());
  assert.deepEqual(second.calls, ['start', 'failed-stop']);
});
