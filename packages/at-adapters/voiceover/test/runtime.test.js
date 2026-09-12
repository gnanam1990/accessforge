import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  GUIDEPUP_CHORDS,
  VoiceOverAdapter,
  createHostEnvironment,
} from '../dist/index.js';

function fakeClient(overrides = {}) {
  const calls = [];
  return {
    calls,
    name: 'VoiceOver',
    version: 'bundled',
    detect: () => true,
    start: async () => calls.push(['start']),
    stop: async () => calls.push(['stop']),
    next: async () => calls.push(['next']),
    previous: async () => calls.push(['previous']),
    act: async () => calls.push(['act']),
    type: async (text) => calls.push(['type', text]),
    press: async (key) => calls.push(['press', key]),
    itemText: async () => {
      calls.push(['itemText']);
      return 'Current field';
    },
    lastSpokenPhrase: async () => {
      calls.push(['lastSpokenPhrase']);
      return 'Spoken phrase';
    },
    ...overrides,
  };
}

function context(sequence = 1) {
  return {
    actionId: `lease:7:${sequence}`,
    actionSequence: sequence,
    capturedAtUtc: () => '2026-09-12T08:00:00Z',
  };
}

test('the real adapter maps only the eight sealed actions onto Guidepup', async () => {
  const client = fakeClient();
  const adapter = new VoiceOverAdapter(client);

  await adapter.start();
  await adapter.perform({ action: 'NEXT' }, context(1));
  await adapter.perform({ action: 'PREVIOUS' }, context(2));
  await adapter.perform({ action: 'ACTIVATE' }, context(3));
  await adapter.perform({ action: 'TYPE_TEXT', text: 'Test Person' }, context(4));
  await adapter.perform({ action: 'KEY_CHORD', keyChord: 'SHIFT+TAB' }, context(5));
  await adapter.perform({ action: 'READ_CURRENT' }, context(6));
  await adapter.stop();

  assert.deepEqual(client.calls, [
    ['start'],
    ['next'],
    ['lastSpokenPhrase'],
    ['previous'],
    ['lastSpokenPhrase'],
    ['act'],
    ['lastSpokenPhrase'],
    ['type', 'Test Person'],
    ['lastSpokenPhrase'],
    ['press', 'Shift+Tab'],
    ['lastSpokenPhrase'],
    ['itemText'],
    ['stop'],
  ]);
});

test('every policy chord has one exact Guidepup key spelling', () => {
  assert.deepEqual(GUIDEPUP_CHORDS, {
    TAB: 'Tab',
    'SHIFT+TAB': 'Shift+Tab',
    ENTER: 'Enter',
    SPACE: 'Space',
    ESCAPE: 'Escape',
    'CTRL+OPT+RIGHT': 'Control+Alt+ArrowRight',
    'CTRL+OPT+LEFT': 'Control+Alt+ArrowLeft',
  });
});

test('reader output is constructed as actual-reader evidence with action provenance', async () => {
  const adapter = new VoiceOverAdapter(fakeClient());
  const result = await adapter.perform({ action: 'NEXT' }, context(9));

  assert.equal(result.status, 'SUCCEEDED');
  assert.deepEqual(result.observation, {
    phrase: 'Spoken phrase',
    capturedAtUtc: '2026-09-12T08:00:00Z',
    actionId: 'lease:7:9',
    actionSequence: 9,
  });
});

test('an empty announcement remains a real empty observation', async () => {
  const client = fakeClient({ lastSpokenPhrase: async () => '' });
  const result = await new VoiceOverAdapter(client).perform({ action: 'NEXT' }, context());
  assert.equal(result.observation.phrase, '');
});

test('a Guidepup action error stays unresolved instead of becoming FAILED', async () => {
  const client = fakeClient({ act: async () => Promise.reject(new Error('AppleEvent timed out')) });
  await assert.rejects(
    () => new VoiceOverAdapter(client).perform({ action: 'ACTIVATE' }, context()),
    /AppleEvent timed out/,
  );
});

test('STOP tears down VoiceOver and never fabricates an observation', async () => {
  const client = fakeClient();
  const result = await new VoiceOverAdapter(client).perform({ action: 'STOP' }, context());
  assert.deepEqual(result, { status: 'SUCCEEDED' });
  assert.deepEqual(client.calls, [['stop']]);
});

