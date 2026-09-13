import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:net';
import { chmodSync, mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createArtifactProbe } from '../dist/artifact-probe.js';

// Real local sockets with synthetic measurements. No candidate/reader or deployment is invoked.
for (const fault of [undefined, 'stale', 'foreign', 'oversize']) {
  test(`private artifact probe ${fault ?? 'fresh measurement and sealed mismatch'}`, { skip: process.platform === 'win32' }, async () => {
    const directory = realpathSync(mkdtempSync(join(tmpdir(), 'afp-')));
    const socketPath = join(directory, 'read.sock');
    const reference = { protocol: 'accessforge.artifact-probe.v1', socketPath, token: 'c'.repeat(64),
      taskId: 'fixture-task', candidateId: 'fixture-candidate', imageId: 'fixture-image', daemonId: 'fixture-daemon' };
    const server = createServer((socket) => {
      let request = '';
      socket.on('error', () => {});
      socket.on('data', (chunk) => {
        request += chunk;
        if (!request.endsWith('\n')) return;
        const parsed = JSON.parse(request);
        assert.equal(parsed.token, reference.token);
        const observation = { taskId: reference.taskId, candidateId: reference.candidateId,
          imageId: reference.imageId, daemonId: reference.daemonId, artifactDigest: 'a'.repeat(64),
          artifactTreeDigest: 'b'.repeat(64), observedAt: new Date().toISOString(),
          meaning: 'DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION' };
        if (fault === 'stale') observation.observedAt = '2000-01-01T00:00:00Z';
        if (fault === 'foreign') observation.candidateId = 'other-candidate';
        socket.end(fault === 'oversize' ? 'x'.repeat(8193) : JSON.stringify({
          protocol: reference.protocol, requestId: parsed.requestId, observation,
        }) + '\n');
      });
    });
    try {
      await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socketPath, resolve); });
      chmodSync(socketPath, 0o600);
      const probe = createArtifactProbe({ reference, expectedBuildDigest: 'd'.repeat(64) });
      const signal = new AbortController().signal;
      if (fault === undefined) {
        assert.deepEqual(await probe(signal), { expectedBuildDigest: 'd'.repeat(64), observedBuildDigest: 'a'.repeat(64) });
        assert.equal((await probe(signal)).observedBuildDigest, 'a'.repeat(64));
      } else {
        await assert.rejects(probe(signal), /unavailable/);
        await assert.rejects(probe(signal), /fenced/);
      }
    } finally {
      await new Promise((resolve) => server.close(resolve));
      rmSync(directory, { recursive: true, force: true });
    }
  });
}
