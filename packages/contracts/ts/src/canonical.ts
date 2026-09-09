/**
 * RFC8785 (JSON Canonicalization Scheme) serialization and SHA-256 digests.
 *
 * This must agree byte-for-byte with the Python implementation in
 * packages/domain/src/accessforge_domain/canonical.py. The shared vectors in
 * ../../vectors/canonicalization.json are the arbiter; neither language owns them.
 *
 * The same two restrictions apply here, for the same reasons (ADR 0003): non-integral floats and
 * integers outside the JSON safe range are rejected rather than guessed at.
 */

import { createHash } from 'node:crypto';

export const MAX_SAFE = Number.MAX_SAFE_INTEGER; // 2^53 - 1
export const MIN_SAFE = Number.MIN_SAFE_INTEGER;

export class CanonicalizationError extends Error {
  override name = 'CanonicalizationError';
}

const SHORT_ESCAPES = new Map<number, string>([
  [0x08, '\\b'],
  [0x09, '\\t'],
  [0x0a, '\\n'],
  [0x0c, '\\f'],
  [0x0d, '\\r'],
  [0x22, '\\"'],
  [0x5c, '\\\\'],
]);

function serializeString(value: string): string {
  let out = '"';
  // Iterating by code unit (not by code point) keeps lone surrogates from being reordered or
  // silently replaced, and matches the ordering rule used for object keys.
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    const short = SHORT_ESCAPES.get(code);
    if (short !== undefined) {
      out += short;
    } else if (code < 0x20) {
      out += `\\u${code.toString(16).padStart(4, '0')}`;
    } else {
      out += value[i];
    }
  }
  return `${out}"`;
}

function serializeNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new CanonicalizationError(`${value} is not representable in JSON`);
  }
  if (!Number.isInteger(value)) {
    throw new CanonicalizationError(
      `refusing to canonicalize the non-integral number ${value}: cross-language formatting ` +
        'agreement is not guaranteed, and floating-point values may not decide an outcome ' +
        '(CONTRACTS section 4)',
    );
  }
  if (value < MIN_SAFE || value > MAX_SAFE) {
    throw new CanonicalizationError(
      `integer ${value} is outside the JSON safe-integer range; overflow must be rejected`,
    );
  }
  // String(-0) is "0", matching the Python implementation's normalization of -0.0.
  return String(value === 0 ? 0 : value);
}

function serialize(value: unknown): string {
  if (value === null) return 'null';
  if (value === true) return 'true';
  if (value === false) return 'false';
  if (typeof value === 'string') return serializeString(value);
  if (typeof value === 'number') return serializeNumber(value);
  if (typeof value === 'bigint') {
    throw new CanonicalizationError('bigint cannot be canonicalized within the safe-integer range');
  }
  if (Array.isArray(value)) {
    return `[${value.map(serialize).join(',')}]`;
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    // Array.prototype.sort on strings compares UTF-16 code units, which is exactly what RFC8785
    // requires. The Python side has to ask for this explicitly.
    const keys = Object.keys(record).sort();
    const parts = keys.map((key) => {
      const entry = record[key];
      if (entry === undefined) {
        throw new CanonicalizationError(`property ${key} is undefined; JSON has no such value`);
      }
      return `${serializeString(key)}:${serialize(entry)}`;
    });
    return `{${parts.join(',')}}`;
  }
  throw new CanonicalizationError(`type ${typeof value} cannot be canonicalized`);
}

export function canonicalize(value: unknown): string {
  return serialize(value);
}

export function digest(value: unknown): string {
  return createHash('sha256').update(canonicalize(value), 'utf8').digest('hex');
}
