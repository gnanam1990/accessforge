/**
 * A tab list with the manual-activation keyboard pattern.
 *
 * This is the one custom keyboard widget in the foundation, so the pattern is documented rather than
 * assumed. From the ARIA Authoring Practices tabs pattern:
 *
 * * Only the selected tab is in the tab order (`tabIndex` 0; the rest are -1), so Tab moves past the
 *   whole group instead of stopping on every tab.
 * * Left/Right move between tabs, Home/End jump to the ends, and movement **wraps**.
 * * Selection follows the arrow keys. This is automatic activation, chosen because the panels here
 *   contain already-loaded content — the practice recommends manual activation only when showing a
 *   panel is expensive, and a tab that requires Enter after arrowing is a step people miss.
 * * Each panel is labelled by its tab and is focusable, so a reader can move from the tab into the
 *   content it controls.
 *
 * Nothing in the product depends on a tab being selected: every panel's content is reachable by
 * other means, because a widget is not a place to hide required information.
 */

import type { JSX } from 'react'

import { useId, useRef } from 'react'
import type { ReactNode } from 'react'

export interface TabDefinition {
  readonly id: string
  readonly label: string
  readonly content: ReactNode
}

export interface TabsProps {
  readonly tabs: readonly TabDefinition[]
  readonly selectedId: string
  readonly onSelect: (id: string) => void
  /** Names the group for a reader who arrives at it with no surrounding context. */
  readonly label: string
}

export const Tabs = ({ tabs, selectedId, onSelect, label }: TabsProps): JSX.Element => {
  const base = useId()
  const refs = useRef(new Map<string, HTMLButtonElement>())
  // The effective selection, which is not always the requested one: a `selectedId` that names no
  // tab falls back to the first. Deriving it once and using it for the panel, `aria-selected` and
  // `tabIndex` alike keeps them agreeing. Comparing against the raw `selectedId` instead would show
  // the first panel while every tab reported itself unselected and none was in the tab order — a
  // group a keyboard could not reach at all.
  const index = Math.max(
    0,
    tabs.findIndex((tab) => tab.id === selectedId),
  )
  const effectiveId = tabs[index]?.id ?? selectedId

  const move = (to: number): void => {
    const wrapped = (to + tabs.length) % tabs.length
    const target = tabs[wrapped]
    if (target === undefined) return
    onSelect(target.id)
    refs.current.get(target.id)?.focus()
  }

  return (
    <div>
      <div role="tablist" aria-label={label} className="af-tabs__list">
        {tabs.map((tab, position) => (
          <button
            key={tab.id}
            ref={(element) => {
              if (element === null) refs.current.delete(tab.id)
              else refs.current.set(tab.id, element)
            }}
            type="button"
            role="tab"
            id={`${base}-tab-${tab.id}`}
            aria-selected={tab.id === effectiveId}
            aria-controls={`${base}-panel-${tab.id}`}
            tabIndex={tab.id === effectiveId ? 0 : -1}
            className="af-tabs__tab"
            onClick={() => onSelect(tab.id)}
            onKeyDown={(event) => {
              switch (event.key) {
                case 'ArrowRight':
                  event.preventDefault()
                  move(position + 1)
                  break
                case 'ArrowLeft':
                  event.preventDefault()
                  move(position - 1)
                  break
                case 'Home':
                  event.preventDefault()
                  move(0)
                  break
                case 'End':
                  event.preventDefault()
                  move(tabs.length - 1)
                  break
                default:
                  break
              }
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`${base}-panel-${tab.id}`}
          aria-labelledby={`${base}-tab-${tab.id}`}
          hidden={tab.id !== effectiveId}
          tabIndex={0}
        >
          {tab.content}
        </div>
      ))}
    </div>
  )
}
