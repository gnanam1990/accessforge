/**
 * The browser entry point.
 *
 * `BrowserRouter` rather than a hash router, because UI-UX section 3 requires stable deep links and
 * a hash is not sent to the server, so it cannot be authorised, logged or redirected.
 *
 * `StrictMode` is on. It double-invokes effects in development, which is uncomfortable and useful:
 * the session read, the abort handling and the focus management here are all things that break under
 * a second invocation if they were written assuming they run once.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from './App'
import { ApiClient } from './api/client'
import './styles/tokens.css'
import './styles/base.css'

const container = document.getElementById('root')
if (container === null) throw new Error('the #root container is missing from index.html')

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <App client={new ApiClient()} />
    </BrowserRouter>
  </StrictMode>,
)
