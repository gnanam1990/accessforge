/**
 * The stylesheet, checked as data.
 *
 * Two kinds of assertion, and both exist because the alternative is a number in a document that was
 * true when someone measured it.
 *
 * **Measured contrast.** The ratios are computed from the declarations in `tokens.css` itself, with
 * the WCAG 2.x relative-luminance formula implemented here rather than imported. A table of ratios in
 * a handoff is a claim about a palette at a moment; this fails the build the day somebody nudges a
 * hex value. UI-UX section 2 says the palette is "candidate pairs, not a substitute for measured
 * contrast" — this is the measurement.
 *
 * **Structural rules.** `outline: none` must not appear anywhere; the focus ring must be 3px with an
 * offset; reduced motion must zero the duration; control boundaries must use the token that meets
 * 3:1 rather than the decorative one. Each is a rule a future edit could break silently, and none of
 * them is visible in a screenshot taken with a mouse.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const read = (name: string): string => readFileSync(join(__dirname, name), 'utf8')

/**
 * Declarations only.
 *
 * The structural assertions below search for forbidden declarations, and these stylesheets explain at
 * length *why* those declarations are forbidden — so a check run against the raw text fails on its
 * own rationale. Comments are stripped first. This is not the test being softened: it is the
 * difference between checking what the browser will apply and grepping an essay.
 */
const withoutComments = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, '')

const TOKENS = withoutComments(read('tokens.css'))
const BASE = withoutComments(read('base.css'))

/** WCAG 2.x relative luminance. */
const luminance = (hex: string): number => {
  const channel = (pair: string): number => {
    const value = Number.parseInt(pair, 16) / 255
    return value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4)
  }
  const r = channel(hex.slice(1, 3))
  const g = channel(hex.slice(3, 5))
  const b = channel(hex.slice(5, 7))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

const contrast = (a: string, b: string): number => {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number]
  return (high + 0.05) / (low + 0.05)
}

