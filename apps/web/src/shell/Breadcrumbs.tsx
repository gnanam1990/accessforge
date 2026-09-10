/**
 * The trail, as a `<nav>` containing an ordered list.
 *
 * Ordered because the sequence is the meaning. The current page is present but is not a link, and
 * carries `aria-current="page"`: a link to where you already are is a trap for anyone navigating by
 * links, and removing the last item entirely leaves the trail ending one step short of the truth.
 */

import type { JSX } from 'react'

import { Link } from 'react-router-dom'

import type { Crumb } from '../routes/routeMap'

export const Breadcrumbs = ({ crumbs }: { readonly crumbs: readonly Crumb[] }): JSX.Element | null => {
  if (crumbs.length === 0) return null
  return (
    <nav aria-label="Breadcrumb">
      <ol className="af-breadcrumbs-list">
        {crumbs.map((crumb) => (
          <li key={`${crumb.label}-${crumb.href ?? 'current'}`}>
            {crumb.href === null ? (
              <span aria-current="page">{crumb.label}</span>
            ) : (
              <Link className="af-link" to={crumb.href}>
                {crumb.label}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  )
}
