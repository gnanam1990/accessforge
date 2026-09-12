import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { digest, REFERENCE_FIXTURE_DIGEST, REFERENCE_FIXTURE_VERSION } from '../dist/index.js';

test('the frozen reference fixture has the same canonical digest in both languages', () => {
  const definition = JSON.parse(readFileSync(
    new URL('../../fixtures/reference-service-request-v1.json', import.meta.url), 'utf8',
  ));
  assert.equal(digest(definition), REFERENCE_FIXTURE_DIGEST);
  assert.equal(REFERENCE_FIXTURE_DIGEST, '39acd4e6ff833c3f5668cbc951f541658858568bd9ba31814bfb19a318dbb6a3');
  assert.equal(definition.identityVersion, REFERENCE_FIXTURE_VERSION);
});
