import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { publishNativeHandoff } from '../dist/native-host.js';
import { cli } from '../dist/main.js';

function directory(t) {
  const path = realpathSync(mkdtempSync('/tmp/af-native-host-'));
  t.after(() => rmSync(path, {recursive: true, force: true}));
  return path;
}

test('private handoff is retained without overwrite, symlink following or public permissions', t => {
  const root = directory(t), path = join(root, 'handoff.json');
  publishNativeHandoff(path, {synthetic: true});
  assert.deepEqual(JSON.parse(readFileSync(path, 'utf8')), {synthetic: true});
  assert.equal(statSync(path).mode & 0o077, 0);
  assert.throws(() => publishNativeHandoff(path, {replacement: true}));
  const alias = join(root, 'alias');
  symlinkSync(path, alias);
  assert.throws(() => publishNativeHandoff(alias, {replacement: true}));
  assert.deepEqual(JSON.parse(readFileSync(path, 'utf8')), {synthetic: true});
  chmodSync(root, 0o755);
  assert.throws(() => publishNativeHandoff(join(root, 'public.json'), {}));
});

test('unqualified native host refuses before operator code import or publishing credentials', async t => {
  const root = directory(t), modulePath = join(root, 'operator.mjs');
  writeFileSync(modulePath, 'globalThis.__afHostImportTest = true; throw new Error("private-marker-must-not-be-returned")', {mode: 0o600});
  t.after(() => { delete globalThis.__afHostImportTest; });
  const lines = [];
  assert.equal(await cli(['--native-host', modulePath, '--handoff-file', join(root, 'handoff')], line => lines.push(line)), 78);
  assert.deepEqual(readdirSync(root), ['operator.mjs']);
  assert.equal(globalThis.__afHostImportTest, undefined);
  assert.ok(lines.every(line => !line.includes('private-marker')));
});

test('malformed native host command refuses without importing configuration', async () => {
  assert.equal(await cli(['--native-host', '/missing/operator.mjs'], () => {}), 64);
});

test('candidate command requires explicit reader-startup option before importing operator code', async t => {
  const root = directory(t), modulePath = join(root, 'candidate.mjs');
  writeFileSync(modulePath, 'globalThis.__afCandidateImported = true;', {mode: 0o600});
  t.after(() => { delete globalThis.__afCandidateImported; });
  assert.equal(await cli(['--candidate-proof', modulePath, '--output-dir', join(root, 'proof')], () => {}), 64);
  assert.equal(globalThis.__afCandidateImported, undefined);
  assert.deepEqual(readdirSync(root), ['candidate.mjs']);
});

test('candidate provisioner failure is sanitized and never creates proof output', async t => {
  const root = directory(t), modulePath = join(root, 'candidate.mjs');
  writeFileSync(modulePath, 'export async function provisionCandidateProof() { throw new Error("private-candidate-marker"); }', {mode: 0o600});
  const lines = [];
  assert.equal(await cli(['--candidate-proof', modulePath, '--output-dir', join(root, 'proof'), '--allow-reader-startup'], v => lines.push(v)), 78);
  assert.ok(lines.every(line => !line.includes('private-candidate-marker')));
  assert.deepEqual(readdirSync(root), ['candidate.mjs']);
});

test('incomplete candidate configuration is refused before trace, claim or adapter creation', async t => {
  const root = directory(t), modulePath = join(root, 'candidate.mjs');
  writeFileSync(modulePath, 'export async function provisionCandidateProof() { return {}; }', {mode: 0o600});
  assert.equal(await cli(['--candidate-proof', modulePath, '--output-dir', join(root, 'proof'), '--allow-reader-startup'], () => {}), 78);
  assert.deepEqual(readdirSync(root), ['candidate.mjs']);
});

for (const preparation of [false, true]) test(`assembled candidate host retains its claim on ${preparation ? 'invalid actions before setup' : 'unavailable runtime'}`, async t => {
  const root = directory(t), modulePath = join(root, 'candidate.mjs');
  const reference = { workspaceId: '00000000-0000-0000-0000-000000000001',
    runId: '00000000-0000-0000-0000-000000000002', attemptId: '00000000-0000-0000-0000-000000000003',
    runnerId: '00000000-0000-0000-0000-000000000004', leaseId: '00000000-0000-0000-0000-000000000005', epoch: 1 };
  const artifactProbe = { expectedBuildDigest: 'a'.repeat(64), reference: {
    protocol: 'accessforge.artifact-probe.v1', socketPath: join(root, 'unused.sock'), token: 'b'.repeat(64),
    taskId: 'synthetic', candidateId: 'synthetic', imageId: 'synthetic', daemonId: 'synthetic' } };
  globalThis.__afCandidateSetupReached = false;
  t.after(() => { delete globalThis.__afCandidateSetupReached; });
  writeFileSync(modulePath, `export async function provisionCandidateProof() { return {
    desktop: ${JSON.stringify({directory: root, desktopSessionId: '123', reference})},
    physicalPreflight: { expectedDesktopSessionId: '123', artifactProbe: ${JSON.stringify(artifactProbe)},
      async observeRuntimeEvidence() { throw new Error('synthetic unavailable source'); } },
    safari: { expectedUrl: 'http://127.0.0.1:8081/form/synthetic-fixture-123', expectedBrowserVersion: '26.6' },
    ${preparation ? `referencePreparation: { fixture: {
      permittedOrigin: 'http://127.0.0.1:8081', reservedNonce: 'synthetic-fixture-123',
      setupToken: 'synthetic-only', variant: 'accessible', expectedBuildDigest: '${'a'.repeat(64)}',
      expectedFixtureDigest: '${'c'.repeat(64)}'
    }, async authorize() { globalThis.__afCandidateSetupReached = true; throw new Error('unexpected setup'); } },` : ''}
    actions: ${JSON.stringify(preparation ? [{action: 'TYPE_TEXT', text: 'not-approved'}] : [{action: 'NEXT'}])},
    approvedTextValues: [], maxDurationSeconds: 60, actionTimeoutMs: 100,
    async authorizeStartup() { throw new Error('must never reach reader startup'); },
    async authorizeAction() { throw new Error('must never reach physical action'); }
  }; }`, {mode: 0o600});
  const args = ['--candidate-proof', modulePath, '--output-dir', join(root, 'proof'), '--allow-reader-startup'];
  assert.equal(await cli(args, () => {}), 78);
  assert.equal(globalThis.__afCandidateSetupReached, false);
  const claimPath = join(root, 'desktop-123.json');
  const claim = readFileSync(claimPath, 'utf8');
  assert.deepEqual(JSON.parse(claim).reference, reference);
  assert.deepEqual(readdirSync(join(root, 'proof')), []);
  assert.equal(await cli(args, () => {}), 78); // Existing output never replays or releases the claim.
  assert.equal(readFileSync(claimPath, 'utf8'), claim);
});
