"""Render the public page from the files that are the record.

One self-contained HTML document, no external stylesheet, no script, no font fetch. A status page
is read at the moment other things are broken, so every additional request is a way for it to be
broken too. That constraint is also why the whole thing is a string in this file rather than a
template engine: a dependency here would have to be installed before the page could be rebuilt
during an incident.

**The page is a snapshot, not a live view**, and it says so rather than implying otherwise. It is
rebuilt nightly by the rollup and on every push, so posting an incident republishes it within a
minute or two — which is when freshness actually matters. What it must never do is display a
confident green banner sourced from data that is a day old.

Colour never carries meaning alone. Every component shows its state as text beside the swatch,
every bar carries a tooltip with its date and numbers, and the day table under each component is
the accessible view of the same data. That is the mitigation the house data-viz standard requires
for the amber status step, which sits below 3:1 on any light surface and cannot be re-stepped
because the status palette is fixed.

The bar strip sits on the page plane rather than on the card surface, and that was computed rather
than chosen: against Bakobo's espresso dark surface the critical red measures 2.64:1, and a red bar
nobody can see is the worst failure this page has. On pitch black all three states clear 3:1.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

from .incidents import Store
from .rollup import DEFAULT_STEP_SECONDS, state_for_uptime
from .snapshot import INTERVALS_PER_DAY, STALE_AFTER_INTERVALS, Now

# Fixed status steps from the house data-viz standard. Never themed, never reused for anything
# else, and deliberately not drawn from Bakobo's brand ramp -- a brand hue standing in for
# "critical" is a hue that means something different on the next page.
STATE_COLOURS = {
    "operational": "#0ca30c",
    "degraded": "#fab219",
    "down": "#d03b3b",
    # Absence, not a status, and THEMED where the three above are fixed -- because it is not a
    # status colour and has no fixed step to honour. It is a near-surface tint rather than the
    # mid-grey it started as: rendering the first build showed 89 empty days as solid grey bars
    # that read as 89 days of something bad, with the single measured day nearly invisible
    # beside them. Absence has to look like absence, or a new page reports its own youth as an
    # outage -- the same mistake the backfill made in the data, made again in the pixels.
    "no-data": "var(--empty)",
}

STATE_LABELS = {
    "operational": "Operational",
    "degraded": "Degraded",
    "down": "Down",
    "no-data": "No data",
}

# A glyph beside every state, so the distinction survives a monochrome print, forced-colours mode,
# and the reader who cannot separate amber from green.
STATE_GLYPHS = {"operational": "●", "degraded": "▲", "down": "■", "no-data": "·"}

SEVERITY_STATE = {
    "maintenance": "degraded",
    "minor": "degraded",
    "major": "down",
    "critical": "down",
}

WINDOW_DAYS = 90

# Beside the history rather than inside it, because it is not one. `history/` holds days that can
# never be recomputed; this file is replaced every quarter hour and losing it costs nothing.
DEFAULT_NOW = Path("now.json")

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --card: #efe9e4; --plane: #ffffff;
  --text: #44403b; --muted: #8c8079; --border: #dec1b1; --accent: #ef8b57;
  --empty: #ece7e3;
  --font: 'Noto Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #000000; --card: #4a2b1b; --plane: #000000;
    --text: #efe9e4; --muted: #8c8079; --border: #705a4d; --accent: #ef8b57;
    --empty: #241a14;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1rem 4rem; background: var(--bg); color: var(--text);
  font-family: var(--font); line-height: 1.5; -webkit-text-size-adjust: 100%;
}
main { max-width: 46rem; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2.5rem 0 .75rem; font-weight: 600; }
a { color: var(--accent); }
.sub { color: var(--muted); font-size: .85rem; margin: 0 0 2rem; }
.banner {
  display: flex; gap: .6rem; align-items: baseline;
  border: 1px solid var(--border); border-left-width: 4px;
  border-radius: 6px; padding: .9rem 1rem; margin-bottom: 2rem; background: var(--card);
}
.banner strong { font-size: 1.05rem; }
.component { border-top: 1px solid var(--border); padding: .9rem 0; }
.component:last-of-type { border-bottom: 1px solid var(--border); }
.row { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; }
.name { font-weight: 600; }
.state { font-size: .8rem; color: var(--muted); white-space: nowrap; }
/* The strip sits on the page plane, not the card: the critical step measures 2.64:1 on the
   espresso dark surface and clears 3:1 on pitch. */
.strip {
  display: flex; gap: 2px; margin-top: .6rem; background: var(--plane);
  padding: 2px 0; border-radius: 3px;
}
.day {
  flex: 1 1 0; min-width: 2px; height: 26px; border-radius: 2px; position: relative;
}
.day:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
/* Today is still moving, and is marked so it does not read as a completed, scored day. Two
   channels rather than one: a slight taper plus a shortened height, so the distinction survives
   for a reader who cannot separate this cell's colour from its neighbour's. */
.day.today { height: 30px; margin-top: -2px; border-radius: 2px 2px 5px 5px; }
.day .tip {
  display: none; position: absolute; bottom: calc(100% + 6px); left: 50%;
  transform: translateX(-50%); background: var(--card); color: var(--text);
  border: 1px solid var(--border); border-radius: 5px; padding: .45rem .6rem;
  font-size: .75rem; line-height: 1.35; white-space: nowrap; z-index: 2;
  box-shadow: 0 2px 8px rgb(0 0 0 / .18);
}
.day:hover .tip, .day:focus .tip, .day:focus-within .tip { display: block; }
.scale { display: flex; justify-content: space-between; color: var(--muted);
         font-size: .7rem; margin-top: .3rem; }
.legend { display: flex; flex-wrap: wrap; gap: 1rem; margin: 1.25rem 0 0;
          color: var(--muted); font-size: .78rem; }
.legend span { display: inline-flex; align-items: center; gap: .35rem; }
.sw { width: .7rem; height: .7rem; border-radius: 2px; display: inline-block; }
/* The empty tint is close to the surface by design, so it needs an outline to be visible as a
   legend key and as a bar. Without it, "no data" is indistinguishable from "no bar". */
.sw[style*="--empty"], .day[style*="--empty"] { outline: 1px solid var(--border);
   outline-offset: -1px; }
details { margin-top: .6rem; }
summary { cursor: pointer; color: var(--muted); font-size: .78rem; }
table { border-collapse: collapse; margin-top: .5rem; font-size: .78rem; width: 100%; }
th, td { text-align: left; padding: .2rem .5rem .2rem 0; border-bottom: 1px solid var(--border); }
.incident { border: 1px solid var(--border); border-radius: 6px; padding: 1rem;
            margin-bottom: 1rem; background: var(--card); }
.incident h3 { margin: 0 0 .2rem; font-size: 1rem; }
.meta { color: var(--muted); font-size: .78rem; margin-bottom: .75rem; }
.update { border-left: 2px solid var(--border); padding: 0 0 0 .8rem; margin: .75rem 0; }
.update .when { color: var(--muted); font-size: .75rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .78rem;
         border-top: 1px solid var(--border); padding-top: 1rem; }
"""

