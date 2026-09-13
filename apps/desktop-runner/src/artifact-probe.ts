/** Private same-host measurement client. Never pass its capability to browser/navigator code. */
import { randomBytes } from 'node:crypto';
import { lstatSync, realpathSync } from 'node:fs';
import { createConnection } from 'node:net';
import { dirname, isAbsolute, resolve } from 'node:path';

const PROTOCOL = 'accessforge.artifact-probe.v1';
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const object = (value: unknown): Record<string, unknown> => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('shape');
  return value as Record<string, unknown>;
};
function closed(value: Record<string, unknown>, keys: readonly string[]): void {
  if (Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))) throw new Error('shape');
}

export interface ArtifactProbeOptions {
  /** Operator/controller-provisioned reference from the live Python CandidateSession. */
  readonly reference: unknown;
  /** Independently loaded from the authorized seal, never copied from the probe response. */
  readonly expectedBuildDigest: string;
}

export function createArtifactProbe(options: ArtifactProbeOptions): (signal: AbortSignal) => Promise<{
  expectedBuildDigest: string; observedBuildDigest: string;
}> {
  let reference: Readonly<Record<string, unknown>>;
  const expectedBuildDigest = options.expectedBuildDigest;
  try {
    const value = object(options.reference);
    closed(value, ['protocol', 'socketPath', 'token', 'taskId', 'candidateId', 'imageId', 'daemonId']);
    if (value.protocol !== PROTOCOL || !hash(value.token) || !hash(expectedBuildDigest) ||
        typeof value.socketPath !== 'string' || !isAbsolute(value.socketPath) ||
        ['taskId', 'candidateId', 'imageId', 'daemonId'].some((key) =>
          typeof value[key] !== 'string' || value[key].length < 1 || value[key].length > 200)) throw new Error('binding');
    reference = Object.freeze({ ...value });
  } catch {
    throw new Error('private artifact probe configuration unavailable');
  }
  const path = reference.socketPath as string;
  const checkPath = () => {
    if (typeof process.getuid !== 'function' || realpathSync(path) !== resolve(path)) throw new Error('path');
    const parent = lstatSync(dirname(path));
    const socket = lstatSync(path);
    if (!parent.isDirectory() || parent.uid !== process.getuid() || (parent.mode & 0o077) !== 0 ||
        !socket.isSocket() || socket.uid !== process.getuid() || (socket.mode & 0o077) !== 0) throw new Error('private socket');
    return socket;
  };
  let busy = false;
  let fenced = false;
  return async (signal) => {
    if (busy || fenced || signal.aborted) { fenced = true; throw new Error('artifact probe fenced'); }
    busy = true;
    try {
      const before = checkPath();
      const started = Date.now();
      const requestId = randomBytes(16).toString('hex');
      const response = await new Promise<Buffer>((resolveResponse, reject) => {
        const socket = createConnection({ path });
        const chunks: Buffer[] = [];
        let size = 0;
        let settled = false;
        const finish = (error?: Error) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          signal.removeEventListener('abort', abort);
          socket.destroy();
          if (error) reject(error); else resolveResponse(Buffer.concat(chunks));
        };
        const abort = () => finish(new Error('cancelled'));
        const timer = setTimeout(() => finish(new Error('deadline')), 6000);
        signal.addEventListener('abort', abort, { once: true });
        socket.once('connect', () => {
          socket.write(JSON.stringify({ protocol: PROTOCOL, requestId, token: reference.token }) + '\n');
        });
        socket.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > 8192) { finish(new Error('size')); return; }
          chunks.push(chunk);
        });
        socket.once('end', () => finish());
        socket.once('error', () => finish(new Error('transport')));
        socket.once('close', () => finish(new Error('closed without response')));
        if (signal.aborted) abort();
      });
      const after = checkPath();
      if (signal.aborted || fenced || before.dev !== after.dev || before.ino !== after.ino ||
          before.ctimeMs !== after.ctimeMs || response.length === 0 || response.at(-1) !== 10 ||
          response.subarray(0, -1).includes(10)) throw new Error('changed probe');
      const result = object(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(response)));
      closed(result, ['protocol', 'requestId', 'observation']);
      if (result.protocol !== PROTOCOL || result.requestId !== requestId) throw new Error('response binding');
      const observation = object(result.observation);
      closed(observation, ['taskId', 'candidateId', 'imageId', 'daemonId', 'artifactDigest', 'artifactTreeDigest', 'observedAt', 'meaning']);
      if (['taskId', 'candidateId', 'imageId', 'daemonId'].some((key) => observation[key] !== reference[key]) ||
          !hash(observation.artifactDigest) || !hash(observation.artifactTreeDigest) ||
          observation.meaning !== 'DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION' ||
          typeof observation.observedAt !== 'string' ||
          !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(observation.observedAt)) throw new Error('measurement');
      const measured = Date.parse(observation.observedAt);
      if (!Number.isFinite(measured) || measured < started || measured > Date.now()) throw new Error('stale');
      return { expectedBuildDigest, observedBuildDigest: observation.artifactDigest };
    } catch {
      fenced = true;
      throw new Error('live artifact measurement unavailable; no cached build identity or automatic retry');
    } finally {
      busy = false;
    }
  };
}
