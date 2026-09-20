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

**Where an output lives is data, not code.** `ParamSpec` originally declared an output's
shape but not where on the page to find it, so `request_loan` needed a hand-written
function in a capability-keyed registry (`src/replay/extractors.py`). Adding
`get_account_overview`, whose result is a table, forced the fix: an output can carry an
`extract` rule, either a `regex` (first capture group of the page text) or a `table` (a row
selector plus named, typed columns), and replay reads it with generic code. Numbers like
`$1,395.50` or `-$2300.00` are converted, and a table that fills in by AJAX is waited for.
The row selector does the real work: the overview's Total row has three cells, one blank, so
it would slip through a cell-count check. `tbody tr:has(a)` selects only account rows (they
link to their activity page). The rules are still written by hand from the page markup
rather than discovered by Claude, and `request_loan` still uses the registry.

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
  delay, so the same gap became a reproducible failure. First patched with a fixed 1 s
  sleep after every step, which the benchmark later showed was most of replay's runtime.
  Replaced with a poll for the state replay actually needs: after the last step it waits
  until a known outcome or the whole checkpoint is visible (5 s cap). Between steps no wait
  is needed, since each step's locator waits for its own element. That is both faster and
  safer on a slow server. Lesson: replay can't inherit discovery's timing behavior, and a
  fixed sleep is a guess where a condition is available.
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

**The gate fails closed.** Live use showed a gap: the confirmation check ran only inside
the human-pause code, and that is off by default, so `--no-escalation`, the benchmark and
any direct caller of `replay()` skipped it entirely. Only the MCP server had its own copy.
Now `--no-escalation` means "don't wait for a human", not "no safety": when a call needs
approval and nobody can be asked, `replay()` raises `ApprovalRequired` before touching the
browser, the CLI exits with code 3, and the MCP server returns `needs_human_approval`. All
three ask the same helper (`artifact_needs_approval`). The rule itself is now a table of
parameter name to limit (`approval_thresholds`, default `loan_amount: 5000`), so a new
capability's amount is one more entry, and a value that can't be read as a number counts as
over the limit. That last case matters: `nan` is not greater than anything, so the check is
written as `not value <= limit`. With escalation on, behavior is unchanged: it pauses on
the pre-filled form for a person.

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
immediate hard failure); discovering `extract` rules with Claude instead of writing them by
hand, and migrating `request_loan` off the code registry; `TenantOverride` (design only, nothing implemented); a real remote co-browsing
console (the underlying primitive is real, the surface is local); an example
`allowlist.json`; conversation-history summarization for long discovery runs; and a
per-field secret flag on `ParamSpec`. Also: the recorded loan flow uses the default funding
account, so the declared `from_account_id` input isn't yet selected by any step, and
running replays back to back can trip the demo site's Cloudflare rate limit (it happened
during benchmarking; replay reported it as a hard failure with the "rate limited" page in
its diagnostics).

**Next, in priority order**: (1) retry-with-backoff in replay, which would also ride out a
transient rate limit; (2) CI that runs the tests and a replay against the ParaBank Docker
image; (3) migrating `request_loan`'s outputs into its artifact, and a per-field
sensitivity flag in the schema; (4) more capabilities (transfer funds needs the account
dropdown, which also fixes `from_account_id`); (5) a second app variant demonstrating
`TenantOverride` for real.

## 8. Test target: a local ParaBank

The public ParaBank demo wipes its database every so often. During this work a freshly
registered account vanished twice within about an hour, which failed a discovery run for a
reason unrelated to the code. `PARABANK_BASE_URL` (`src/config.py`) points discovery, replay
and the benchmark at the official `parasoft/parabank` Docker image instead: a stable
database, a seeded `john` / `demo` user, no rate limiter.

The design constraint was that artifacts stay portable. They always record the canonical
public URL; replay rebases it onto the configured instance at run time (`rebase`), and
compiling an artifact converts a local URL back (`canonicalize`), so a capability recorded
against `localhost` doesn't commit `localhost`. The safety allowlist's default domain follows
the configured host, so the guardrail still holds for whichever instance is in use. One
bug worth remembering: `.env.example` ships `PARABANK_BASE_URL=` blank, which dotenv loads
as an empty string rather than "unset", so the lookup uses `or`, not a default argument.

## 9. MCP server

`src/mcp_server.py` exposes each artifact as an MCP tool over stdio, so an agent calls
`request_loan(...)` without knowing a browser is involved. The tool's input schema is
generated from the artifact's `inputs`, which is the payoff of typing the artifact.

Decisions that matter here, because the caller is now another model rather than a person:

- **Credentials stay server-side.** `login` is not exposed as a tool; the server logs in
  from its own environment before every call. An agent that can call `login` could be
  talked into handling a password.
- **The confirmation gate becomes a refusal.** Terminal handoff needs a person at the
  keyboard, and an MCP call has none. A loan above the limit returns
  `needs_human_approval` without opening a browser. Refusing is the safe default; a real
  deployment would route that to an approval queue.
- **The failure payload is deliberately thin.** It reports the failing step and what was
  expected, not the observed page text, since that text can hold customer data and would
  otherwise flow into a model's context. Full diagnostics go to local logs (`runs/`).
- **Calls are serialized with a lock, and each runs in a worker thread.** Playwright's
  sync API cannot run inside the server's asyncio loop, and a shared browser between
  concurrent calls would cross their sessions. Throughput is one call at a time, which
  also keeps the demo site's rate limiter happy. Real concurrency would need a browser
  pool.

Tested two ways: offline tests drive the server through an in-process MCP client with the
browser stubbed (`tests/test_mcp_server.py`), and one live call over real stdio returned an
approved loan against ParaBank.