# The page's only script. It fetches the snapshot from this same origin and swaps in verdicts that
# were already decided in Python (see `publish`), so nothing here knows what "up" means.
#
# It is an UPGRADE, never a requirement. The document that arrives is already correct as of its
# build and renders fully without this running: a reader with JavaScript off, a reader whose fetch
# is blocked, and a reader on a page served while the endpoint is down all see the built-in state
# and its honest timestamp. Every failure path below leaves the document exactly as it arrived,
# which is why there is no error handling that writes anything to the page.
#
# The freshness gate runs HERE rather than at write time, and it is the only arithmetic in the
# file. A payload written at 09:00 and read at 14:00 still says "operational"; only the reader's
# clock can catch that. A clock that is badly wrong fails toward grey rather than toward a
# confident green, which is the direction to fail in.
SCRIPT = """
(async function () {
  try {
    var res = await fetch('/now', { cache: 'no-store' });
    if (!res.ok) return;
    var data = await res.json();
    var gate = (data.stale_after_seconds || 2700) * 1000;

    function fresh(iso) { return iso && (Date.now() - Date.parse(iso)) < gate; }

    var anyFresh = Object.keys(data.components || {}).some(function (id) {
      return fresh(data.components[id].as_of);
    });
    if (!anyFresh) return;   // nothing newer than the build; leave the document alone

    var b = data.banner;
    if (b) {
      document.getElementById('banner').style.borderLeftColor = b.colour;
      document.getElementById('banner-swatch').style.background = b.colour;
      document.getElementById('banner-headline').textContent = b.headline;
      document.getElementById('banner-evidence').textContent = b.evidence;
    }

    Object.keys(data.components).forEach(function (id) {
      var c = data.components[id];
      // Matched by name, so a component added or removed since the build is skipped rather than
      // shifting every cell after it and painting the wrong service red.
      var cell = document.querySelector('[data-now="' + CSS.escape(id) + '"]');
      var row = document.querySelector('[data-now-state="' + CSS.escape(id) + '"]');
      if (!cell || !row) return;
      var stale = !fresh(c.as_of);
      cell.style.background = stale ? 'var(--empty)' : c.colour;
      var tip = cell.querySelector('.tip');
      if (tip && !stale) tip.innerHTML = tip.innerHTML.split('<br>')[0] + '<br>' + c.tip;
      row.innerHTML = '<span class="sw" style="background:' +
        (stale ? 'var(--empty)' : c.colour) + '"></span> ' +
        (stale ? '\\u00b7 No data' : c.glyph + ' ' + c.label);
    });
  } catch (e) {
    // Deliberately silent and deliberately empty. The page is already correct; a reader looking at
    // a status page during an outage is the last person who should be shown a second failure.
  }
})();
"""


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _swatch(state: str) -> str:
    return f'<span class="sw" style="background:{STATE_COLOURS[state]}"></span>'


