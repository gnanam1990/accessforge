/**
 * Every URL the web client builds must be one the API actually serves.
 *
 * The web client is hand-written — deliberately, because its discriminated result union and its
 * session-epoch handling are decisions a generator cannot make. The cost of that choice is drift:
 * a route renamed on the server leaves this client building a URL that returns 404, and nothing in
 * TypeScript can see it. A type checker checks shapes, and a path is a string.
 *
 * So the generated operation table is the referee. `PATHS` comes from `contracts/openapi.json`,
 * which comes from the live application, so a path here that is not there is a path the server does
 * not serve — today, in this build.
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

import { PATHS } from '../../../../packages/clients/ts/src/operations'

// Resolved from the project root rather than from `import.meta.url`: the jsdom environment this
// suite runs in gives modules an `http://localhost/` URL, so `fileURLToPath` refuses it.
const source = readFileSync(path.resolve(process.cwd(), 'src/api/resources.ts'), 'utf8')

/**
 * Turn the template literals the client builds into the templates the contract declares.
 *
 * `${base(workspaceId)}/runs/${encodeURIComponent(runId)}` becomes
 * `/v1/workspaces/{workspace_id}/runs/{run_id}`. Crude, and crude is right: anything cleverer would
 * be a second implementation of the client's URL building, and a bug shared by both would be
 * invisible to this test.
 */
function templatesUsedByTheClient(): string[] {
  const matches = source.match(/\$\{base\([A-Za-z]+\)\}[^`]*/g) ?? []
  return [
    ...new Set(
      matches.map((raw) =>
        raw
          .replace('${base(workspaceId)}', '/v1/workspaces/{workspace_id}')
          .replace(/\$\{encodeURIComponent\(([A-Za-z]+)\)\}/g, (_, name: string) =>
            `{${name.replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase()}}`,
          ),
      ),
    ),
  ]
}

describe('the web client against the published contract', () => {
  it('extracts something to check, so a broken matcher cannot pass vacuously', () => {
    // Without this the regex could stop matching and every assertion below would hold over an
    // empty list — a green test proving that nothing was examined.
    expect(templatesUsedByTheClient().length).toBeGreaterThan(20)
    expect(PATHS.length).toBeGreaterThan(20)
  })

  it('builds no URL the API does not serve', () => {
    const served = new Set(PATHS)
    const unknown = templatesUsedByTheClient().filter((path) => !served.has(path))
    expect(unknown, `these paths are not in the contract: ${unknown.join(', ')}`).toEqual([])
  })

  it('names the routes that exist and nobody uses yet, rather than hiding them', () => {
    // Not a failure. The API deliberately serves more than this UI consumes — the event stream,
    // grants, schedules — and listing them here is how somebody notices a screen is missing rather
    // than assuming the API is complete because the UI compiles.
    const used = new Set(templatesUsedByTheClient())
    const unused = PATHS.filter(
      (path) => path.startsWith('/v1/workspaces/') && !used.has(path),
    )
    expect(unused.length).toBeGreaterThan(0)
    expect(unused).toContain('/v1/workspaces/{workspace_id}/events/stream')
  })
})
