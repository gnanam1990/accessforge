import type { JSX, ReactNode } from 'react'
import { Brand, SectionIcon } from '../components/Brand'
import { RouteHeading, MAIN_CONTENT_ID } from '../a11y/RouteHeading'
import { SkipLink } from '../a11y/SkipLink'
import { useTheme } from '../a11y/ThemeProvider'
import { useMediaQuery } from '../shell/useMediaQuery'

const steps = [
  ['Capture a baseline', 'Start with a defined journey and retain the evidence behind each observation.'],
  ['Understand the finding', 'Bring diagnosis, source context, and a constrained repair into one review flow.'],
  ['Review the difference', 'Compare independent rerun evidence before a person accepts the result.'],
] as const

export const WelcomeLayout = ({ children }: { readonly children: ReactNode }): JSX.Element => {
  const { choice, setChoice } = useTheme()
  const systemDark = useMediaQuery('(prefers-color-scheme: dark)')
  const dark = choice === 'dark' || (choice === 'system' && systemDark)
  return <div className="af-welcome">
    <SkipLink />
    <header className="af-welcome-header">
      <Brand />
      <nav aria-label="About AccessForge">
        <a href="#workflow">The workflow</a>
        <button className="af-theme-button" onClick={() => setChoice(dark ? 'light' : 'dark')}>
          {dark ? 'Light appearance' : 'Dark appearance'}
        </button>
      </nav>
    </header>
    <main id={MAIN_CONTENT_ID} tabIndex={-1}>
      <div className="af-welcome-hero">
        <section className="af-welcome-story">
          <p className="af-eyebrow"><span className="af-signal-dot" /> THE ACCESSIBILITY WORKSPACE</p>
          <RouteHeading>Build for everyone.{' '}<br /><span className="af-accent-text">Prove every step.</span></RouteHeading>
          <p className="af-hero-description">From the first finding to a reviewed repair. Keep your accessibility journeys, evidence, and decisions connected—in one focused workspace.</p>
          <a className="af-text-action" href="#signin">Enter your workspace <span aria-hidden="true">↗</span></a>
          <div className="af-evidence-map" aria-label="Evidence workflow illustration, not live run results">
            <div className="af-map-caption"><span className="af-mono">EVIDENCE, NOT ASSUMPTIONS</span><span>Workflow illustration</span></div>
            <div className="af-map-path">
              <span><SectionIcon index={2} /><strong>Baseline</strong><small>Observe</small></span>
              <span aria-hidden="true" className="af-path-connector">→</span>
              <span><SectionIcon index={4} /><strong>Repair</strong><small>Constrain</small></span>
              <span aria-hidden="true" className="af-path-connector">→</span>
              <span><SectionIcon index={3} /><strong>Review</strong><small>Verify</small></span>
            </div>
            <p>Every conclusion needs a traceable source.</p>
          </div>
        </section>
        <section id="signin" className="af-signin-card" aria-labelledby="af-signin-title">
          <span className="af-card-symbol"><SectionIcon index={3} /></span>
          <p className="af-eyebrow">YOUR NEXT STEP</p>
          <h2 id="af-signin-title">Welcome to AccessForge</h2>
          <p className="af-secondary">Sign in to your team’s accessibility workspace.</p>
          <div className="af-signin-content">{children}</div>
          <div className="af-signin-footnote"><SectionIcon index={1} /><p>Workspace access is managed by your owner. Signing in never grants permissions on its own.</p></div>
        </section>
      </div>
      <section id="workflow" className="af-workflow" aria-labelledby="af-workflow-title">
        <div className="af-section-heading"><div><p className="af-eyebrow">A CLEAR PATH FORWARD</p><h2 id="af-workflow-title">Less guesswork. More evidence.</h2></div><p className="af-secondary">A deliberate workflow, from observation to human review.</p></div>
        <div className="af-workflow-grid">{steps.map(([title, detail], index) => <article key={title}>
          <span className="af-step-number">0{index + 1}</span><h3>{title}</h3><p>{detail}</p>
        </article>)}</div>
      </section>
    </main>
    <footer className="af-welcome-footer"><Brand /><p>Built around evidence. Designed for people.</p><span>Reader qualification required for verified runs.</span></footer>
  </div>
}
