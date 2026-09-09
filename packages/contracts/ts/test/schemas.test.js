import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { SCHEMA_DIGESTS, RUN_STATUSES, OUTCOMES, CONDITIONS, APPROVAL_SCOPES } from '../dist/index.js';
import { digest } from '../dist/index.js';

const read = (rel) =>
  JSON.parse(readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8'));

const fixtures = read('../../fixtures/schema-fixtures.json');

test('the generated digests match the schemas this build sees', () => {
  // Recomputed with the TypeScript canonicalizer, so this also proves the two implementations
  // agree on the schema documents themselves, not just on the vector payloads.
  for (const [name, recorded] of Object.entries(SCHEMA_DIGESTS)) {
    const schema = read(`../../schemas/${name}`);
    assert.equal(digest(schema), recorded, `${name} digest mismatch`);
  }
});

test('the shared vocabulary is closed', () => {
  assert.deepEqual([...RUN_STATUSES], [
    'QUEUED', 'LEASED', 'RUNNING', 'FINALIZING', 'COMPLETED', 'INTERRUPTED', 'CANCELLED',
  ]);
  assert.deepEqual([...OUTCOMES], ['NOT_EVALUATED', 'PASS', 'FAIL', 'INCONCLUSIVE']);
  assert.deepEqual([...CONDITIONS], ['TRUE', 'FALSE', 'UNKNOWN']);
  assert.deepEqual([...APPROVAL_SCOPES], ['RUN_EFFECTS', 'PATCH_APPLY', 'GITHUB_PUBLISH']);
});

test('fixture payloads are shared with the Python suite', () => {
  // Not a validation run — TypeScript schema validation belongs to module 18's generated client.
  // What matters here is that both languages read the same fixture file, so the two suites cannot
  // drift onto different examples.
  assert.ok(Object.keys(fixtures.valid).length > 0);
  assert.ok(fixtures.invalid.length > 0);
  for (const c of fixtures.invalid) {
    assert.ok(typeof c.why === 'string' && c.why.length > 0, 'each rejection states its reason');
  }
});
