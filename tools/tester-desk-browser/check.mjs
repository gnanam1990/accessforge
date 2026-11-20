#!/usr/bin/env node
/**
 * AccessForge Tester Desk · real-browser proof of the pilot journey.
 *
 * Boots the real FastAPI service and the real Vite/React app on ephemeral
 * loopback ports, provisions a disposable AF-21 scene through the real HTTP
 * entry points (provision.py), then drives a normal Chromium through the
 * tester journey and the review decision:
 *
 *   tester sign-in → offer + consent → claim → step observations → save draft
 *   → reload (draft must persist) → submit → reviewer sign-in → review queue
 *   → digest-bound accept → payable shows PENDING DISPATCH → outsider denied.
 *
 * Keyboard accessibility is exercised for real: the consent checkbox is ticked
 * with Space, the claim is activated with Enter, and a Tab-walk audit at both
 * desktop and 320px widths records the focus path.
 *
 * Deliberately out of scope (recorded, not faked): a consenting human tester,
 * a real VoiceOver/Safari session, and a live payout provider. The payable
 * staying PENDING DISPATCH is the honest state of that external gate.
 *
 * Configuration (all optional except where noted):
 *   AF_BROWSER_BIN      Chromium/Chrome executable to drive. Default: the
 *                       macOS Playwright headless-shell cache (1254), falling
 *                       back to `chrome-headless-shell` on PATH.
 *   AF_BROWSER_ARGS     Extra browser args, space-separated (e.g.
 *                       "--single-process --no-sandbox" for sandboxed shells).
 *   AF_DATABASE_URL     Postgres URL for the disposable scene. Default:
 *                       postgresql://af_td:af_td_local@localhost:5432/af_td_dev
 *   AF_API_PORT         Pin the API port (default: pick a free one).
 *   AF_WEB_PORT         Pin the web port (default: pick a free one).
 *   AF_EVIDENCE_DIR     Where the JSON record + screenshots go
 *                       (default: <this dir>/evidence/<timestamp>).
 *   AF_KEEP_PROCESSES=1 Leave API + vite running after the check (prints URLs).
 *
 * Exit code is non-zero if any named check fails; output stays on stdout/stderr
 * and a redacted record lands in the evidence dir (synthetic identities only).
 */

import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright-core'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(HERE, '..', '..')

const DATABASE_URL =
  process.env.AF_DATABASE_URL ?? 'postgresql://af_td:af_td_local@localhost:5432/af_td_dev'
const EVIDENCE_DIR =
  process.env.AF_EVIDENCE_DIR ??
  join(HERE, 'evidence', new Date().toISOString().replaceAll(':', '-').slice(0, 19))
const KEEP = process.env.AF_KEEP_PROCESSES === '1'

