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
