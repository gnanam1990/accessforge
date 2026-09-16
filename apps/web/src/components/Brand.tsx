import type { JSX } from 'react'

export const Brand = (): JSX.Element => (
  <span className="af-brand">
    <svg className="af-brand-mark" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="M16 5 27 25H5L16 5Z" stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round" />
      <path d="m12 19 3 3 6-8" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
    <span>Access<span className="af-brand-accent">Forge</span></span>
  </span>
)

export const SectionIcon = ({ index = 0 }: { readonly index?: number }): JSX.Element => {
  const paths = [
    'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    'M3 7h7l2-3h9v16H3z',
    'M8 4v16l12-8z',
    'M12 3 3 7v6c0 4 9 8 9 8s9-4 9-8V7z M8 12l3 3 5-6',
    'M8 5 2 12l6 7 M16 5l6 7-6 7 M14 3l-4 18',
    'M4 20V10 M12 20V4 M20 20v-8',
  ]
  return <svg className="af-section-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={paths[index % paths.length]} />
  </svg>
}