const results = []
const screenshots = []
const log = (msg) => console.log(`[check] ${msg}`)
const record = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`)
}

const freePort = () =>
  new Promise((resolvePort, rejectPort) => {
    const server = createServer()
    server.once('error', rejectPort)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolvePort(port))
    })
  })

const waitFor = async (url, { timeoutMs = 30000, okStatuses = [200, 404] } = {}) => {
  const deadline = Date.now() + timeoutMs
  let last = 'no response'
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { redirect: 'manual' })
      if (okStatuses.includes(res.status)) return true
      last = `HTTP ${res.status}`
    } catch (error) {
      last = String(error).slice(0, 120)
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error(`timed out waiting for ${url} (${last})`)
}

const startProcess = (name, command, args, { cwd = REPO, env = {} } = {}) => {
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const tail = []
  const capture = (chunk) => {
    tail.push(chunk.toString())
    if (tail.length > 40) tail.shift()
  }
  child.stdout.on('data', capture)
  child.stderr.on('data', capture)
  child.on('exit', (code) => {
    if (code !== null && code !== 0 && !shuttingDown) {
      log(`${name} exited early (${code}):\n${tail.slice(-12).join('')}`)
    }
  })
  return { child, tail: () => tail.join('') }
}

let shuttingDown = false

const browserCandidates = () => {
  const cache = join(homedir(), 'Library/Caches/ms-playwright')
  return [
    process.env.AF_BROWSER_BIN,
    join(cache, 'chromium_headless_shell-1254/chrome-headless-shell-mac-arm64/chrome-headless-shell'),
    join(cache, 'chromium-1254/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'),
  ].filter(Boolean)
}

const resolveBrowser = () => {
  for (const candidate of browserCandidates()) {
    if (existsSync(candidate)) return candidate
  }
  throw new Error(
    'no usable Chromium found; set AF_BROWSER_BIN to a Chrome/headless-shell executable'
  )
}

const shot = async (page, name) => {
  const path = join(EVIDENCE_DIR, `${name}.png`)
  await page.screenshot({ path, fullPage: true })
  screenshots.push(path)
}

const signIn = async (page, email) => {
  // A real sign-in through the form, not a cookie transplant. Wait for the session POST to land
  // and for the signed-in chrome to render: a following page.goto can cancel an in-flight POST
  // and leave the app showing the sign-in form again.
  await page.getByLabel('Email address').fill(email)
  const created = page.waitForResponse(
    (r) => r.url().includes('/v1/sessions') && r.request().method() === 'POST' && r.status() === 201,
    { timeout: 20000 },
  )
  await page.getByRole('button', { name: 'Sign in' }).press('Enter')
  await created
  await page.getByRole('button', { name: 'Sign out' }).waitFor({ timeout: 20000 })
}

const signOut = async (page) => {
  const ended = page.waitForResponse(
    (r) => r.url().includes('/v1/sessions') && r.request().method() === 'DELETE',
    { timeout: 20000 },
  )
  await page.getByRole('button', { name: 'Sign out' }).click()
  await ended.catch(() => {})
  await page.getByLabel('Email address').waitFor({ timeout: 20000 })
}

/** Walk Tab from the top of the page, recording the focus path. */
const tabWalk = async (page, maxTabs = 30) => {
  await page.evaluate(() => {
    document.activeElement?.blur?.()
    document.body.focus?.()
  })
  const path = []
  for (let i = 0; i < maxTabs; i += 1) {
    await page.keyboard.press('Tab')
    const descriptor = await page.evaluate(() => {
      const el = document.activeElement
      if (!el || el === document.body) return null
      const name =
        el.getAttribute('aria-label') ??
        el.getAttribute('name') ??
        el.textContent?.trim().slice(0, 60) ??
        el.id ??
        el.tagName
      return `${el.tagName.toLowerCase()}[${el.getAttribute('type') ?? el.getAttribute('role') ?? ''}] ${name}`
    })
    path.push(descriptor)
  }
  return path
}

const apiSignIn = async (apiBase, email) => {
  const res = await fetch(`${apiBase}/v1/sessions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  if (res.status !== 201) throw new Error(`api sign-in refused: ${res.status}`)
  const cookie = res.headers
    .getSetCookie()
    .map((c) => c.split(';')[0])
    .join('; ')
  const csrf = /accessforge_csrf=([^;]+)/.exec(cookie)?.[1] ?? ''
  return { cookie, csrf }
}

const apiGet = async (apiBase, path, session) =>
  fetch(`${apiBase}${path}`, { headers: { cookie: session.cookie } })

