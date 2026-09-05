# bakobo/status

The public status page for everything Bakobo runs, and the tool that writes what it says.

An incident is a **file in this repository**. `bakobo-status` appends to it, the site is generated from those files, and the generated site is published somewhere that does not share a failure domain with what it reports on. The reasoning lives in `bakobo/infra`'s intent tree — [`@xxbjzkw5`](https://github.com/bakobo/infra) for the page, `@52ehw3d7` for the file format, `@2oxu5757` for the uptime history — and is not restated here.

The short version of why it is not Statuspage: an incident history in a vendor's database is outside the repository and cannot be rebuilt from it, and a playbook can call a command where it cannot click a console.

## The tool

```
bakobo-status open --title "DE witness unreachable" --severity major \
                   --components witness-de --message "No OOBI response from any probe."

bakobo-status update 2026-09-04-de-witness-unreachable --state identified \
                   --message "The host's disk is full; the LMDB could not grow."

bakobo-status resolve 2026-09-04-de-witness-unreachable \
                   --message "Rotated the logs and restarted. Receipting resumed at 19:05Z."

bakobo-status list --open
bakobo-status show 2026-09-04-de-witness-unreachable
```

Four properties are deliberate and worth not undoing.

**It has no dependencies and talks to no network.** It writes a file. A status tool must work when the things it reports on do not, and every dependency is a way for that to stop being true. The incident header is hand-parsed rather than YAML for the same reason — and because a YAML header would admit eight ways to write the same thing, so two tools would eventually disagree about a file that looked fine.

**It does not commit.** It writes and prints the path; whether that becomes a commit, a pull request or something somebody reads first is the caller's decision. A mistyped severity should be an edit, not a revert of something already published.

**A resolved incident cannot be reopened.** The resolution was published to everyone watching, and amending it rewrites something they have already read. Open a new incident that references the old one; both statements then stand.

**An unknown component is refused.** `components.json` is generated from the monitoring root's `components` output, so every id here has a probe behind it. A typo would otherwise put a row on the public page that can never go green, sitting next to rows that mean something.

## The uptime history

```
bakobo-status rollup --date 2026-09-05 --backfill
```

Two numbers per component per day, because they answer different questions. **Uptime** is interval-based — the fraction of time points in which *at least one* probe succeeded, which is what the coloured bar shows and why three probe locations were chosen: one probe's own network trouble must not be published as an outage. **Reachability** is execution-based, the fraction of all probe executions that succeeded. Uptime 1.0 with reachability 0.67 means the service was up throughout and one region could not reach it — real, worth knowing, and invisible in the bar.

Both definitions are Grafana's own, so the page agrees with the Synthetics UI a reader may have open beside it.

**This is the piece with a deadline.** Grafana Cloud's free tier retains metrics for 14 days, so a day not captured before its fourteenth birthday is gone — not slowly, not with a bigger query, not at all. That is why the nightly job passes `--backfill`: it asks the history which days inside the window are still missing and does those too, so a week of failed runs is repaired by the first successful run rather than leaving a permanent hole.

It also means the record's shape is a one-way door. `DayRecord` carries uptime, reachability, intervals, executions and failures — enough for a 90-day bar and its hover. Latency percentiles and per-probe detail are *not* in it and cannot be added retroactively. If they are ever wanted, that has to be decided inside the fortnight.

## The incident format

```
---
id: 2026-09-04-de-witness-unreachable
title: DE witness unreachable
severity: major
components: witness-de
---

## 2026-09-04T18:22:11Z investigating

No OOBI response from any probe.

## 2026-09-04T19:05:00Z resolved

Rotated the logs and restarted. Receipting resumed.
```

A fixed `key: value` header, then updates as markdown sections. Times are UTC at second resolution, so an update can be compared against a probe series without anyone reasoning about an offset at an hour when they are not sharp. States are `investigating`, `identified`, `monitoring`, `resolved` — the vocabulary a reader already knows from other status pages, adopted rather than invented, because a stranger should not have to learn ours during our outage.

The format is meant to survive being typed by hand when the tooling is what is broken. That is asserted by test, not assumed: the round trip is checked in both directions, and a hand-added update parses.

## Running the tests

```
uv sync
uv run pytest
```

100% branch coverage is enforced by `fail_under = 100`, per `bakobo/dev`'s standard. A threshold that is not enforced is a preference.

## What is not built yet

Stated plainly so nobody reads this README as a description of a working service.

- **The site generator.** Incidents and history parse; nothing renders them to HTML yet.
- **Publication.** The target is Cloudflare Pages, from GitHub Actions, on a subdomain so that `bakobo.com`'s zone stays at Namecheap.
- **A dead-man's switch on the rollup.** `ops.md` §6 wants backup freshness monitored by silence, and this job has the same shape: a rollup that stops running looks exactly like a quiet month until someone opens the page in three weeks and finds a hole. healthchecks.io, once its ping URLs exist.
- **A check that `components.json` is still what the monitoring root outputs.** It is committed so the tool works without a tofu toolchain, and a committed copy of generated data drifts. Until that check exists, the drift is caught by nobody.
- **A `workflow_dispatch` workflow** wrapping the incident commands, so an incident can be posted from a phone.
