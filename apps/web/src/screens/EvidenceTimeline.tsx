/**
 * The canonical chain, as an ordered list a person can read and operate with a keyboard.
 *
 * Five decisions, each one a way the obvious version misleads.
 *
 * **A semantic ordered list, not a canvas.** `<ol>` with one `<li>` per event: a screen-reader user
 * gets position and count for free, and the order is the document's order. UI-UX section 5 forbids
 * making the only usable replay a visual one, and a canvas is a visual one no matter what is
 * layered on top of it.
 *
 * **Paginated, complete, and unvirtualized.** Every event on a page is in the DOM. Virtualization is
 * an optional visual optimisation with an equivalent complete accessible mode; this *is* the
 * complete mode, so there is nothing to be equivalent to yet.
 *
 * **Position is the sequencer's.** Each entry leads with its sequence number, and the source time is
 * shown as context and labelled as the producer's own clock. Sorting by that field would reorder an
 * attempt according to whose machine was fast.
 *
 * **Provenance is a labelled field, not a colour or an icon.** Who submitted a record decides what
 * it can establish, and a timeline that rendered a supervisor receipt and an observer receipt
 * identically would erase the distinction the outcome rests on.
 *
 * **Search filters, and says that it is filtering.** A filtered list that looked like the whole
 * chain would be a gap presented as an absence of events.
 */

import { useId, useState } from 'react'
import type { JSX } from 'react'

import { Button } from '../components/Button'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { Pagination } from '../components/Pagination'
import type { Timeline, TimelineEvent } from '../api/resources'

const matches = (event: TimelineEvent, term: string): boolean => {
  if (term.trim() === '') return true
  const needle = term.trim().toLowerCase()
  return (
    event.eventType.toLowerCase().includes(needle) ||
    (event.producerId ?? '').toLowerCase().includes(needle) ||
    JSON.stringify(event.payload ?? {})
      .toLowerCase()
      .includes(needle)
  )
}

export const EvidenceTimeline = ({
  timeline,
  onPrevious,
  onNext,
  pageStart,
}: {
  readonly timeline: Timeline
  readonly onPrevious: (() => void) | null
  readonly onNext: (() => void) | null
  /** The `after_sequence` this page was read from, for the position description. */
  readonly pageStart: number
}): JSX.Element => {
  const [term, setTerm] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const searchId = useId()

  const shown = timeline.events.filter((event) => matches(event, term))
  const filtering = term.trim() !== ''

  return (
    <section className="af-stack">
      <h2>Evidence timeline</h2>

      <Notice tone="information" heading="How this is ordered" headingLevel={3}>
        <p>{timeline.orderingMeaning}</p>
      </Notice>

      <FormField
        id={searchId}
        label="Search this page"
        hint="Matches the event type, the producer and the recorded payload. It filters what is shown; it does not fetch more."
      >
        {({ id, describedBy }) => (
          <input
            id={id}
            type="search"
            value={term}
            aria-describedby={describedBy}
            onChange={(event) => setTerm(event.target.value)}
          />
        )}
      </FormField>

      {filtering && (
        <p role="status" className="af-secondary">
          {/* Said out loud, because a filtered list that looked like the whole chain would present
              a gap as an absence of events. */}
          Showing {shown.length} of {timeline.events.length} events on this page. This is a filtered
          view, not the whole attempt.
        </p>
      )}

      {timeline.events.length === 0 ? (
        <Notice tone="warning" heading="No events on this page" headingLevel={3}>
          <p>
            Nothing was recorded at this position. An empty transcript after a run started is
            missing evidence, not a quiet successful journey — the evidence completeness section
            says which.
          </p>
        </Notice>
      ) : (
        <ol className="af-stack">
          {shown.map((event) => (
            <li key={event.eventId} className="af-panel af-stack">
              <h3 style={{ margin: 0, fontSize: 'var(--af-text-body)' }}>
                <span className="af-mono">#{event.sequence}</span> {event.eventType}
              </h3>
              <dl>
                <dt>Recorded by</dt>
                <dd>
                  {event.producerId === null ? (
                    <span className="af-secondary">
                      no producer recorded for this position
                    </span>
                  ) : (
                    <>
                      <code>{event.producerId}</code>
                      {event.producerSequence !== null && (
                        <span className="af-secondary">
                          {' '}
                          · its own record {event.producerSequence}
                        </span>
                      )}
                    </>
                  )}
                </dd>
                <dt>Producer’s clock</dt>
                <dd>
                  <time dateTime={event.sourceTime}>{event.sourceTime}</time>
                  <span className="af-secondary">
                    {' '}
                    · context only; producers’ clocks disagree and none of them decides order
                  </span>
                </dd>
                <dt>Admitted</dt>
                <dd>
                  <time dateTime={event.receivedTime}>{event.receivedTime}</time>
                </dd>
              </dl>

              <Button
                aria-expanded={expanded === event.sequence}
                onClick={() =>
                  setExpanded((current) => (current === event.sequence ? null : event.sequence))
                }
              >
                {expanded === event.sequence
                  ? `Hide what was recorded at #${event.sequence}`
                  : `Show what was recorded at #${event.sequence}`}
              </Button>

              {expanded === event.sequence && (
                <>
                  <pre className="af-mono">{JSON.stringify(event.payload, null, 2)}</pre>
                  <dl>
                    <dt>Payload digest</dt>
                    <dd>
                      <code>{event.payloadDigest}</code>
                    </dd>
                    <dt>Previous event hash</dt>
                    <dd>
                      <code>{event.previousEventHash}</code>
                    </dd>
                  </dl>
                </>
              )}
            </li>
          ))}
        </ol>
      )}

      <Pagination
        label="Evidence timeline pages"
        onPrevious={onPrevious}
        onNext={onNext}
        positionDescription={
          timeline.exhausted
            ? `Showing events after ${pageStart}. This is the end of the recorded chain.`
            : `Showing events after ${pageStart}. There are more.`
        }
      />
    </section>
  )
}
