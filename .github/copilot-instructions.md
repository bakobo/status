# Copilot Code Review — Repository Instructions

These rules apply to every PR. They are the fleet's shared instructions with a repository-context section written for this repo; where the two disagree, the repository context wins.

**This file is a deliberate exception to the fleet copy, not drift.** Every other Bakobo repo carries one of two byte-identical 57-line variants with no per-repo content. Four of those lines are false here and are changed below, with the reasons in the commit that added this file. There is no `sync-tier1`-style drift check for this filename, so a later change to the shared contract will not reach this copy automatically — that is a gap in the fleet tooling, recorded rather than hidden. Do not "fix" this file back to the boilerplate.

## Repository context — read before reviewing

- **Zero RUNTIME dependencies is a rule, not a default.** `[project].dependencies` in `pyproject.toml` is `[]` and says why: this tool has to work when other things do not. A PR that adds an entry *there* is a finding on those grounds alone, whatever the package's merits. This does not extend to `[dependency-groups].dev`, which legitimately holds pytest and pytest-cov; a new dev-only tool is an ordinary judgement call. The hand-written header parser in `incidents.py` exists for the same reason as the empty runtime list — do not suggest PyYAML or any other parser library.
- **No verdict is computed in JavaScript.** Uptime thresholds, the projection rule, and anything deciding what "up" means are settled in Python so that `state_for_uptime` in `rollup.py` never grows a JS twin that drifts from it. New logic of *that* kind in `functions/now.js` or in the script `site.py` emits is a finding. This is narrower than "no comparisons": the emitted script deliberately performs one, the freshness gate at `site.py:179` (`Date.now() - Date.parse(iso) < gate`), because only the browser knows what time it is now. Staleness is the client's job; state is not.
- **`site.py` emits both HTML and the client script, so escaping is the live hazard.** `_esc` wraps `html.escape(..., quote=True)`, and the emitted script assigns to `innerHTML` with values taken from the KV payload. New interpolation into the page that bypasses `_esc`, or reaches for `innerHTML` where `textContent` would do, is the highest-value finding available in this repo.
- **The page must render without JavaScript, and must never show the reader a second failure.** This is the property the repo exists for, so protect it: a change that makes the page depend on the fetch succeeding, or that renders an error banner when `/now` is unavailable, is a finding. Note it is a goal the code does not yet fully reach — the mutation loop at `site.py:194` can throw part-way through on a malformed component entry and leave a partially updated DOM, which the silent catch hides. Flagging a change that *widens* that gap is in scope; re-reporting the gap itself is not.
- **Error codes follow `bakobo/dev/standards/error-codes.md`.** New failure paths in Python get a module-scope literal in `errors.py`, never an inline string, and the disposition has to match reality: `.f` where retrying cannot help, `.r` where it can. Most codes here are `.f` because this tool's failures are disagreements between what was typed and what is on disk, but not all — `METRICS_UNREACHABLE` at `errors.py:67` is `e.env.metrics.unavailable.r`, because Grafana being down is exactly the kind of thing that resolves by waiting. This bullet is about Python; `functions/now.js` cannot import these constants and signals through HTTP status instead.
- **Cross-repo references are intentional and are not broken links.** Paths like `../sibling/`, and opaque intent handles like `@52ehw3d7` or `@2oxu5757`, point at sibling repos on disk and at `bakobo/infra`'s intent tree. Do not flag them.
- **Long comments in `.github/workflows/` are the design record.** `deploy.yml`, `rollup.yml` and `now.yml` explain outages that actually happened. Do not suggest trimming them.
- **Outside pull requests are not merged** (`CONTRIBUTING.md`). Review advice framed as guidance for an external contributor is out of place here.

## Light mode

If the PR title or description contains `[light-ccr]`, perform **only** the Light-mode checks below. Skip everything else in this file. (`[no-ccr]` is handled at the workflow layer; if you are running, the PR is at least light.)

### Light-mode checks

1. **Secrets** — hardcoded API keys, passwords, tokens, private keys, connection strings with credentials.
2. **PII/PHI in new log statements** — emails, phone numbers, names, government IDs, addresses, card-shaped strings interpolated directly or via `toString()`.
3. **Obvious security mistakes** — SQL built by string concat with user input, `eval`/`exec` of user input, disabled TLS verification, auth checks removed.
4. **Broken syntax** — missing braces, malformed imports, references to undefined symbols visible in the diff.

If none apply, output `Light review found nothing.` and stop.

## Full review — what to check

The Light-mode checks above apply in full review too. Plus:

- **Input validation on new external entry points** — new CLI arguments, incident-file fields, metrics responses, or Pages Function requests without visible validation of size, type, or format. Data arriving from Grafana and from KV is external input.
- **HTML escaping at every new interpolation**, per the repository context above.
- **Error quality.** Full standard: `bakobo/dev/standards/error-handling.md`; the code grammar is `bakobo/dev/standards/error-codes.md`. In brief, every failure must:
  1. Carry a **stable distinguishing code** — two occurrences of the same error must be recognizable as the same. `"Something went wrong"` with no code is unacceptable.
  2. Have a **user-friendly message** — complete sentence, plain language, no all-caps, no exclamation, no raw stack trace or exception class name.
  3. Indicate **whether retry might succeed**, explicitly or through a correctly chosen status code. `functions/now.js` is the only HTTP surface here; 503 vs 400 and 429 vs 422 apply to it and to nothing else in the repo.
- **Unmarked tech debt.** This repo uses the `tick` ledger: a debt marker is `~XXXX` in a comment, referring to a tick. A bare TODO/FIXME/HACK with no tick is a finding. Do not ask for `TICKET-NNN`; that is another repo's convention.
- **Commented-out code.** Disabled blocks left in place. VCS history is the archive; delete.
- **Code health in regions the diff touches** — duplication near the change, misleading names, methods doing too much, magic numbers, dead code.
- **New dependencies** in `pyproject.toml`. See the repository context: the answer is no, and the finding is that the PR adds one.

## What NOT to review

- Design, architecture, or tradeoff correctness
- PR description, release notes, rollback plan completeness
- Test pass/fail or coverage thresholds — CI enforces 100% branch coverage and arguing about it here is noise
- Formatting, imports, line length (linters/formatters)
- Prose tone or grammar in markdown beyond broken links and bad code-fence languages
- UI visual design, copy, aesthetics — the wording of the page and its banner is argued out in `README.md` and is settled
- **Hardcoded user-visible strings.** There is no localization layer and there is not going to be one; every string in `site.py` is a deliberate English literal. Do not flag them.
- Performance unless the diff has an obvious quadratic loop, N+1 query, or sync call in a hot path

If the PR is purely cosmetic, say so in one sentence and stop.

## Code health bias

Comment on smells *in regions the diff touches*. Do not propose refactors of untouched code. Frame as opportunities, not blockers, unless correctness or security. Prefer fewer high-signal comments. Mark speculative findings as such.

## Output

- One comment per finding, on the relevant file and line.
- No "looks good" filler. No summary that restates the diff.
