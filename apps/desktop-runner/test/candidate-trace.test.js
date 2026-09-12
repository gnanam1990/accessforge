import assert from 'node:assert/strict';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { digest } from '@accessforge/contracts';

import {
  CandidateTraceWriter,
  FileCandidateTraceSink,
  MemoryCandidateTraceSink,
} from '../dist/candidate-trace.js';

function writer(sink = new MemoryCandidateTraceSink()) {
  let id = 0;
  return {
    sink,
    trace: new CandidateTraceWriter({
      producerId: 'runner:voiceover:local',
      sink,
      sourceRecordId: () => `record-${++id}`,
      utc: () => '2026-09-12T12:00:00.000Z',
    }),
  };
}

test('candidate records are contiguous, digested and explicitly inadmissible as canonical evidence', async () => {
  const { trace, sink } = writer();
  const first = await trace.record('PREFLIGHT_RESULT', { checks: { READER_ACTIVE: 'TRUE' } });
  const second = await trace.record('READER_OBSERVATION', { phrase: 'Email address' });
  const closing = await trace.close();

  assert.equal(first.producerSequence, 1);
  assert.equal(second.producerSequence, 2);
  assert.equal(first.sourceRecordDigest, digest(first.sourceRecord));
  assert.equal(first.classification, 'CANDIDATE_PROOF');
  assert.equal(first.authenticationStatus, 'LOCAL_UNAUTHENTICATED');
  assert.equal(first.admissibleAsCanonicalEvidence, false);
  for (const forbidden of ['sequence', 'eventId', 'previousEventHash', 'receivedTime']) {
    assert.equal(forbidden in first, false, `${forbidden} is assigned only by trusted ingestion`);
  }
  assert.equal(closing.finalProducerSequence, 2);
  assert.deepEqual(closing.sourceRecordDigests, [
    first.sourceRecordDigest,
    second.sourceRecordDigest,
  ]);
  assert.deepEqual(sink.flushes, ['record-1', 'record-2', 'CLOSING_WATERMARK']);
});

test('a closed candidate trace refuses a hidden tail and close is idempotent', async () => {
  const { trace } = writer();
  await trace.record('RUN_FINISHED', { status: 'BLOCKED' });
  const first = await trace.close();
  assert.deepEqual(await trace.close(), first);
  await assert.rejects(
    () => trace.record('READER_OBSERVATION', { phrase: 'late' }),
    /already closed/,
  );
});

test('the file sink writes independently replayable fsynced JSONL', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'accessforge-candidate-trace-'));
  const path = join(directory, 'trace.jsonl');
  let id = 0;
  const trace = new CandidateTraceWriter({
    producerId: 'runner:voiceover:local',
    sink: new FileCandidateTraceSink(path),
    sourceRecordId: () => `disk-${++id}`,
    utc: () => '2026-09-12T12:00:00.000Z',
  });
  await trace.record('ACTION_INTENT', { actionId: 'lease:1:1', action: 'NEXT' });
  await trace.close();

  const lines = (await readFile(path, 'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(lines.length, 2);
  assert.equal(lines[0].kind, 'CANDIDATE_SOURCE_RECORD');
  assert.equal(lines[1].kind, 'CANDIDATE_CLOSING_WATERMARK');
});
