/**
 * The body of a route whose screen belongs to a later module.
 *
 * It renders the page's heading — so the route focus behaviour, the breadcrumb and the document
 * structure are all real and testable — followed by an explicit statement that the screen does not
 * exist. Nothing is requested from the server, and nothing on the page is data.
 */

import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { NotBuiltYetState } from '../components/states'
import type { RouteDefinition } from './routeMap'

export const ScreenNotBuilt = ({ route }: { readonly route: RouteDefinition }): JSX.Element => (
  <>
    <RouteHeading>{route.heading}</RouteHeading>
    <NotBuiltYetState screen={route.heading} ownedByModule={route.ownedByModule} />
  </>
)
