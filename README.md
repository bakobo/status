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
bakobo-status now
```

Two numbers per component per day, because they answer different questions. **Uptime** is interval-based — the fraction of time points in which *at least one* probe succeeded, which is what the coloured bar shows and why three probe locations were chosen: one probe's own network trouble must not be published as an outage. **Reachability** is execution-based, the fraction of all probe executions that succeeded. Uptime 1.0 with reachability 0.67 means the service was up throughout and one region could not reach it — real, worth knowing, and invisible in the bar.

Both definitions are Grafana's own, so the page agrees with the Synthetics UI a reader may have open beside it.

**This is the piece with a deadline.** Grafana Cloud's free tier retains metrics for 14 days, so a day not captured before its fourteenth birthday is gone — not slowly, not with a bigger query, not at all. That is why the nightly job passes `--backfill`: it asks the history which days inside the window are still missing and does those too, so a week of failed runs is repaired by the first successful run rather than leaving a permanent hole.

## Now, which is a different claim from today

`rollup` captures completed days; `now` captures the moment. They share the uptime expression on purpose — a second definition of "up" would eventually disagree with the bars beside it — but nothing else, and the difference is worth keeping straight. A day record is permanent and unrecomputable. A snapshot is replaced every quarter hour, is never committed, and losing every one of them costs nothing. That is why it is `now.json` beside the tree rather than a file inside `history/`.

**Today is scored as if the rest of the day looks like right now.** Not as if the rest of the day succeeds, which is the obvious rule and is wrong in the only case that matters: with an outage active, optimistic completion needs five failed intervals — seventy-five minutes — before the cell turns red, while a reader watches a page showing amber during an outage they are currently experiencing. Assuming the present persists puts the cell where it belongs within one interval, and the two rules are identical whenever nothing is down, which is almost always.

Two consequences that look like bugs and are not. Today can never return to green once an interval has failed, though it does improve from red to amber when an outage ends — that is the day's true final figure arriving early. And the cell's ability to shout decays through the day: an outage at 00:15 projects to 0% and is violently red, while the same outage at 23:50 projects to 97.9% and is amber, because there is not enough day left for it to matter. **So the strip is not what answers "are you up".** The banner is, and it is sourced from the same snapshot for exactly this reason.

The banner used to say "All systems operational" whenever no incident file existed. Nothing alerts — `alert_sensitivity` is `none` in the monitoring root, deliberately — and incidents are declared by hand, so an outage at three in the morning produced silence identical to a healthy night. It now reports what was measured, is allowed to say the estate is down without waiting for a human to notice, and says "current status unknown" rather than green when it has nothing fresh to go on.

A snapshot older than three probe periods stops being evidence. Past that the page shows today grey and names the time measurement stopped, rather than projecting forward from another hour. Grey and not red: an Actions outage or an expired token has nothing to do with whether the estate is up, so red there would publish a monitoring failure as an outage. That the harvest stopped is worth an alarm to the operator, and it gets one from the deploy job's dead-man's switch instead.

## The uptime history, continued

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

- **Corroboration from the reader's own browser.** All seven probe targets send `Access-Control-Allow-Origin: *`, so a browser can run the same assertion the Grafana check runs — the witness's own AID out of `GET /oobi`, the `proves` regex for each site — and confirm within a second what the snapshot measured minutes ago. It has to be asymmetric: agreement upgrades the banner's claim, while disagreement says "your browser cannot reach this" rather than painting the estate red, because one reader on one network is one probe from one location. Blocked on the monitoring root emitting each surface's `url` and `proves` into `components.json`; without that the browser check would drift from the real check and the two would disagree in public.
- **Alerting.** `alert_sensitivity` is `none` and the banner now says so implicitly, by reporting only what it measured. An outage still reaches nobody's phone, and an incident is still declared only when a person notices — so a measured dip can sit on the page with no incident explaining it.
- **A check that `components.json` is still what the monitoring root outputs.** It is committed so the tool works without a tofu toolchain, and a committed copy of generated data drifts. Until that check exists, the drift is caught by nobody.
- **A `workflow_dispatch` workflow** wrapping the incident commands, so an incident can be posted from a phone.
