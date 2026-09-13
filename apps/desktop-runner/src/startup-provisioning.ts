/** Read a private operator-exported reference. This file is never an execution credential. */
import { constants, closeSync, fstatSync, openSync, readSync, realpathSync } from 'node:fs';
import { isAbsolute, resolve } from 'node:path';
import { parseReference, type DispatchReference } from './dispatch-receiver.js';
import { parseReaderStartupConsentScope, type ReaderStartupConsentScope } from './reader-startup-consent.js';

export function readStartupConsentReference(path: string, expected: DispatchReference): Readonly<ReaderStartupConsentScope> {
  let fd: number | undefined;
  try {
    const reference = parseReference(expected);
    if (!isAbsolute(path) || realpathSync(path) !== resolve(path)) throw new Error('path');
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const before = fstatSync(fd);
    if (typeof process.getuid !== 'function' || before.uid !== process.getuid() ||
        (before.mode & 0o077) !== 0 || !before.isFile() || before.nlink !== 1 || before.size > 8192) throw new Error('private file');
    const bytes = Buffer.alloc(8193);
    let size = 0;
    while (size < bytes.length) {
      const n = readSync(fd, bytes, size, bytes.length - size, null);
      if (n === 0) break;
      size += n;
    }
    const after = fstatSync(fd);
    if (size > 8192 || size !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs ||
        after.ctimeMs !== before.ctimeMs || after.nlink !== 1 || after.mode !== before.mode) throw new Error('changed file');
    const row: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, size)));
    if (row === null || typeof row !== 'object' || Array.isArray(row)) throw new Error('shape');
    const value = row as Record<string, unknown>;
    const keys = ['schemaVersion', 'workspaceId', 'runId', 'runnerId', 'consentExpiresAt', 'scope', 'meaning'];
    if (Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key)) ||
        value.schemaVersion !== 1 || value.workspaceId !== reference.workspaceId || value.runId !== reference.runId ||
        value.runnerId !== reference.runnerId || value.meaning !== 'OPERATOR_CONSENT_REFERENCE_NOT_EXECUTION_AUTHORITY' ||
        typeof value.consentExpiresAt !== 'string' ||
        !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value.consentExpiresAt) ||
        !Number.isFinite(Date.parse(value.consentExpiresAt)) || Date.parse(value.consentExpiresAt) <= Date.now()) throw new Error('binding');
    return parseReaderStartupConsentScope(value.scope);
  } catch {
    throw new Error('private reader consent reference unavailable; no startup authority granted');
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}
