/**
 * Durable local output for the first actual-reader capability proof.
 *
 * This is intentionally not an EvidenceEnvelope. Canonical sequence, event IDs, authentication and
 * chain links belong to trusted server ingestion. A local runner cannot manufacture those fields,
 * so every line here is labelled CANDIDATE_PROOF and LOCAL_UNAUTHENTICATED. Module 10 may later
 * submit the underlying source records using a real supervisor credential.
 */

import { closeSync, fsyncSync, mkdirSync, openSync, writeSync } from 'node:fs';
import { dirname } from 'node:path';

import { digest } from '@accessforge/contracts';

export const CANDIDATE_RECORD_TYPES = [
  'PREFLIGHT_RESULT',
  'ACTION_INTENT',
  'ACTION_RESULT',
  'READER_OBSERVATION',
  'BUDGET_EVENT',
  'INTERRUPTION',
  'RUN_FINISHED',
] as const;

export type CandidateRecordType = (typeof CANDIDATE_RECORD_TYPES)[number];

export interface CandidateSourceRecord {
  readonly kind: 'CANDIDATE_SOURCE_RECORD';
  readonly schemaVersion: 1;
  readonly classification: 'CANDIDATE_PROOF';
  readonly authenticationStatus: 'LOCAL_UNAUTHENTICATED';
  readonly admissibleAsCanonicalEvidence: false;
  readonly producerId: string;
  readonly sourceRecordId: string;
  readonly producerSequence: number;
  readonly sourceRecordDigest: string;
  readonly sourceTime: string;
  readonly sourceRecord: {
    readonly type: CandidateRecordType;
    readonly payload: unknown;
  };
}

export interface CandidateClosingWatermark {
  readonly kind: 'CANDIDATE_CLOSING_WATERMARK';
  readonly schemaVersion: 1;
  readonly classification: 'CANDIDATE_PROOF';
  readonly authenticationStatus: 'LOCAL_UNAUTHENTICATED';
  readonly admissibleAsCanonicalEvidence: false;
  readonly producerId: string;
  readonly finalProducerSequence: number;
  readonly sourceRecordDigests: readonly string[];
  readonly closedAt: string;
}

export type CandidateTraceLine = CandidateSourceRecord | CandidateClosingWatermark;

export interface CandidateTraceSink {
  /** Append exactly one JSONL line and resolve only after the bytes are durable. */
  appendAndFlush(line: CandidateTraceLine): Promise<void>;
}

export class FileCandidateTraceSink implements CandidateTraceSink {
  constructor(private readonly path: string) {}

  async appendAndFlush(line: CandidateTraceLine): Promise<void> {
    mkdirSync(dirname(this.path), { recursive: true });
    const fd = openSync(this.path, 'a', 0o600);
    try {
      writeSync(fd, `${JSON.stringify(line)}\n`);
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
  }
}

export class MemoryCandidateTraceSink implements CandidateTraceSink {
  readonly lines: CandidateTraceLine[] = [];
  readonly flushes: string[] = [];

  async appendAndFlush(line: CandidateTraceLine): Promise<void> {
    this.lines.push(line);
    this.flushes.push(
      line.kind === 'CANDIDATE_SOURCE_RECORD' ? line.sourceRecordId : 'CLOSING_WATERMARK',
    );
  }
}

export interface CandidateTraceWriterOptions {
  readonly producerId: string;
  readonly sink: CandidateTraceSink;
  readonly sourceRecordId: () => string;
  readonly utc: () => string;
}

export class CandidateTraceWriter {
  private producerSequence = 0;
  private readonly sourceRecordDigests: string[] = [];
  private closing: CandidateClosingWatermark | undefined;

  constructor(private readonly options: CandidateTraceWriterOptions) {
    if (options.producerId.trim() === '') throw new Error('candidate trace needs a producer id');
  }

  async record(type: CandidateRecordType, payload: unknown): Promise<CandidateSourceRecord> {
    if (this.closing !== undefined) {
      throw new Error('candidate trace is already closed; a hidden producer tail is forbidden');
    }
    this.producerSequence += 1;
    const sourceRecord = { type, payload } as const;
    const line: CandidateSourceRecord = {
      kind: 'CANDIDATE_SOURCE_RECORD',
      schemaVersion: 1,
      classification: 'CANDIDATE_PROOF',
      authenticationStatus: 'LOCAL_UNAUTHENTICATED',
      admissibleAsCanonicalEvidence: false,
      producerId: this.options.producerId,
      sourceRecordId: this.options.sourceRecordId(),
      producerSequence: this.producerSequence,
      sourceRecordDigest: digest(sourceRecord),
      sourceTime: this.options.utc(),
      sourceRecord,
    };
    await this.options.sink.appendAndFlush(line);
    this.sourceRecordDigests.push(line.sourceRecordDigest);
    return line;
  }

  async close(): Promise<CandidateClosingWatermark> {
    if (this.closing !== undefined) return this.closing;
    const line: CandidateClosingWatermark = {
      kind: 'CANDIDATE_CLOSING_WATERMARK',
      schemaVersion: 1,
      classification: 'CANDIDATE_PROOF',
      authenticationStatus: 'LOCAL_UNAUTHENTICATED',
      admissibleAsCanonicalEvidence: false,
      producerId: this.options.producerId,
      finalProducerSequence: this.producerSequence,
      sourceRecordDigests: [...this.sourceRecordDigests],
      closedAt: this.options.utc(),
    };
    await this.options.sink.appendAndFlush(line);
    this.closing = line;
    return line;
  }
}