def load_history(root: Path, component: str) -> dict:
    path = Path(root) / f"{component}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("days", {})


def window(days: dict, through: dt.date, span: int = WINDOW_DAYS) -> list[tuple[dt.date, dict]]:
    """The last `span` days, oldest first, with absent days present as None.

    Absent days are kept rather than skipped so the strip's geometry is time, not a list of
    records. A strip that closed up its gaps would silently compress a week of missing data into
    nothing and read as continuous coverage.
    """
    start = through - dt.timedelta(days=span - 1)
    return [
        (start + dt.timedelta(days=n), days.get((start + dt.timedelta(days=n)).isoformat()))
        for n in range(span)
    ]


def day_state(record) -> str:
    if not record or not record.get("intervals"):
        return "no-data"
    return state_for_uptime(record["uptime"])


def today_state(snap, now) -> str:
    """The newest cell: today, projected forward from what is happening right now.

    Three outcomes rather than two. Absent means the harvest has never run or nothing has been
    measured since midnight -- the first quarter hour of a UTC day, legitimately. Stale means the
    harvest stopped: the last thing it saw may be hours old, and projecting from it would paint
    today green on the strength of a measurement from another hour. Both render as absence, which
    is the honest colour for "we do not currently know", and both say which one they are in the
    tooltip.

    Grey rather than red for a stopped harvest, deliberately. The common causes -- an Actions
    outage, an expired token, a renamed metric -- have nothing to do with whether the estate is up,
    so red would report a monitoring failure as an outage. That the harvest stopped is worth an
    alarm to the operator, and it gets one from the job's dead-man's switch rather than from the
    colour of a bar a stranger is reading.
    """
    if snap is None or snap.is_stale(now):
        return "no-data"
    return state_for_uptime(snap.projected_uptime())


def _bar(day: dt.date, record) -> str:
    state = day_state(record)
    if record and record.get("intervals"):
        detail = (
            f"{STATE_LABELS[state]} — uptime {record['uptime'] * 100:.2f}%, "
            f"reachability {record['reachability'] * 100:.2f}%<br>"
            f"{record['failures']} of {record['executions']} probe runs failed"
        )
    else:
        # Said explicitly. A grey bar with no explanation is read as an outage by anyone who has
        # not been told otherwise, which is the whole class of mistake this page must avoid.
        detail = "No data — nothing was being measured on this day."
    # tabindex so the tooltip is reachable without a pointer; the strip is the only interactive
    # thing on the page and it should not require a mouse.
    return (
        f'<div class="day" tabindex="0" style="background:{STATE_COLOURS[state]}">'
        f'<span class="tip"><strong>{_esc(day.isoformat())}</strong><br>{detail}</span></div>'
    )


def _clock(iso: str) -> str:
    return dt.datetime.fromisoformat(iso).strftime("%H:%M UTC")


