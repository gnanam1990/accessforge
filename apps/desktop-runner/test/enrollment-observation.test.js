import assert from 'node:assert/strict';
import test from 'node:test';
import { digest } from '@accessforge/contracts';
import { createHostEnvironment } from '@accessforge/at-voiceover';
import { observeEnrollment } from '../dist/enrollment-observation.js';
import { cli } from '../dist/main.js';

const deviceId = '12345678-1234-1234-1234-123456789abc';
const env = () => ({
  platform: () => 'darwin', deviceIdentifier: () => deviceId,
  auditSessionId: () => '100', processAuditSessionId: () => '100', screenLocked: () => false,
  processRunning: () => true, pathExists: () => false, hasPermission: () => { throw Error('not a permission probe'); },
  readPreference: (_domain, key) => key === 'ProductVersion' ? '26.6' : '25G72',
  browserVersion: () => '26.6', localeAndKeyboard: () => ({ locale: 'en_US', keyboardLayout: 'com.apple.keylayout.US' }),
});

test('read-only observation carries exact session/profile, not a token or qualification', async () => {
  const result = observeEnrollment(env());
  assert.deepEqual(result.session, { deviceId, platform: 'darwin', interactiveSessionId: '100', console: true });
  assert.equal(result.profileDigest, digest(result.profile));
  assert.equal(result.meaning, 'LOCAL_DECLARATION_NOT_ENROLLMENT_OR_QUALIFICATION');
  assert.equal('token' in result, false);
  const output = [], errors = [];
  assert.equal(await cli(['--enrollment-observation'], (s) => errors.push(s), env(), (s) => output.push(s)), 0);
  assert.deepEqual(JSON.parse(output[0]), result);
  assert.deepEqual(errors, []);
});

test('unknown, mismatched, locked or inactive desktop never produces enrollment JSON', async () => {
  for (const patch of [
    { platform: () => 'linux' }, { deviceIdentifier: () => undefined },
    { deviceIdentifier: () => '00000000-0000-0000-0000-000000000000' },
    { auditSessionId: () => undefined }, { processAuditSessionId: () => '101' },
    { screenLocked: () => undefined }, { screenLocked: () => true },
    { processRunning: () => false }, { browserVersion: () => undefined },
  ]) {
    const output = [];
    assert.equal(await cli(['--enrollment-observation'], () => {}, { ...env(), ...patch }, (s) => output.push(s)), 78);
    assert.deepEqual(output, []);
  }
});

test('console switch across observation is refused', () => {
  let reads = 0;
  assert.throws(() => observeEnrollment({ ...env(), auditSessionId: () => ++reads === 1 ? '100' : '101' }));
});

test('hardware probe uses fixed read-only command and rejects absent/ambiguous IDs', () => {
  let command;
  const probe = (stdout) => createHostEnvironment({ run: (exe, args) => {
    command = [exe, args]; return { status: 0, stdout, stderr: '' };
  } }).deviceIdentifier();
  const line = `"IOPlatformUUID" = "${deviceId.toUpperCase()}"`;
  assert.equal(probe(line), deviceId);
  assert.deepEqual(command, ['/usr/sbin/ioreg', ['-rd1', '-c', 'IOPlatformExpertDevice']]);
  for (const text of ['', `${line}\n${line}`, 'x'.repeat(65537)]) assert.equal(probe(text), undefined);
});
