# Rewind: Design Notes

## 1. Architecture

The system has two phases with very different cost profiles. **Discovery** runs Claude in
a reactive observe-decide-act loop against a real browser: each turn, the current
accessibility-tree snapshot goes to Claude, it decides one action, the action executes via
Playwright, the result becomes the next observation. Reactive rather than pre-planned so it
can adapt to runtime conditions instead of assuming a happy path. A `finish` tool lets
Claude self-report which of its actions succeeded and what checkpoint it observed - this is
where the artifact's checkpoint concretely comes from. **Replay** takes the resulting
artifact and executes it mechanically - no reasoning, no LLM call - via parameter
substitution, locator resolution with a fallback chain, and a three-way result
classification. This is the actual production path an agent invokes.

**Stack**: Python + Pydantic (typed artifact contract), Playwright's sync API (no
concurrent replay needed at this scale, and escalation needs to cleanly block on a
terminal prompt), and a hand-rolled Claude tool-use loop rather than Anthropic's
screenshot/coordinate-based Computer Use tool or an existing agent framework - coordinates
are a poor thing to store in a reusable artifact, and a framework would hide the design
decisions this project is meant to demonstrate.

**Perception**: the accessibility tree, not screenshots or raw HTML - built from plain HTML
semantics, so it works on legacy markup with no test IDs. One real gap found in practice:
ParaBank's login form has no `<label>` association, so accessible-name locators return
nameless fields. Fixed by augmenting the observation with a supplementary DOM
attribute list for exactly this case (`src/discovery/accessibility.py`).

**Login is a separate, composed capability**, not steps duplicated into every capability.
`run_chain()` runs artifacts sequentially against one shared session. Credentials never
appear as literals - Claude only sees `{username}`/`{password}` placeholder tokens,
resolved only at the Playwright execution boundary, never logged.

**Trade-offs**: single process, no queues - nothing at this scale needs scaling
infrastructure yet. Conversation history grows every turn since the API is stateless;
bounded and fine at this scale (5-7 turns/run), would need summarization for much
longer discovery runs (see Limitations and next steps).

## 2. Artifact schema

`Artifact` (`src/schema.py`) holds `inputs`/`outputs` (`ParamSpec`), ordered `steps`
(`Step`, each with a `Locator`), a `checkpoint` (list of `Condition`), and `known_outcomes`
(list of `OutcomeRule`).

**`Locator` carries a fallback chain, and `reasoning` is required, not optional.** Some
elements have a clean accessible name, others only a stable `id`/`name` with no label
association - found on the live target, not assumed. Rather than one guaranteed-fragile
locator, the schema admits "try this, then this." `reasoning` is required so every locator
choice is explainable; making it a required tool-call argument means it's populated by
Claude at the moment it acts, never invented after the fact.

**The artifact is decoupled from the raw discovery transcript.** `created_from_run` points
at the full raw action log rather than embedding it - the raw log is read once, by
`compile.py`, and never touched again; replay doesn't know it exists. Keeps the artifact
small and reviewable while the messy discovery-time reasoning stays available separately
for debugging.

**`known_outcomes` is narrow on purpose**, and getting it wrong once taught the lesson
directly: it's for a run landing on a genuinely different recognized page (session expiry
redirecting to login), not different *data* on the *same* expected page. Our first
compiled `request_loan` artifact got this backwards - its checkpoint included
outcome-specific text ("has been approved"), which would have reported a legitimate Denied
result as a hard failure. Fixed by broadening the checkpoint to an outcome-agnostic
condition and fixing the underlying tool guidance; a second, independent discovery run
then produced a correctly outcome-agnostic checkpoint unprompted.

**Known limitation**: `ParamSpec` declares an output's shape but not where on the page to
find it - no schema-driven extraction locator. Worked around with a small
capability-keyed extractor registry (`src/replay/extractors.py`) rather than changing the
frozen schema mid-project.

## 3. Determinism & error handling

Determinism comes from three things: a fixed step sequence, dumb parameter substitution
(a plain `"{name}"` string swap, no expression language), and locator resolution built on
the *same* selector-building logic discovery uses (`src/locator_utils.py`) - a locator
discovery validated must resolve identically at replay time.

After all steps execute, `known_outcomes` is checked before `checkpoint` (a loose
checkpoint match could otherwise coincidentally overlap with a known-outcome page). The
result is one of three buckets: `success`, `known_outcome`, or `hard_failure` (with the
failing step, what was expected, what was actually observed).

Real failures found via live testing, not design review:

- **Replay never navigated to the target first.** Artifact steps assume the page is
  already there, true during discovery's separate bootstrap step, but nothing established
  it for replay. Fixed by adding the same deterministic bootstrap.
- **`networkidle` didn't reliably catch an AJAX-driven update.** Invisible during discovery
  because Claude's next API call added enough real latency to mask it; replay has no such
  delay, so the same gap became a reproducible failure. Fixed with an explicit buffer wait.
  Lesson: replay can't inherit discovery's timing behavior, it needs its own.
- **An unsatisfiable `element_visible` condition with no locator silently failed an entire
  checkpoint**, since conditions are ANDed. Fixed the artifact and the guidance that
  produced it.

