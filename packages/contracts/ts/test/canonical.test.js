import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { canonicalize, digest, CanonicalizationError } from '../dist/index.js';

const vectorsPath = fileURLToPath(new URL('../../vectors/canonicalization.json', import.meta.url));
const vectors = JSON.parse(readFileSync(vectorsPath, 'utf8'));

// The same file drives the Python suite. If the two implementations diverge, one of them fails
// here rather than producing a quietly different digest in production.
for (const testCase of vectors.cases) {
  test(`canonical form matches shared vector: ${testCase.name}`, () => {
    assert.equal(canonicalize(testCase.input), testCase.canonical);
  });

  test(`digest matches sha256 of canonical form: ${testCase.name}`, () => {
    const expected = createHash('sha256').update(testCase.canonical, 'utf8').digest('hex');
    assert.equal(digest(testCase.input), expected);
    assert.equal(digest(testCase.input), digest(testCase.input).toLowerCase());
    assert.equal(digest(testCase.input).length, 64);
  });
}

test('object keys are ordered by UTF-16 code unit', () => {
  // U+1F600 is the surrogate pair 0xD83D 0xDE00, so it sorts before U+FF00 by code unit even
  // though its code point is larger. Python must be told to do this; JavaScript does it natively.
  const out = canonicalize({ '\u{1F600}': 1, '＀': 2 });
  assert.ok(out.indexOf('\u{1F600}') < out.indexOf('＀'));
});

test('property order does not affect the digest, array order does', () => {
  assert.equal(digest({ a: 1, b: 2 }), digest({ b: 2, a: 1 }));
  assert.notEqual(digest({ k: [1, 2] }), digest({ k: [2, 1] }));
});

for (const value of [NaN, Infinity, -Infinity, 2 ** 53, -(2 ** 53), 0.87]) {
  test(`rejects ${String(value)}`, () => {
    assert.throws(() => canonicalize({ v: value }), CanonicalizationError);
  });
}

test('safe integers and integral floats are accepted', () => {
  assert.equal(canonicalize({ v: 9007199254740991 }), '{"v":9007199254740991}');
  assert.equal(canonicalize({ v: 5.0 }), '{"v":5}');
  assert.equal(canonicalize({ v: -0 }), '{"v":0}');
  assert.equal(digest({ v: -0 }), digest({ v: 0 }));
});

test('undefined properties are rejected rather than dropped', () => {
  // JSON.stringify silently omits them, which would make two different objects share a digest.
  assert.throws(() => canonicalize({ a: undefined }), CanonicalizationError);
});

// --- lone surrogates (independent review finding 1) --------------------------------------
// Node's UTF-8 encoder silently substitutes U+FFFD for an unpaired surrogate, so digest() used
// to return a confident, wrong hash while Python raised a raw encoding error. Both now refuse.

for (const [name, s] of [
  ['unpaired high', '\uD800'],
  ['unpaired low', '\uDFFF'],
  ['high followed by a non-surrogate', '\uD83Da'],
  ['non-surrogate followed by low', 'a\uDE00'],
]) {
  test(`rejects a lone surrogate: ${name}`, () => {
    assert.throws(() => canonicalize({ s }), CanonicalizationError);
    assert.throws(() => digest({ s }), CanonicalizationError);
  });
}

test('rejects a lone surrogate in an object key, before sorting', () => {
  assert.throws(() => canonicalize({ '\uD800': 1 }), CanonicalizationError);
});

test('valid surrogate pairs are unaffected', () => {
  // Allowed-path control: rejecting all surrogates would break every emoji.
  assert.equal(canonicalize({ s: '\u{1F600}' }), '{"s":"\u{1F600}"}');
  assert.equal(digest({ s: '\u{1F600}' }).length, 64);
});

test('a lone surrogate no longer hashes as U+FFFD', () => {
  // The specific silent-corruption case: these two digests were identical before the fix.
  assert.throws(() => digest({ s: '\uD800' }), CanonicalizationError);
  assert.equal(digest({ s: '�' }).length, 64); // the replacement character itself is fine
});