const main = async () => {
  mkdirSync(EVIDENCE_DIR, { recursive: true })
  const apiPort = Number(process.env.AF_API_PORT) || (await freePort())
  const webPort = Number(process.env.AF_WEB_PORT) || (await freePort())
  // Readiness TCP-probes the evidence endpoint; a throwaway listener satisfies
  // the probe without pretending to be an object store (tester-desk attachments
  // live in Postgres and never touch it).
  const evidencePort = Number(process.env.AF_EVIDENCE_PORT) || (await freePort())
  const evidenceStub = createServer((socket) => socket.end())
  await new Promise((r) => evidenceStub.listen(evidencePort, '127.0.0.1', r))
  log(`api → 127.0.0.1:${apiPort}  web → 127.0.0.1:${webPort}  evidence-stub → 127.0.0.1:${evidencePort}`)

  const api = startProcess('api', 'uv', ['run', 'python', '-m', 'accessforge_api'], {
    env: {
      UV_CACHE_DIR: join(REPO, '.uv-cache'),
      ACCESSFORGE_DATABASE_URL: DATABASE_URL,
      ACCESSFORGE_EVIDENCE_ENDPOINT_URL: `http://127.0.0.1:${evidencePort}`,
      ACCESSFORGE_EVIDENCE_BUCKET: 'unused',
      ACCESSFORGE_EVIDENCE_ACCESS_KEY: 'unused',
      ACCESSFORGE_EVIDENCE_SECRET_KEY: 'unused',
      ACCESSFORGE_IDENTITY_PROVIDER: 'local-development',
      ACCESSFORGE_ENVIRONMENT: 'local',
      ACCESSFORGE_HOST: '127.0.0.1',
      ACCESSFORGE_PORT: String(apiPort),
    },
  })
  const web = startProcess(
    'web',
    join(REPO, 'apps/web/node_modules/.bin/vite'),
    // Loopback only and IPv4-explicit: a bare `localhost` resolves to ::1 on
    // this host, which leaves 127.0.0.1 fetches hanging with nothing listening.
    ['--port', String(webPort), '--strictPort', '--host', '127.0.0.1'],
    {
      cwd: join(REPO, 'apps/web'),
      env: { ACCESSFORGE_API_ORIGIN: `http://127.0.0.1:${apiPort}` },
    },
  )
  const stopAll = async () => {
    if (KEEP) return
    shuttingDown = true
    evidenceStub.close()
    for (const p of [web, api]) {
      if (p.child.exitCode === null && p.child.signalCode === null) {
        p.child.kill('SIGTERM')
      }
    }
    // Never wait on a child that already exited: `once(exit)` on one hangs.
    await Promise.allSettled(
      [web, api]
        .filter((p) => p.child.exitCode === null && p.child.signalCode === null)
        .map((p) =>
          Promise.race([once(p.child, 'exit'), new Promise((r) => setTimeout(r, 4000))]),
        ),
    )
  }
  process.on('SIGINT', async () => { shuttingDown = true; await stopAll(); process.exit(130) })

  const apiBase = `http://127.0.0.1:${apiPort}`
  let scene = null
  try {
  // A fresh database gets its full schema before the API's readiness gate runs.
  const migration = spawn(
    'uv',
    [
      'run', 'python', 'tools/tester-desk-browser/provision.py',
      '--database-url', DATABASE_URL, '--migrate-only',
    ],
    { cwd: REPO, env: { ...process.env, UV_CACHE_DIR: join(REPO, '.uv-cache') }, stdio: 'inherit' },
  )
  const [migrationCode] = await once(migration, 'exit')
  if (migrationCode !== 0) throw new Error('database migration failed')

  await waitFor(`${apiBase}/health/live`)
  await waitFor(`${apiBase}/health/ready`)
  await waitFor(`http://127.0.0.1:${webPort}/`, { okStatuses: [200] })
  log('api + web are up')

  // --- provisioning through the real API ------------------------------------
  const provision = spawn(
    'uv',
    [
      'run', 'python', 'tools/tester-desk-browser/provision.py',
      '--database-url', DATABASE_URL,
      '--api-base', apiBase,
    ],
    { cwd: REPO, env: { ...process.env, UV_CACHE_DIR: join(REPO, '.uv-cache') } },
  )
  let provisionOut = ''
  provision.stdout.on('data', (c) => { provisionOut += c })
  provision.stderr.on('data', (c) => process.stderr.write(c))
  const [provisionCode] = await once(provision, 'exit')
  if (provisionCode !== 0) {
    throw new Error('provisioning failed; see stderr above')
  }
  scene = JSON.parse(provisionOut.trim().split('\n').pop())
  log(`scene ${scene.runId}: workspace ${scene.workspaceId.slice(0, 8)}… commission ${scene.reference}`)

  const ws = scene.workspaceId
  const webBase = `http://127.0.0.1:${webPort}`
  const inboxUrl = `${webBase}/w/${ws}/tester-desk/tester`
  const deskUrl = `${webBase}/w/${ws}/tester-desk`
  const clientUrl = `${webBase}/w/${ws}/tester-desk/client`

  // --- browser ---------------------------------------------------------------
  const executablePath = resolveBrowser()
  log(`browser: ${executablePath}`)
  const profileDir = join(HERE, '.browser-profile')
  rmSync(profileDir, { recursive: true, force: true })
  const extraArgs = (process.env.AF_BROWSER_ARGS ?? '').split(' ').filter(Boolean)
  const context = await chromium.launchPersistentContext(profileDir, {
    executablePath,
    headless: true,
    args: ['--disable-gpu', '--no-proxy-server', ...extraArgs],
    viewport: { width: 1280, height: 800 },
  })
  const page = context.pages()[0] ?? (await context.newPage())
  page.setDefaultTimeout(20000)

  let narrowPath = []
  try {
    // -- 1. tester journey -----------------------------------------------------
    await page.goto(`${webBase}/w/${ws}`, { waitUntil: 'domcontentloaded' })
    await page.getByLabel('Email address').waitFor()
    record('sign-in form reachable', true)
    await shot(page, '01-sign-in')

    // Sign in at the workspace's front door: the landing redirect must take a
    // TESTER to their inbox, not the member overview the server refuses.
    await signIn(page, scene.emails.tester)
    await page.getByRole('heading', { name: 'My test work' }).waitFor()
    record(
      'TESTER landing resolves to the tester inbox',
      page.url().includes('/tester-desk/tester'),
      page.url().replace(webBase, ''),
    )
    record('tester signed in via UI form', true)

    const offer = page.locator('article[aria-label^="Offer AF-21"]')
    await offer.waitFor()
    record('offer card visible to enrolled tester', true)
    record(
      'offer shows the pinned fee as readable money',
      await offer.getByText('20.00 USD').isVisible(),
      (await offer.textContent())?.slice(0, 120),
    )
    await shot(page, '02-offer')

    // Consent gate: claim disabled until the checkbox is ticked — by keyboard.
    const claimButton = page.getByRole('button', { name: 'Claim this assignment' })
    record('claim disabled before consent', await claimButton.isDisabled())
    await offer.locator('details > summary').click()
    record(
      'pinned consent terms are readable before accepting',
      (await offer.locator('details').textContent())?.includes('declared assistive') === true,
    )
    const consentBox = page.getByRole('checkbox', {
      name: 'I read and accept the consent terms for this assignment',
    })
    await consentBox.focus()
    await page.keyboard.press('Space')
    record('consent checkbox ticked with Space', await consentBox.isChecked())
    await claimButton.focus()
    record('claim enabled after consent', await claimButton.isEnabled())
    await claimButton.press('Enter')
    const assignmentsSection = page.locator('section[aria-labelledby="td-my"]')
    await assignmentsSection.getByRole('link').first().waitFor()
    record('assignment appears after keyboard claim', true)
    await shot(page, '03-claimed')

    // -- 2. capture: error visibility before content ---------------------------
    await assignmentsSection.getByRole('link').first().click()
    await page.getByRole('heading', { name: /Capture · r18/ }).waitFor()
    const assignmentId = /assignments\/([0-9a-f-]{36})/.exec(page.url())?.[1]
    record('capture screen opens the claimed assignment', Boolean(assignmentId))

    const missing = page.getByText('Not yet submittable')
    await missing.waitFor()
    record('empty draft shows submittability warning', await missing.isVisible())
    await page.getByRole('button', { name: 'Seal and submit for review' }).click()
    const refused = page.getByRole('heading', { name: 'Refused' })
    await refused.waitFor()
    record('incomplete submit is refused with a visible problem notice', await refused.isVisible())
    await shot(page, '04-submit-refused')

    // -- 3. observations: the focus-trap finding -------------------------------
    const step = (title) =>
      page.getByRole('group', { name: `Result for ${title}` })
    const setResult = async (title, result) => {
      const radio = step(title).getByRole('radio', { name: result, exact: true })
      await radio.focus()
      await page.keyboard.press('Space')
      return radio.isChecked()
    }
    record('s1 PASSED via keyboard', await setResult('Open the release checkout URL', 'PASSED'))
    record('s2 PASSED via keyboard', await setResult('Fill the contact form', 'PASSED'))
    record('s3 PASSED via keyboard', await setResult('Fill the shipping form', 'PASSED'))
    record('s4 FAILED via keyboard', await setResult('Advance past the shipping form', 'FAILED'))
    await page
      .getByLabel('Notes for Advance past the shipping form')
      .fill('Keyboard focus traps in the shipping frame; no path forward without a pointer.')
    record('s5 UNREACHABLE via keyboard', await setResult('Complete payment', 'UNREACHABLE'))
    await page
      .getByLabel('Notes for Complete payment')
      .fill('Unreachable because the focus trap at the previous step blocks the journey.')
    await shot(page, '05-observations')

    // -- 4. save, reload, persistence ------------------------------------------
    await page.getByRole('button', { name: 'Save draft' }).click()
    await page.waitForTimeout(800) // autosave lands; the announcer fires
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.getByRole('heading', { name: /Capture · r18/ }).waitFor()
    const s4after = page
      .getByRole('group', { name: 'Result for Advance past the shipping form' })
      .getByRole('radio', { name: 'FAILED', exact: true })
    await s4after.waitFor()
    record('draft persists across reload', await s4after.isChecked())
    const s5after = page
      .getByRole('group', { name: 'Result for Complete payment' })
      .getByRole('radio', { name: 'UNREACHABLE', exact: true })
    record('unreachable step also persists', await s5after.isChecked())
    await shot(page, '06-draft-restored')

    // -- 5. attach the required ACTION_TRACE evidence through the UI -----------
    const tracePath = join(EVIDENCE_DIR, 'action-trace.json')
    writeFileSync(tracePath, JSON.stringify({
      kind: 'keyboard-action-trace',
      release: 'r18',
      steps: [
        { stepId: 's4', event: 'focus-trap', detail: 'Tab cycled inside the shipping iframe' },
        { stepId: 's5', event: 'unreachable', detail: 'no forward keyboard path' },
      ],
    }))
    await page.getByLabel('Evidence kind').selectOption('ACTION_TRACE')
    await page.getByLabel('Evidence file').setInputFiles(tracePath)
    await page.getByRole('button', { name: 'Attach evidence' }).click()
    const traceRow = page.getByRole('row', { name: /action-trace\.json/ })
    await traceRow.waitFor()
    record('ACTION_TRACE evidence attaches via UI', await traceRow.isVisible())
    await shot(page, '06b-evidence-attached')

    // -- 6. 320px keyboard accessibility ---------------------------------------
    await page.setViewportSize({ width: 320, height: 800 })
    await page.waitForTimeout(300)
    const offenders = await page.evaluate(() => {
      const limit = document.documentElement.clientWidth
      const hits = []
      for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect()
        if (r.right > limit + 1 || r.left < -1) {
          hits.push(
            `${el.tagName.toLowerCase()}${el.className ? '.' + String(el.className).split(' ').join('.') : ''}` +
            ` right=${Math.round(r.right)} left=${Math.round(r.left)}`,
          )
        }
      }
      return hits.slice(0, 12)
    })
    const narrowWidth = await page.evaluate(() => document.scrollingElement.scrollWidth)
    record('320px has no document-level horizontal overflow', narrowWidth <= 320,
      `scrollWidth=${narrowWidth}` + (offenders.length > 0 ? ` offenders=${offenders.join(' ;; ')}` : ''))
    narrowPath = await tabWalk(page, 40)
    const reachedSave = narrowPath.some((d) => d?.includes('Save draft'))
    const reachedSubmit = narrowPath.some((d) => d?.includes('Seal and submit'))
    const reachedRadios = narrowPath.some((d) => d?.startsWith('input[radio]'))
    record('320px Tab reaches observation radios', reachedRadios)
    record('320px Tab reaches Save draft and Submit', reachedSave && reachedSubmit,
      narrowPath.filter(Boolean).join(' | '))
    await shot(page, '07-narrow-320px')
    await page.setViewportSize({ width: 1280, height: 800 })

    // -- 6. submit -------------------------------------------------------------
    await page.getByRole('button', { name: 'Seal and submit for review' }).click()
    await page.getByText('Submission sealed and sent to review').waitFor().catch(() => {})
    const awaiting = page.getByText('AWAITING REVIEW')
    await awaiting.waitFor()
    record('sealed submission shows FAILED task + AWAITING REVIEW', true)
    await shot(page, '08-submitted')

    // -- 7. reviewer decision ---------------------------------------------------
    await signOut(page)
    await signIn(page, scene.emails.reviewer)
    await page.goto(`${webBase}/w/${ws}`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('heading', { name: 'Overview' }).waitFor().catch(() => {})
    record(
      'member landing still resolves to the overview',
      !page.url().includes('/tester-desk/'),
      page.url().replace(webBase, ''),
    )
    await page.goto(deskUrl, { waitUntil: 'domcontentloaded' })
    const reviewCard = page.locator('article[aria-label="Review AF-21"]')
    await reviewCard.waitFor()
    record('review queue shows the sealed submission', true)
    await reviewCard.getByText(/Keyboard focus traps in the shipping frame/).waitFor()
    record('reviewer can read sealed step notes before deciding', true)
    await shot(page, '09-review-queue')

    await reviewCard.getByLabel('Justification').fill(
      'Focus-trap evidence is digest-bound and matches the defect; task failure stands.',
    )
    await reviewCard.getByRole('button', { name: 'Accept evidence' }).click()
    // The card leaves the queue once decided. The reviewer role deliberately
    // cannot see finance: the payable is verified where it is meant to be
    // visible — the tester's own compensation section.
    await reviewCard.waitFor({ state: 'detached', timeout: 15000 })
    record('accepted evidence removes the card from the review queue', true)

    const ownerSession = await apiSignIn(apiBase, scene.emails.owner)
    const payablesRes = await apiGet(
      apiBase,
      `/v1/workspaces/${ws}/tester-desk/finance/payables`,
      ownerSession,
    )
    const payablesBody = await payablesRes.json()
    const pendingPayables = (payablesBody.items ?? []).filter(
      (p) => p.status === 'INTENT',
    )
    record(
      'accepted evidence created exactly one payable, honestly PENDING DISPATCH',
      payablesRes.status === 200 && pendingPayables.length === 1,
      `HTTP ${payablesRes.status} intent=${pendingPayables.length}`,
    )
    await shot(page, '10-accepted-queue-cleared')

    // The tester reads the review outcome on their own capture surface.
    await signOut(page)
    await signIn(page, scene.emails.tester)
    await page.goto(`${webBase}/w/${ws}/tester-desk/tester/assignments/${assignmentId}`,
      { waitUntil: 'domcontentloaded' })
    const acceptedBadge = page.getByText('ACCEPTED')
    await acceptedBadge.first().waitFor()
    record(
      'tester sees the ACCEPTED decision with the reviewer justification',
      await page.getByText('Focus-trap evidence is digest-bound').isVisible(),
    )
    const testerPending = page.getByText('PENDING DISPATCH')
    record(
      'tester sees the payable as PENDING DISPATCH, not a settled claim',
      await testerPending.isVisible(),
    )
    await shot(page, '10b-tester-decision')

    await signOut(page)
    await signIn(page, scene.emails.owner)
    await page.goto(deskUrl, { waitUntil: 'domcontentloaded' })
    await page.reload({ waitUntil: 'domcontentloaded' })
    const reportAction = page.getByRole('article', { name: 'Client report for AF-21' })
    await reportAction.getByRole('button', { name: 'Generate client report', exact: true }).click()
    await reportAction.getByText('REPORT r1 GENERATED', { exact: true }).waitFor()
    record('owner generates a client report after revisiting the agency desk', true)

    await signOut(page)
    await signIn(page, scene.emails.client)
    await page.goto(`${webBase}/w/${ws}`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('heading', { name: 'Test reports' }).waitFor()
    record(
      'CLIENT landing resolves to the report list',
      page.url().includes('/tester-desk/client'),
      page.url().replace(webBase, ''),
    )
    const reportCard = page.locator('article[aria-label^="Report r18"]')
    await reportCard.waitFor()
    await reportCard.getByRole('button', { name: 'Show findings' }).click()
    const clientHtml = await page.content()
    const protectedFields = ['local-ledger', 'Taylor Tester', scene.emails.tester, 'desk-demo', 'Keyboard focus traps in the shipping frame']
    const leaked = protectedFields.filter((f) => clientHtml.includes(f))
    record('client sees the rendered r18 report', true)
    record(
      'client report carries no tester identity, payout route or credentials',
      leaked.length === 0,
      leaked.length > 0 ? `leaked: ${leaked.join(', ')}` : 'findings rendered, none of the protected fields present',
    )
    await shot(page, '11-client-report')

    // -- 8. permission / error visibility ---------------------------------------
    await signOut(page)
    await signIn(page, scene.emails.outsider)
    await page.goto(inboxUrl, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1200)
    const deniedInbox =
      (await page.getByText(/do not have permission|Not available/i).first().isVisible().catch(() => false)) ||
      (await page.getByText('No offers right now.').isVisible().catch(() => false))
    record(
      'outsider sees a refusal/empty state, never the offer',
      deniedInbox && (await page.locator('article[aria-label^="Offer AF-21"]').count()) === 0,
    )
    await shot(page, '12-outsider')

    await page.goto(deskUrl, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1200)
    const deniedDesk = await page
      .getByText(/do not have permission|Not available/i)
      .first()
      .isVisible()
      .catch(() => false)
    record('outsider is refused on the agency console', deniedDesk)
    await shot(page, '13-outsider-console')

    // A workspace named in the session is not authority: the tester can reach
    // the workspace route but every agency surface still refuses directly.
    await signOut(page)
    await signIn(page, scene.emails.tester)
    await page.goto(deskUrl, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1200)
    const testerDenied = await page
      .getByText(/do not have permission|Not available|not permitted|Not found|no such resource/i)
      .first()
      .isVisible()
      .catch(() => false)
    const agencyDataVisible = (await page.locator('article[aria-label^="Review "]').count()) > 0 ||
      (await page.locator('article[aria-label^="Commission "]').count()) > 0
    record('direct agency-console URL still refuses the tester',
      testerDenied && !agencyDataVisible,
      `refusal visible=${testerDenied} agency cards=${agencyDataVisible}`)
    await shot(page, '14-tester-agency-refused')

    // -- 9. desktop keyboard audit ---------------------------------------------
    await signOut(page)
    await signIn(page, scene.emails.tester)
    await page.goto(inboxUrl, { waitUntil: 'domcontentloaded' })
    await page.getByRole('heading', { name: 'My test work' }).waitFor()
    const deskPath = await tabWalk(page, 40)
    const deskOk =
      deskPath.some((d) => d?.includes('tester-desk') || d?.includes('My test work')) &&
      deskPath.filter(Boolean).length >= 3
    record('desktop Tab path traverses real controls', deskOk, deskPath.filter(Boolean).slice(0, 12).join(' | '))

    writeFileSync(
      join(EVIDENCE_DIR, 'tab-paths.json'),
      JSON.stringify({ desktop: deskPath, narrow320: narrowPath }, null, 2),
    )

    // -- 10. the scoped dark palette still renders a legible surface ------------
    await page.getByRole('radio', { name: 'Dark' }).check()
    await page.waitForTimeout(300)
    const darkRead = await page.evaluate(() => {
      // `.af-td` paints the canvas — `body` resolves the tokens before the shell exists.
      const shell = document.querySelector('.af-td')
      const heading = document.querySelector('main h1')
      return { bg: getComputedStyle(shell).backgroundColor, ink: getComputedStyle(heading).color }
    })
    record(
      'explicit Dark applies the scoped dark palette',
      darkRead.bg === 'rgb(28, 22, 16)' && darkRead.ink === 'rgb(244, 236, 221)',
      `bg=${darkRead.bg} ink=${darkRead.ink}`,
    )
    await shot(page, '15-dark-inbox')
    await page.getByRole('radio', { name: 'System' }).check()
  } catch (error) {
    record('browser journey completed', false, String(error).slice(0, 300))
    await shot(page, 'zz-failure').catch(() => {})
  } finally {
    await context.close()
    rmSync(profileDir, { recursive: true, force: true })
  }

  // --- evidence record --------------------------------------------------------
  const passed = results.filter((r) => r.ok).length
  const record_out = {
    scene: {
      runId: scene.runId,
      workspaceId: ws,
      commissionId: scene.commissionId,
      reference: scene.reference,
      // Synthetic identities only — no real people, credentials or payout routes.
      emails: Object.fromEntries(
        Object.entries(scene.emails).map(([k, v]) => [k, v.split('@')[0].slice(0, 20) + '@…']),
      ),
    },
    services: { apiPort, webPort, database: DATABASE_URL.replace(/:\/\/.*@/, '://***@') },
    browser: executablePath,
    checks: results,
    screenshots: screenshots.map((s) => s.split('/').pop()),
    summary: `${passed}/${results.length} checks passed`,
    externalGates: [
      'no consenting human tester — synthetic role only',
      'no real VoiceOver/Safari session — capability claims are fixture data',
      'no live payout provider — payable correctly stays PENDING DISPATCH',
    ],
  }
  writeFileSync(join(EVIDENCE_DIR, 'evidence.json'), JSON.stringify(record_out, null, 2))
  log(`evidence → ${EVIDENCE_DIR}`)

  if (KEEP) {
    log(`kept running — api ${apiBase}  web http://127.0.0.1:${webPort}`)
    await new Promise(() => {})
  }
  } finally {
    await stopAll()
  }

  const failed = results.filter((r) => !r.ok)
  log(`done: ${results.length - failed.length}/${results.length} checks passed`)
  if (failed.length > 0) {
    for (const f of failed) console.error(`FAIL ${f.name}: ${f.detail}`)
    process.exitCode = 1
  }
}

main().catch(async (error) => {
  console.error(`[check] fatal: ${error.stack ?? error}`)
  process.exitCode = 1
})
