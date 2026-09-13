# Retained diagnosis reviewer view

This slice connects the retained diagnosis history API to the existing finding detail page and
adds a bounded, read-only findings list to the workspace overview, making that page reachable.
It depends on the retained-diagnosis backend in PR 42. Opening either page invokes no model,
changes no finding status and grants no permission to apply a repair.

The model section displays original obstacles, task steps, uncertainty, alternatives, evidence
references, frozen source locations and supported repair briefs. Allowed files, functional
constraints, protected surfaces and stop recommendations remain explicit. Machine outcomes and
human assessments stay in separate sections, with no model-generated compliance claim.

Original and follow-up entries retain their exact predecessor and provenance digests. The API's
bounded-history flag is visible; a missing predecessor is not invented. Deleted source evidence
suppresses all analysis text even if a faulty response also carries it. Missing or unsupported
stored schemas render an unavailable explanation without breaking machine or human evidence.
All model text uses React text nodes, never HTML, Markdown execution or generated external links.

UI guidance influenced sequential headings, semantic data/list markup, native disclosure for
provenance and reuse of the existing keyboard-scrollable table. No new visual system was introduced.
Local validation: web typecheck/production build and diff checks. CI-only component regressions
cover overview navigation, inert model text, unchanged INCONCLUSIVE outcomes, repair scope,
retention erasure, unsupported/unknown formats and older-server absence. These are not actual
screen-reader, browser-runtime or model-provider acceptance proof.

Remaining: authorized operator diagnosis request workflow, provider usage/reconciliation, actual
retained source/model/reader acceptance, and the remaining repair/execution/release integration.