def today_detail(snap, now) -> str:
    """The tooltip for today's cell, as a fragment of HTML.

    Split out from the cell because the browser needs the same sentence when it refreshes the page
    from the snapshot endpoint. Written once here rather than twice -- once in Python and once in
    JavaScript -- for the same reason the thresholds live in one place: two copies of a sentence
    about an outage will eventually describe two different outages.
    """
    if snap is None:
        return "Nothing has been measured. No harvest has run against this component yet."
    if snap.is_stale(now):
        return (
            f"Measurement stopped at {_esc(_clock(snap.as_of))}, and nothing has arrived since.<br>"
            "This is not a report that anything is down — it is the monitoring that went quiet."
        )
    failed = snap.intervals - snap.up
    verdict = "up right now" if snap.current else "DOWN right now"
    return (
        f"Today so far — {verdict}, as of {_esc(_clock(snap.as_of))}<br>"
        f"{snap.intervals} of {INTERVALS_PER_DAY} intervals measured, {failed} failed<br>"
        f"Projects to {snap.projected_uptime() * 100:.2f}% if it stays this way"
    )


def _today_bar(component_id: str, day: dt.date, snap, now) -> str:
    """The newest cell, which is today and is the only one still moving.

    Marked with a class so it is visibly the day in progress rather than a completed one. A cell
    that looked identical to its ninety neighbours would be claiming today is finished and scored,
    when what it actually carries is a projection that will move again before midnight.

    `data-now` is the hook the page's own script updates in place from /now. It carries the
    component id rather than an index, so a component added or removed between the build and the
    fetch mismatches by name and is skipped, instead of silently shifting every cell after it by
    one and painting the wrong service red.
    """
    state = today_state(snap, now)
    return (
        f'<div class="day today" tabindex="0" data-now="{_esc(component_id)}" '
        f'style="background:{STATE_COLOURS[state]}">'
        f'<span class="tip"><strong>{_esc(day.isoformat())} — today</strong><br>'
        f"{today_detail(snap, now)}</span></div>"
    )


