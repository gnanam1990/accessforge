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

/**
 * Refuse text containing an unpaired surrogate code unit.
 *
 * A lone surrogate has no UTF-8 encoding. Without this check Node's encoder silently substitutes
 * U+FFFD and `digest()` returns a confident, wrong hash, while the Python implementation raised a
 * raw UnicodeEncodeError. Neither is agreement, and the silent-success side is the more dangerous.
 */
function rejectLoneSurrogates(value: string): void {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = i + 1 < value.length ? value.charCodeAt(i + 1) : -1;
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        throw new CanonicalizationError(
          `unpaired high surrogate U+${code.toString(16).toUpperCase().padStart(4, '0')} at ` +
            `index ${i}: no UTF-8 encoding exists, so no digest over it can be meaningful`,
        );
      }
      i += 1;
      continue;
    }
    if (code >= 0xdc00 && code <= 0xdfff) {
      throw new CanonicalizationError(
        `unpaired low surrogate U+${code.toString(16).toUpperCase().padStart(4, '0')} at ` +
          `index ${i}: no UTF-8 encoding exists`,
      );
    }
  }
}

function serializeString(value: string): string {
  rejectLoneSurrogates(value);
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
  if (typeof value === 'function' || typeof value === 'symbol' || value === undefined) {
    throw new CanonicalizationError(`${typeof value} has no JSON representation`);
  }
  if (typeof value === 'bigint') {
    throw new CanonicalizationError('bigint cannot be canonicalized within the safe-integer range');
  }
  if (Array.isArray(value)) {
    // Array.prototype.map skips holes, so a sparse array like [, 1] would serialize to "[,1]",
    // which is not valid JSON and has no Python counterpart.
    const parts: string[] = [];
    for (let i = 0; i < value.length; i += 1) {
      if (!(i in value)) {
        throw new CanonicalizationError(
          `array index ${i} is a hole; sparse arrays have no JSON representation`,
        );
      }
      parts.push(serialize(value[i]));
    }
    return `[${parts.join(',')}]`;
  }
  if (typeof value === 'object') {
    // Object.keys returns [] for a Date, Map, Set, RegExp or any class instance whose state is
    // not an own enumerable property, so these would serialize as "{}" and digest to a
    // confident, wrong hash. The Python implementation refuses them, so this must too — the same
    // silent-success failure mode the surrogate check exists to prevent.
    const prototype = Object.getPrototypeOf(value) as object | null;
    if (prototype !== Object.prototype && prototype !== null) {
      throw new CanonicalizationError(
        `${(value as object).constructor?.name ?? 'object'} has no JSON representation; ` +
          'only plain objects, arrays and JSON primitives can be canonicalized',
      );
    }
    const record = value as Record<string, unknown>;
    // Array.prototype.sort on strings compares UTF-16 code units, which is exactly what RFC8785
    // requires. The Python side has to ask for this explicitly.
    const keys = Object.keys(record);
    // Validate before sorting so both implementations refuse at the same point.
    for (const key of keys) rejectLoneSurrogates(key);
    keys.sort();
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