test('reader-idle timeout is unknown evidence, not empty speech', async () => {
  let now = 0;
  const phrases = ['one', 'two', 'three', 'four'];
  const client = fakeClient({ lastSpokenPhrase: async () => phrases.shift() ?? 'five' });
  const adapter = new VoiceOverAdapter(client, {
    idlePollMs: 10,
    idleTimeoutMs: 25,
    idleStableSamples: 2,
    monotonicNow: () => now,
    sleep: async (ms) => {
      now += ms;
    },
  });

  const result = await adapter.perform({ action: 'WAIT_FOR_READER_IDLE' }, context());
  assert.equal(result.status, 'SUCCEEDED');
  assert.equal(result.observation.provenance, 'CAPTURE_UNKNOWN');
  assert.match(result.observation.reason, /unknown evidence, not silence/);
  assert.equal('phrase' in result.observation, false);
});

test('reader-idle ignores a stable stale phrase until a new announcement stabilizes', async () => {
  let now = 0;
  const phrases = ['Email', 'Email', 'Email', 'Invalid email', 'Invalid email'];
  const client = fakeClient({ lastSpokenPhrase: async () => phrases.shift() ?? 'Invalid email' });
  const adapter = new VoiceOverAdapter(client, {
    idlePollMs: 10,
    idleTimeoutMs: 100,
    idleStableSamples: 2,
    monotonicNow: () => now,
    sleep: async (ms) => {
      now += ms;
    },
  });

  const result = await adapter.perform({ action: 'WAIT_FOR_READER_IDLE' }, context());
  assert.equal(result.status, 'SUCCEEDED');
  assert.equal(result.observation.phrase, 'Invalid email');
});

test('reader-idle returns unknown when only the previous announcement is observable', async () => {
  let now = 0;
  const client = fakeClient({ lastSpokenPhrase: async () => 'Email' });
  const adapter = new VoiceOverAdapter(client, {
    idlePollMs: 10,
    idleTimeoutMs: 25,
    idleStableSamples: 2,
    monotonicNow: () => now,
    sleep: async (ms) => {
      now += ms;
    },
  });

  const result = await adapter.perform({ action: 'WAIT_FOR_READER_IDLE' }, context());
  assert.equal(result.observation.provenance, 'CAPTURE_UNKNOWN');
  assert.equal('phrase' in result.observation, false);
});

test('host probes use real command results and preserve unknowns', () => {
  const consoleState = JSON.stringify({
    IOConsoleUsers: [
      {
        kCGSSessionOnConsoleKey: true,
        kCGSessionLoginDoneKey: true,
        kCGSSessionAuditIDKey: 100025,
      },
    ],
  });
  const run = (executable, args, input) => {
    const invocation = [executable, ...args].join(' ');
    if (invocation.includes('ioreg')) return { status: 0, stdout: consoleState, stderr: '' };
    if (invocation.includes('plutil')) return { status: 0, stdout: input, stderr: '' };
    if (invocation.includes('ProductVersion')) return { status: 0, stdout: '26.6\n', stderr: '' };
    if (invocation.includes('SCREnableAppleScript')) {
      return { status: 0, stdout: '1\n', stderr: '' };
    }
    if (invocation.includes('pgrep')) return { status: 0, stdout: '123\n', stderr: '' };
    if (invocation.includes('AXIsProcessTrusted')) {
      return { status: 0, stdout: 'TRUE\n', stderr: '' };
    }
    if (invocation.includes('AEDeterminePermissionToAutomateTarget')) {
      return { status: 0, stdout: '0\n', stderr: '' };
    }
    return { status: 1, stdout: '', stderr: 'unknown command' };
  };
  const env = createHostEnvironment({
    run,
    pathExists: () => true,
  });

  assert.equal(env.readPreference('/System/Library/CoreServices/SystemVersion', 'ProductVersion'), '26.6');
  assert.equal(env.readPreference('com.apple.VoiceOver4/default', 'SCREnableAppleScript'), '1');
  assert.equal(env.processRunning('VoiceOver'), true);
  assert.equal(env.auditSessionId(), '100025');
  assert.equal(env.screenLocked(), false);
  assert.equal(env.hasPermission('Accessibility'), true);
  assert.equal(env.hasPermission('Automation'), true);
});

test('a missing console lock property is unlocked only for the active completed console login', () => {
  const state = JSON.stringify({
    IOConsoleUsers: [{ kCGSSessionOnConsoleKey: false, kCGSessionLoginDoneKey: true }],
  });
  const env = createHostEnvironment({
    run: (_file, args) =>
      args.includes('-n')
        ? { status: 0, stdout: state, stderr: '' }
        : { status: 1, stdout: '', stderr: '' },
    pathExists: () => false,
  });
  assert.equal(env.screenLocked(), undefined);
  assert.equal(env.auditSessionId(), undefined);
});