def _table(rows) -> str:
    measured = [(d, r) for d, r in rows if r and r.get("intervals")]
    if not measured:
        return "<p class='state'>No days recorded yet.</p>"
    body = "".join(
        f"<tr><td>{_esc(d.isoformat())}</td><td>{r['uptime'] * 100:.2f}%</td>"
        f"<td>{r['reachability'] * 100:.2f}%</td><td>{r['failures']}/{r['executions']}</td></tr>"
        for d, r in reversed(measured)
    )
    return (
        "<table><thead><tr><th>Day</th><th>Uptime</th><th>Reachability</th>"
        f"<th>Failed runs</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _component_block(component_id: str, display: str, rows, snap, now) -> str:
    # The component's headline state is today's, not the newest completed day's. Reading it off the
    # history was defensible while the history was all there was; with a snapshot in hand, saying
    # "Operational" from yesterday's record while today is measurably down would be the page
    # telling a stranger something untrue with the evidence to know better sitting beside it.
    #
    # And when today is unknown it says "No data" rather than falling back to the newest completed
    # day. The fallback is the tempting version and it is the stale-confident-green failure with
    # extra steps: a harvest that stopped on Tuesday would keep the row green through Friday.
    state = today_state(snap, now)
    measured = [r for _, r in rows if r and r.get("intervals")]
    average = sum(r["uptime"] for r in measured) / len(measured) if measured else None
    counted = f"{len(measured)} day" + ("" if len(measured) == 1 else "s")
    summary = f"{average * 100:.2f}% over {counted}" if measured else "not yet measured"

    return f"""<section class="component">
  <div class="row">
    <span class="name">{_esc(display)}</span>
    <span class="state" data-now-state="{_esc(component_id)}">{_swatch(state)} {STATE_GLYPHS[state]}
      {STATE_LABELS[state]}</span>
  </div>
  <div class="strip">{''.join(_bar(d, r) for d, r in rows[:-1])}
    {_today_bar(component_id, rows[-1][0], snap, now)}</div>
  <div class="scale"><span>{WINDOW_DAYS} days ago</span><span>{_esc(summary)}</span>
    <span>today</span></div>
  <details><summary>Show the numbers for {_esc(display)}</summary>{_table(rows)}</details>
</section>"""


def _incident_block(incident) -> str:
    updates = "".join(
        f'<div class="update"><div class="when">{_esc(u.at.strftime("%Y-%m-%d %H:%M UTC"))}'
        f" — {_esc(u.state)}</div><div>{_esc(u.message)}</div></div>"
        for u in incident.updates
    )
    closed = "" if not incident.is_resolved else f" · resolved after {incident.duration}"
    return f"""<article class="incident">
  <h3>{_esc(incident.title)}</h3>
  <div class="meta">{_esc(incident.severity)} · {_esc(incident.state)}{_esc(closed)}
    · affects {_esc(', '.join(incident.components))}</div>
  {updates}
</article>"""


def banner(open_incidents, snaps, now, total) -> tuple[str, str, str]:
    """The headline: state, claim, and the evidence the claim rests on.

    THIS is the answer to "are you up", and the strip below is not. A reader arriving mid-outage
    wants one sentence, and the bars can only ever give them a day-shaped one -- an outage starting
    at 23:50 barely moves today's projection, because there is not enough day left for it to.

    It used to say "All systems operational" from the absence of an incident file, which was an
    inference from silence rather than a measurement: nothing alerts, incidents are declared by
    hand, and an outage at three in the morning produces silence identical to a healthy night. So
    the sentence was worth very little, and a reader who did not trust it looked down at the strip
    for corroboration and found the newest cell grey. That is the whole reason this work happened.

    Order of authority, and each layer overrides the one below it:

    An open incident wins outright. It is a statement a person made on purpose, it carries a cause
    and a remedy, and no probe result is worth more than that.

    Otherwise the measurement speaks, and it is allowed to say the estate is down. This is the part
    that is new.

    Otherwise -- no incident and no usable measurement -- it says so, rather than falling back to
    "All systems operational". That fallback is what the old version did every minute of its life,
    and it is a green light generated by the absence of information.
    """
    if open_incidents:
        worst = max(open_incidents, key=lambda i: list(SEVERITY_STATE).index(i.severity))
        plural = "incident" if len(open_incidents) == 1 else "incidents"
        return (
            SEVERITY_STATE[worst.severity],
            f"{len(open_incidents)} open {plural}",
            "Declared by hand — the detail is below.",
        )

    live = {c: s for c, s in snaps.items() if not s.is_stale(now)}
    if not live:
        # Named for what it is. "Status unknown" is a worse headline than "operational" to write
        # and a better one to read, and the reader is told which of the two possible worlds they
        # are in: nobody has measured, versus the measuring stopped.
        seen = max((s.as_of for s in snaps.values()), default=None)
        since = f"Nothing measured since {_clock(seen)}." if seen else "No measurement has run yet."
        return "no-data", "Current status unknown", f"{since} This is not a report of an outage."

    down = sorted(c for c, s in live.items() if not s.current)
    at = _clock(max(s.as_of for s in live.values()))
    if down:
        return "down", f"{len(down)} of {total} not responding", f"{', '.join(down)}, as of {at}."
    if len(live) < total:
        return "operational", f"{len(live)} of {total} responding", (
            f"As of {at}. The rest have no current measurement."
        )
    return "operational", "All systems operational", f"All {total} probed and responding as of {at}."


def publish(components: dict, snaps: dict, open_incidents, now) -> dict:
    """The payload served at /now and consumed by the page's own script.

    Every verdict in here is already decided. The browser sets a colour and swaps a sentence; it
    owns no threshold, no projection and no notion of what "up" means. That is the whole point of
    precomputing: the moment the page fetches its data at runtime, rendering logic wants to migrate
    into JavaScript, and `state_for_uptime` acquires a twin that drifts. Deciding here keeps one
    definition, in the language the strip and the banner are already rendered from.

    The ONE thing the browser must decide for itself is freshness, because only the browser knows
    what time it is when the page is being read. A payload written at 09:00 and read at 14:00 says
    "operational" and is worthless; the reader's clock is the only thing that can catch that. So
    the gate travels with the payload as a number of seconds rather than as a rule, and the
    arithmetic on the other side is a subtraction.
    """
    state, headline, evidence = banner(open_incidents, snaps, now, len(components))
    return {
        "taken_at": now.isoformat(),
        "stale_after_seconds": STALE_AFTER_INTERVALS * DEFAULT_STEP_SECONDS,
        "banner": {
            "state": state,
            "colour": STATE_COLOURS[state],
            "headline": headline,
            "evidence": evidence,
        },
        "components": {
            cid: {
                "state": (cstate := today_state(snaps.get(cid), now)),
                "colour": STATE_COLOURS[cstate],
                "glyph": STATE_GLYPHS[cstate],
                "label": STATE_LABELS[cstate],
                "tip": today_detail(snaps.get(cid), now),
                # The raw reading travels too. It costs a few bytes and it is what anyone else
                # consuming this -- a customer's dashboard, a future sparkline -- would actually
                # want, rather than our rendering of it.
                "as_of": snaps[cid].as_of if cid in snaps else None,
                "current": snaps[cid].current if cid in snaps else None,
                "intervals": snaps[cid].intervals if cid in snaps else 0,
                "up": snaps[cid].up if cid in snaps else 0,
            }
            for cid in sorted(components)
        },
    }


def render(components: dict, history_root: Path, incidents_root: Path, built_at, now_path=None) -> str:
    store = Store(incidents_root)
    all_incidents = store.all()
    open_incidents = [i for i in all_incidents if not i.is_resolved]
    recent_closed = [i for i in all_incidents if i.is_resolved][:10]

    # Narrowed to components the page actually shows. A snapshot holding a component that has since
    # been removed from the registry must not be able to put "1 of 7 not responding" on the banner
    # for a row nobody can see.
    snaps = {c: s for c, s in Now(now_path or DEFAULT_NOW).snapshots().items() if c in components}
    state, headline, evidence = banner(open_incidents, snaps, built_at, len(components))
    today = built_at.date()

    blocks = "".join(
        _component_block(
            cid,
            meta.get("display", cid),
            window(load_history(history_root, cid), today),
            snaps.get(cid),
            built_at,
        )
        for cid, meta in sorted(components.items(), key=lambda kv: (kv[1].get("kind", ""), kv[0]))
    )
    legend = "".join(
        f"<span>{_swatch(s)} {STATE_GLYPHS[s]} {STATE_LABELS[s]}</span>" for s in STATE_COLOURS
    )

    incidents_html = "".join(_incident_block(i) for i in open_incidents) or (
        "<p class='state'>No open incidents.</p>"
    )
    past_html = "".join(_incident_block(i) for i in recent_closed) or (
        "<p class='state'>Nothing has been reported yet.</p>"
    )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bakobo status</title>
<meta name="description" content="Operational status of the services Bakobo runs.">
<style>{STYLE}</style>
</head><body><main>
<h1>Bakobo status</h1>
<p class="sub">Whether the things Bakobo runs are working.</p>

<div class="banner" id="banner" style="border-left-color:{STATE_COLOURS[state]}">
  <span class="sw" id="banner-swatch" style="background:{STATE_COLOURS[state]}"></span>
  <strong id="banner-headline">{_esc(headline)}</strong>
  <span class="state" id="banner-evidence">{_esc(evidence)}</span>
</div>

<h2>Current incidents</h2>
{incidents_html}

<h2>Uptime, last {WINDOW_DAYS} days</h2>
{blocks}
<div class="legend">{legend}</div>

<h2>Recent incidents</h2>
{past_html}

<script>{SCRIPT}</script>
<footer>
<p>Each day is measured every 15 minutes from three probe locations. A day counts as up in an
interval when at least one probe succeeded, so one probe's own network trouble is not reported
here as an outage. Hover a bar for that day's figures, or open the numbers under any component.</p>
<p>The last bar is today, still in progress, and it is scored as if the rest of the day looks like
right now — so it goes red while an outage is happening and settles to the day's true figure once
it ends. It can never return to green after a failed interval. Late in a UTC day an outage moves it
very little, because there is not enough day left for it to matter; the banner above, not the bars,
is what answers whether the estate is up at this moment.</p>
<p><strong>This page is a snapshot, not a live view.</strong> It is rebuilt whenever an incident is
posted and once nightly, and it is published on infrastructure separate from everything it reports
on. Built {_esc(built_at.strftime('%Y-%m-%d %H:%M UTC'))}.</p>
<p>Found a security problem? Please report it to
<a href="mailto:security@bakobo.com">security@bakobo.com</a>
(<a href="https://bakobo.com/.well-known/security.txt">security.txt</a>).</p>
</footer>
</main></body></html>
"""
