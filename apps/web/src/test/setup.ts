import '@testing-library/jest-dom/vitest'

/**
 * jsdom implements neither `matchMedia` nor `IntersectionObserver`. The shell reads `matchMedia`
 * for the reduced-motion and colour-scheme preferences, so it is stubbed here with a value that
 * reports *no* preference — the default a browser reports when the user has expressed none.
 *
 * Individual tests override it. A test that cares about reduced motion sets the preference itself
 * rather than relying on a global default, because a global default that happened to match would
 * make the test pass without exercising anything.
 */
const noPreference = (query: string): MediaQueryList =>
  ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList

if (!window.matchMedia) {
  window.matchMedia = noPreference as typeof window.matchMedia
}