/** The declarations inside one rule, as token name to hex. */
const paletteIn = (selector: string): Record<string, string> => {
  const start = TOKENS.indexOf(selector)
  expect(start, `${selector} is missing from tokens.css`).toBeGreaterThan(-1)
  const body = TOKENS.slice(start, TOKENS.indexOf('}', start))
  const palette: Record<string, string> = {}
  for (const match of body.matchAll(/(--af-[\w-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    palette[match[1] as string] = (match[2] as string).toLowerCase()
  }
  return palette
}

const LIGHT = paletteIn(':root {')
const DARK = paletteIn(":root[data-theme='dark'] {")

/** Normal-size text: 4.5:1. Nothing in this product states essential information in large text. */
const TEXT_PAIRS: readonly (readonly [string, string])[] = [
  ['--af-foreground', '--af-background'],
  ['--af-foreground', '--af-surface'],
  ['--af-foreground', '--af-surface-sunken'],
  ['--af-foreground-secondary', '--af-background'],
  ['--af-foreground-secondary', '--af-surface'],
  ['--af-foreground-secondary', '--af-surface-sunken'],
  ['--af-primary', '--af-surface'],
  ['--af-primary', '--af-background'],
  ['--af-destructive', '--af-surface'],
  ['--af-destructive', '--af-background'],
  ['--af-primary-foreground', '--af-primary'],
  ['--af-destructive-foreground', '--af-destructive'],
  ['--af-status-neutral', '--af-surface'],
  ['--af-status-progress', '--af-surface'],
  ['--af-status-pass', '--af-surface'],
  ['--af-status-fail', '--af-surface'],
  ['--af-status-inconclusive', '--af-surface'],
  ['--af-status-interrupted', '--af-surface'],
  ['--af-status-neutral', '--af-background'],
  ['--af-status-progress', '--af-background'],
  ['--af-status-pass', '--af-background'],
  ['--af-status-fail', '--af-background'],
  ['--af-status-inconclusive', '--af-background'],
  ['--af-status-interrupted', '--af-background'],
]

/** Focus indicators and control boundaries: 3:1, WCAG 1.4.11. */
const NON_TEXT_PAIRS: readonly (readonly [string, string])[] = [
  ['--af-focus-ring', '--af-background'],
  ['--af-focus-ring', '--af-surface'],
  ['--af-focus-ring-contrast', '--af-focus-ring'],
  ['--af-border-strong', '--af-surface'],
  ['--af-border-strong', '--af-background'],
  ['--af-border-strong', '--af-surface-sunken'],
]

describe.each([
  ['light', LIGHT],
  ['dark', DARK],
])('measured contrast in the %s palette', (_name, palette) => {
  it.each(TEXT_PAIRS)('%s on %s reaches 4.5:1', (foreground, background) => {
    const a = palette[foreground]
    const b = palette[background]
    expect(a, `${foreground} is not defined in this palette`).toBeDefined()
    expect(b, `${background} is not defined in this palette`).toBeDefined()
    expect(contrast(a as string, b as string)).toBeGreaterThanOrEqual(4.5)
  })

  it.each(NON_TEXT_PAIRS)('%s against %s reaches 3:1', (foreground, background) => {
    const a = palette[foreground]
    const b = palette[background]
    expect(a, `${foreground} is not defined in this palette`).toBeDefined()
    expect(b, `${background} is not defined in this palette`).toBeDefined()
    expect(contrast(a as string, b as string)).toBeGreaterThanOrEqual(3)
  })

  it('defines every colour the dark blocks redefine', () => {
    // A token defined only inside a media query disappears the moment the theme is toggled the other
    // way. The light palette on bare :root has to be complete.
    for (const token of Object.keys(palette)) {
      expect(LIGHT[token], `${token} has no light definition`).toBeDefined()
    }
  })
})

describe('the dark palette is declared twice, for the two ways it can apply', () => {
  it('has a prefers-color-scheme block that an explicit light choice overrides', () => {
    expect(TOKENS).toContain('@media (prefers-color-scheme: dark)')
    // Without the :not(), a person who chose light would still get dark from their system setting.
    expect(TOKENS).toContain(":root:not([data-theme='light'])")
  })

  it('and an explicit attribute block, so the choice wins in both directions', () => {
    expect(TOKENS).toContain(":root[data-theme='dark']")
  })

  it('declares the same set of colours in both dark blocks', () => {
    const media = paletteIn(":root:not([data-theme='light']) {")
    expect(Object.keys(media).sort()).toEqual(Object.keys(DARK).sort())
    for (const [token, value] of Object.entries(media)) {
      expect(DARK[token], `${token} differs between the two dark blocks`).toBe(value)
    }
  })
})

describe('focus', () => {
  it('is never removed, anywhere', () => {
    // The most common way a keyboard user loses their place is a designer removing the ring and a
    // developer never noticing. The only reliable defence is that the removal is not written.
    for (const [name, css] of [
      ['tokens.css', TOKENS],
      ['base.css', BASE],
    ] as const) {
      expect(css, `${name} removes an outline`).not.toMatch(/outline:\s*(none|0)/)
    }
  })

  it('is a 3px ring with an offset, as the specification requires', () => {
    expect(BASE).toMatch(/:focus-visible\s*\{[^}]*outline:\s*3px solid var\(--af-focus-ring\)/)
    expect(BASE).toMatch(/:focus-visible\s*\{[^}]*outline-offset:/)
  })
})

describe('motion', () => {
  it('zeroes the token under a reduced-motion preference', () => {
    expect(TOKENS).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*:root\s*\{\s*--af-motion-duration:\s*0ms/,
    )
  })

  it('also neutralises transitions and animations declared elsewhere', () => {
    // The token covers this stylesheet. The blanket rule covers a component that declared its own
    // duration, and a third-party stylesheet that was never going to read the token.
    expect(BASE).toMatch(/@media \(prefers-reduced-motion: reduce\)/)
    expect(BASE).toContain('transition-duration: 0.01ms !important')
  })

  it('never uses display or visibility to hide the skip link', () => {
    const start = BASE.indexOf('.af-skip-link {')
    const body = BASE.slice(start, BASE.indexOf('}', start))
    // Either would remove it from the tab order, which is the one thing a skip link must not lose.
    expect(body).not.toMatch(/display:\s*none/)
    expect(body).not.toMatch(/visibility:\s*hidden/)
    expect(body).toContain('transform: translateY(-200%)')
  })
})

describe('control boundaries use the token that meets 3:1', () => {
  const controlRules = [
    '.af-field input,',
    '.af-button--secondary {',
    '.af-dialog {',
  ] as const

  it.each(controlRules)('%s is bounded by --af-border-strong', (selector) => {
    const start = BASE.indexOf(selector)
    expect(start, `${selector} is missing from base.css`).toBeGreaterThan(-1)
    const body = BASE.slice(start, BASE.indexOf('}', start))
    expect(body).toMatch(/border(-color)?:[^;]*--af-border-strong/)
  })
})

describe('hit areas and units', () => {
  it('gives controls a 44px minimum without a fixed height', () => {
    // `min-height` rather than `height`, so a larger text preference grows the target instead of
    // clipping the label inside it.
    expect(BASE).toMatch(/\.af-button\s*\{[^}]*min-height:\s*44px/)
    expect(BASE).not.toMatch(/\.af-button\s*\{[^}]*\sheight:\s*44px/)
  })

  it('uses no native pt or dp units, which UI-UX section 2 forbids in CSS', () => {
    for (const css of [TOKENS, BASE]) {
      expect(css).not.toMatch(/:\s*-?\d+(\.\d+)?(pt|dp)\b/)
    }
  })

  it('sets no root font size in pixels, so a browser text preference scales everything', () => {
    const start = BASE.indexOf('html {')
    const body = BASE.slice(start, BASE.indexOf('}', start))
    expect(body).not.toMatch(/font-size:\s*\d+px/)
  })
})
