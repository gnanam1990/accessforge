import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { chmodSync, linkSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { READER_STARTUP_EFFECTS_DIGEST } from '../dist/reader-startup-consent.js';
import { readStartupConsentReference } from '../dist/startup-provisioning.js';

function fixture(t) {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'accessforge-consent-reference-')));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const path = join(directory, 'consent.json');
  const reference = { workspaceId: randomUUID(), runId: randomUUID(), runnerId: randomUUID(),
    attemptId: randomUUID(), leaseId: randomUUID(), epoch: 1 };
  const scope = { consentId: randomUUID(), manifestDigest: 'a'.repeat(64), desktopSessionKey: 'b'.repeat(64),
    runnerProfileDigest: 'c'.repeat(64), effectsDigest: READER_STARTUP_EFFECTS_DIGEST };
  const row = { schemaVersion: 1, workspaceId: reference.workspaceId, runId: reference.runId,
    runnerId: reference.runnerId, consentExpiresAt: new Date(Date.now() + 60000).toISOString(), scope,
    meaning: 'OPERATOR_CONSENT_REFERENCE_NOT_EXECUTION_AUTHORITY' };
  const write = () => writeFileSync(path, JSON.stringify(row), { mode: 0o600 });
  write();
  return { directory, path, reference, scope, row, write };
}

test('private provisioned reference yields only immutable matching scope, not a credential', (t) => {
  const f = fixture(t);
  const result = readStartupConsentReference(f.path, f.reference);
  assert.deepEqual(result, f.scope);
  assert.ok(Object.isFrozen(result));
});

for (const fault of ['run', 'expiry', 'effects', 'extra', 'public', 'symlink', 'hardlink', 'oversize']) {
  test(`private provisioning refuses ${fault} before any reader or network action`, (t) => {
    const f = fixture(t);
    let path = f.path;
    if (fault === 'run') f.row.runId = randomUUID();
    if (fault === 'expiry') f.row.consentExpiresAt = '2020-01-01T00:00:00Z';
    if (fault === 'effects') f.row.scope.effectsDigest = 'f'.repeat(64);
    if (fault === 'extra') f.row.credential = 'not-accepted';
    f.write();
    if (fault === 'public') chmodSync(path, 0o644);
    if (fault === 'symlink') { path = join(f.directory, 'alias.json'); symlinkSync(f.path, path); }
    if (fault === 'hardlink') linkSync(path, join(f.directory, 'other.json'));
    if (fault === 'oversize') writeFileSync(path, 'x'.repeat(8193));
    assert.throws(() => readStartupConsentReference(path, f.reference), /no startup authority granted/);
  });
}
