/**
 * Durable local output for the first actual-reader capability proof.
 *
 * This is intentionally not an EvidenceEnvelope. Canonical sequence, event IDs, authentication and
 * chain links belong to trusted server ingestion. A local runner cannot manufacture those fields,
 * so every line here is labelled CANDIDATE_PROOF and LOCAL_UNAUTHENTICATED. Module 10 may later
 * submit the underlying source records using a real supervisor credential.
 */

import { closeSync, constants, fstatSync, fsyncSync, lstatSync, openSync, realpathSync, writeSync, type Stats } from 'node:fs';
import { dirname, isAbsolute, resolve } from 'node:path';

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
  private identity: Stats | undefined;
  private parentIdentity: Stats | undefined;
  private size = 0;
  private fenced = false;
  constructor(private readonly path: string) {}

  async appendAndFlush(line: CandidateTraceLine): Promise<void> {
    let fd: number | undefined;
    let directory: number | undefined;
    try {
      if (this.fenced) throw new Error('candidate trace storage fenced');
      const parent = dirname(this.path);
      if (!isAbsolute(this.path) || realpathSync(parent) !== resolve(parent)) throw new Error('private trace directory required');
      directory = openSync(parent, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
      const root = fstatSync(directory);
      const owned = (info: Stats) => typeof process.getuid === 'function' &&
        info.uid === process.getuid() && (info.mode & 0o077) === 0;
      const same = (a: Stats, b: Stats) => a.dev === b.dev && a.ino === b.ino;
      if (!root.isDirectory() || !owned(root) || !same(root, lstatSync(parent)) ||
          (this.parentIdentity !== undefined && !same(root, this.parentIdentity))) throw new Error('trace directory changed');
      fd = openSync(this.path, constants.O_WRONLY | constants.O_APPEND | constants.O_NOFOLLOW |
        constants.O_NONBLOCK | (this.identity === undefined ? constants.O_CREAT | constants.O_EXCL : 0), 0o600);
      const info = fstatSync(fd);
      if (!info.isFile() || !owned(info) || info.nlink !== 1 || info.size !== this.size ||
          (this.identity !== undefined && !same(info, this.identity))) throw new Error('trace file changed');
      const bytes = Buffer.from(`${JSON.stringify(line)}\n`);
      let offset = 0;
      while (offset < bytes.length) {
        const count = writeSync(fd, bytes, offset, bytes.length - offset);
        if (count <= 0) throw new Error('candidate trace write incomplete');
        offset += count;
      }
      fsyncSync(fd);
      fsyncSync(directory);
      if (!same(root, lstatSync(parent)) || !same(info, lstatSync(this.path))) throw new Error('trace storage replaced');
      this.identity = info;
      this.parentIdentity = root;
      this.size += bytes.length;
    } catch (error) {
      // Retain any partial original bytes for reconciliation. Never append a new run or retry
      // an uncertain flush into what might look like a complete producer history.
      this.fenced = true;
      throw error;
    } finally {
      if (fd !== undefined) closeSync(fd);
      if (directory !== undefined) closeSync(directory);
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
