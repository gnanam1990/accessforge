// Actual subprocess boundary with explicitly synthetic executables; no VoiceOver access.
import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { createVoiceOverCaptureProbe } from '../dist/capture-probe.js';

function helper(t, body) {
  const directory = realpathSync(mkdtempSync('/tmp/af-capture-test-'));
  t.after(() => rmSync(directory, {recursive: true, force: true}));
  const executable = join(directory, 'synthetic-probe');
  writeFileSync(executable, `#!${process.execPath}\n${body}\n`, {mode: 0o700});
  return executable;
}

test('only an exact bounded successful synthetic channel receipt establishes readiness', async t => {
  const path = helper(t, 'console.log(JSON.stringify({schemaVersion:1,status:"KNOWN",captureResponsive:true}));');
  const probe = createVoiceOverCaptureProbe(path);
  assert.equal(await probe(new AbortController().signal), true);
  chmodSync(path, 0o777);
  assert.equal(await probe(new AbortController().signal), false);
});

for (const body of [
  'console.log(JSON.stringify({schemaVersion:1,status:"UNKNOWN"})); process.exitCode=78;',
  'console.log(JSON.stringify({schemaVersion:1,status:"KNOWN",captureResponsive:true,phrase:"private"}));',
  'console.log("private".repeat(1000));',
  'console.log(JSON.stringify({schemaVersion:1,status:"KNOWN",captureResponsive:true})); process.exitCode=1;',
]) {
  test('unknown, malformed, oversized or failed synthetic output cannot assert readiness', async t => {
    assert.equal(await createVoiceOverCaptureProbe(helper(t, body))(new AbortController().signal), false);
  });
}

test('cancellation does not accept a late synthetic capture receipt', async t => {
  const path = helper(t, 'setTimeout(()=>console.log(JSON.stringify({schemaVersion:1,status:"KNOWN",captureResponsive:true})),3000);');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 50);
  try { assert.equal(await createVoiceOverCaptureProbe(path)(controller.signal), false); }
  finally { clearTimeout(timer); }
  assert.equal(await createVoiceOverCaptureProbe(path)(controller.signal), false);
});