**Known gap**: a complete error taxonomy has three tiers: business outcomes, recoverable
conditions (for example, retrying a transient load), and hard failures. This system
implements business outcomes and hard failures well; there's no retry tier - any execution
failure is an immediate hard failure.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** `BrowserTools`' interface (five methods, each taking a `Locator`
strategy+value, returning observation text) is already technology-agnostic - nothing above
it knows Playwright exists. That's the seam between "how we perceive/act on a surface" and
"the recorded flow." A more hostile legacy web app needs no schema change - the
`structural`/XPath fallback already exists for it. A **desktop app** means a new backend
behind the *same* interface, using a native OS accessibility API instead of Playwright -
conceptually the same tree-of-roles-and-names idea, different plumbing. `Locator.strategy`
would likely gain an OS-specific option; `Step`/`Condition`/`ParamSpec` need no changes.

**Multi-tenant reuse.** Locators, checkpoint shape, and input/output schema describe the
*capability*; only `target.base_url` (and possibly tenant-customized copy) is genuinely
tenant-specific, assuming tenants share the same underlying vendor product. Proposed (not
built): a `TenantOverride` object per (tenant, capability) holding only the delta from a
base artifact - most tenants need zero overrides beyond their URL. Replay would merge
base + override at call time rather than storing a full duplicate per tenant.

**Drift detection needs no new infrastructure** - replay's existing `hard_failure`
reporting already is the signal. A diverged tenant instance simply starts failing
informatively; aggregating failure rates per tenant is monitoring layered on what exists,
not a new architectural piece. The artifact's `version` field is the natural pin point for
a genuine vendor release bump.

None of this was implemented - design proposal only.

## 5. Escalation & handoff

One primitive, `request_intervention`, called from three real trigger points: discovery's
explicit `finish(status="stuck")`, a replay step/checkpoint failure, and a risky action
(large loan amount) needing confirmation *before* it executes.

**Mechanism**: non-headless browser, blocking on a real terminal `input()` while the
visible window sits exactly as automation left it - the same live `Page`/session, not a
fresh one. This is intentionally a minimal operator surface rather than a remote console;
what's real is the control-transfer primitive itself (pause, expose the live session,
explicit resume signal, logged before/after state).

**Resume differs by trigger**: discovery feeds the human's help back into the conversation
and lets the *same* loop continue for its remaining turns - genuine "resume or complete."
Replay re-checks the checkpoint exactly once rather than re-running all steps. The
risky-action gate just proceeds after confirmation. Every trigger caps at one escalation
per run, so an unresolvable problem doesn't pause forever.

Verified live: a deliberately impossible discovery goal correctly escalated and resumed
before genuinely running out of turns; a deliberately broken replay locator correctly
escalated then reported a real hard failure after a no-op resume; the confirmation gate
correctly fired for a $50,000 loan and correctly didn't for a $1,000 one.

## 6. Safety

**Allowlist enforcement**: domains/routes/action types, checked at the point an action
touches a live page (discovery's `BrowserTools`, replay's `engine.py`) - not trusted to
Claude's reasoning, and enforced identically for replay, which has no reasoning to trust in
the first place.

**Risk classification**: risky `click`s above a parameter threshold require confirmation;
smaller ones auto-proceed, since "always ask a human" would defeat unattended replay. A
real bug found via code review before wiring this up: the initial logic would have
required confirmation on *every* click, including harmless ones, since it only skipped
confirmation via a loan-specific escape hatch. Fixed by requiring an actual risk signal to
be present, not just "this action type is generically risky."

**Redaction - the most significant finding in this project.** While preparing evidence for
submission, a real password was found in plaintext across six log files. Two compounding
causes: field-name redaction wasn't recursive, so a secret nested inside a `"params"` dict
went unseen; and a value-based scrubber for secrets appearing as plain content (e.g. a
typed password inside an accessibility-tree observation) existed from early on but was
never actually wired into anything. Fixed both, verified live in both directions with zero
leaks, and discarded/regenerated all prior evidence rather than salvaging it. A stray file
with an unprotected API key copy was also found and removed before the first commit.

**Known limits**: `ParamSpec` has no per-field sensitivity flag, so redaction infers
secrecy from field-name patterns rather than a schema guarantee. The allowlist is fully
configurable in code but not exposed as an example file yet.

## 7. Limitations and next steps

**Not implemented yet**: retry logic for recoverable conditions (every failure is an
immediate hard failure); an extraction locator on `ParamSpec` (worked around with a code
registry); `TenantOverride` (design only, nothing implemented); a real remote co-browsing
console (the underlying primitive is real, the surface is local); an example
`allowlist.json`; conversation-history summarization for long discovery runs; and a
per-field secret flag on `ParamSpec`. Two more surfaced by the benchmark: replay waits a
fixed 1 s after every step, which is most of the 3.7 s loan step, and the recorded loan
flow uses the default funding account, so the declared `from_account_id` input isn't yet
selected by any step.

**Next, in priority order**: (1) condition-based waits and retry-with-backoff in replay,
closing the biggest performance and error-taxonomy gaps; (2) an MCP server so any AI agent
can call recorded capabilities as tools; (3) unit tests and CI against a local mock app;
(4) an extraction locator and a per-field sensitivity flag in the schema; (5) a second app
variant demonstrating `TenantOverride` for real.
