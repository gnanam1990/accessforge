import assert from 'node:assert/strict';
import test from 'node:test';
import { createHostEnvironment, observeRunnerProfile, parseObservedRunnerProfile } from '../dist/index.js';

const input = { locale: 'en_US', keyboardLayout: 'com.apple.keylayout.US' };
const env = () => ({ platform: () => 'darwin', processRunning: () => true,
  readPreference: (_domain, key) => key === 'ProductVersion' ? '26.6' : '25G72',
  browserVersion: () => '26.6', localeAndKeyboard: () => input });

test('complete profile is constructed from observed values, not the target matrix', () => {
  const profile = observeRunnerProfile(env());
  assert.deepEqual(profile, { platform: 'darwin', readerName: 'VoiceOver',
    readerVersion: 'bundled with macOS 26.6 (build 25G72)', browserName: 'Safari',
    browserVersion: '26.6', locale: 'en-US', keyboardLayout: 'com.apple.keylayout.US' });
  assert.equal(Object.isFrozen(profile), true);
  assert.equal(observeRunnerProfile({ ...env(), browserVersion: () => '26.7' }).browserVersion, '26.7');
});

test('missing, inactive, foreign or private fields never acquire target defaults', () => {
  for (const patch of [{ platform: () => 'linux' }, { processRunning: () => false },
    { readPreference: () => undefined }, { browserVersion: () => undefined },
    { localeAndKeyboard: () => undefined }, { localeAndKeyboard: () => ({ ...input, locale: 'bad@@@' }) }]) {
    assert.equal(observeRunnerProfile({ ...env(), ...patch }), undefined);
  }
  assert.equal(parseObservedRunnerProfile({ ...observeRunnerProfile(env()), privatePath: '/private' }), undefined);
  assert.equal(parseObservedRunnerProfile({ ...observeRunnerProfile(env()), locale: ['en-US'] }), undefined);
});

test('native input-source probe reads only and fails closed on bad output', () => {
  let command;
  const native = createHostEnvironment({ run: (exe, args) => {
    command = [exe, args];
    return { status: 0, stdout: JSON.stringify(input), stderr: '' };
  } });
  assert.deepEqual(native.localeAndKeyboard(), input);
  assert.equal(command[0], '/usr/bin/xcrun');
  assert.match(command[1][2], /TISCopyCurrentKeyboardInputSource/);
  assert.doesNotMatch(command[1][2], /TISSelectInputSource|defaults write|osascript/);
  for (const stdout of ['null', '{"locale": 4}', 'x'.repeat(1025)]) {
    assert.equal(createHostEnvironment({ run: () => ({ status: 0, stdout, stderr: '' }) }).localeAndKeyboard(), undefined);
  }
});
