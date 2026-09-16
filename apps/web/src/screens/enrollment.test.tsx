import { createHash, webcrypto } from 'node:crypto'
import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'
import { parseEnrollmentObservation } from './enrollmentObservation'

const profile = { platform: 'darwin', readerName: 'VoiceOver', readerVersion: 'bundled with macOS 26.6 (build 25G72)',
  browserName: 'Safari', browserVersion: '26.6', locale: 'en-US', keyboardLayout: 'com.apple.keylayout.US' }
const profileDigest = createHash('sha256').update(JSON.stringify(Object.fromEntries(Object.entries(profile).sort()))).digest('hex')
const observation = { meaning: 'LOCAL_DECLARATION_NOT_ENROLLMENT_OR_QUALIFICATION', profile, profileDigest,
  session: { deviceId: '12345678-1234-1234-1234-123456789abc', platform: 'darwin', interactiveSessionId: '100', console: true } }
const secret = 't'.repeat(43)

describe('reviewed runner enrollment', () => {
  beforeEach(() => vi.stubGlobal('crypto', webcrypto))
  afterEach(() => vi.unstubAllGlobals())
  function setup(mode: 'success' | 'token-unknown' | 'enroll-unknown' = 'success', owner = true) {
    const server = createFakeServer({ userId: 'u-1', email: 'owner@example.test',
      workspaces: [{ workspaceId: 'ws-1', name: 'Test', role: owner ? 'OWNER' : 'MAINTAINER' }] })
    const writes: { path: string; body: Record<string, unknown>; key: string | null }[] = []
    const fetchImpl: typeof fetch = async (input, init) => {
      const path = String(input)
      if (init?.method === 'POST' && /\/runners(?:\/enrollment-tokens)?$/.test(path)) {
        writes.push({ path, body: JSON.parse(String(init.body)) as Record<string, unknown>, key: new Headers(init.headers).get('Idempotency-Key') })
        const issuing = path.endsWith('/enrollment-tokens')
        if ((issuing && mode === 'token-unknown') || (!issuing && mode === 'enroll-unknown' && writes.length === 2)) throw new TypeError('lost response')
        return new Response(JSON.stringify(issuing ? { token: secret, expiresAt: '2099-01-01T00:00:00Z' } :
          { runnerId: '11111111-1111-4111-8111-111111111111', status: 'PREFLIGHT_REQUIRED', profileDigest }),
        { status: 201, headers: { 'content-type': 'application/json' } })
      }
      return server.fetch(input, init)
    }
    render(<MemoryRouter initialEntries={['/w/ws-1/runners']}><App client={new ApiClient({ fetchImpl, cookieSource: () => '' })} /></MemoryRouter>)
    return writes
  }
  async function review(raw = JSON.stringify(observation)) {
    const user = userEvent.setup()
    await user.click(await screen.findByText('Enroll an observed desktop'))
    fireEvent.change(screen.getByLabelText(/Runner name/), { target: { value: 'Dedicated desk' } })
    fireEvent.change(screen.getByLabelText(/Desktop enrollment observation JSON/), { target: { value: raw } })
    await user.click(screen.getByRole('button', { name: 'Review enrollment draft' }))
    return user
  }
  async function issue() {
    const user = await review()
    await user.click(await screen.findByLabelText(/I reviewed this actual dedicated desktop/))
    await user.click(screen.getByRole('button', { name: 'Issue single-use enrollment token' }))
    return user
  }
  it('separates review, issuance and enrollment, never displaying the raw token', async () => {
    const writes = setup()
    const user = await review()
    await screen.findByLabelText(/I reviewed this actual dedicated desktop/)
    expect(writes).toEqual([])
    await user.click(screen.getByLabelText(/I reviewed this actual dedicated desktop/))
    await user.click(screen.getByRole('button', { name: 'Issue single-use enrollment token' }))
    await screen.findByRole('button', { name: 'Enroll reviewed desktop' })
    expect(writes).toHaveLength(1)
    expect(document.body.textContent).not.toContain(secret)
    await user.click(screen.getByRole('button', { name: 'Enroll reviewed desktop' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Preflight is still required')
    expect(writes[1]?.body).toEqual({ token: secret, name: 'Dedicated desk', session: observation.session, profile })
    expect(writes[1]?.key).toBeTruthy()
    expect(screen.getByLabelText(/Desktop enrollment observation JSON/)).toHaveValue('')
  })
  it('keeps enrollment payload and key on a lost response', async () => {
    const writes = setup('enroll-unknown')
    const user = await issue()
    await user.click(await screen.findByRole('button', { name: 'Enroll reviewed desktop' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Enrollment was not confirmed')
    expect(screen.getByLabelText(/Runner name/)).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Retry same enrollment' }))
    await screen.findByRole('status')
    expect(writes[1]).toEqual(writes[2])
  })
  it('does not retry uncertain token issuance or continue to enrollment', async () => {
    const writes = setup('token-unknown')
    await issue()
    expect(await screen.findByRole('alert')).toHaveTextContent('Token issuance outcome is unknown')
    expect(screen.queryByRole('button', { name: 'Issue single-use enrollment token' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enroll reviewed desktop' })).not.toBeInTheDocument()
    expect(writes).toHaveLength(1)
  })
  it('refuses changed profile digests before any write', async () => {
    const writes = setup()
    await review(JSON.stringify({ ...observation, profileDigest: '0'.repeat(64) }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Profile digest does not match')
    expect(writes).toEqual([])
  })
  it('does not expose infrastructure actions to a maintainer', async () => {
    const writes = setup('success', false)
    await screen.findByRole('heading', { level: 1, name: 'Runners' })
    expect(screen.queryByText('Enroll an observed desktop')).not.toBeInTheDocument()
    expect(writes).toEqual([])
  })
  it('rejects incomplete, foreign, coerced and oversized observations', async () => {
    for (const raw of ['null', '{}', 'x'.repeat(8193),
      JSON.stringify({ ...observation, session: { ...observation.session, console: 'true' } }),
      JSON.stringify({ ...observation, session: { ...observation.session, deviceId: 'host-alias' } }),
      JSON.stringify({ ...observation, profile: { ...profile, extra: 'field' } })]) {
      await expect(parseEnrollmentObservation(raw)).rejects.toThrow()
    }
  })
})
