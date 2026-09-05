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
    uptime = record["uptime"]
    if uptime >= 0.999:
        return "operational"
    return "degraded" if uptime >= 0.95 else "down"


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


def _component_block(component_id: str, display: str, rows) -> str:
    latest = next((r for _, r in reversed(rows) if r and r.get("intervals")), None)
    state = day_state(latest)
    measured = [r for _, r in rows if r and r.get("intervals")]
    average = sum(r["uptime"] for r in measured) / len(measured) if measured else None
    counted = f"{len(measured)} day" + ("" if len(measured) == 1 else "s")
    summary = f"{average * 100:.2f}% over {counted}" if measured else "not yet measured"

    return f"""<section class="component">
  <div class="row">
    <span class="name">{_esc(display)}</span>
    <span class="state">{_swatch(state)} {STATE_GLYPHS[state]} {STATE_LABELS[state]}</span>
  </div>
  <div class="strip">{''.join(_bar(d, r) for d, r in rows)}</div>
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


def banner(open_incidents) -> tuple[str, str]:
    """The headline, from open incidents only.

    Deliberately not from the uptime history. The history's newest day may be up to a day old, and
    a green banner sourced from stale data is the precise failure a status page exists to prevent.
    An open incident is a statement someone made on purpose and is current by construction.
    """
    if not open_incidents:
        return "operational", "All systems operational"
    worst = max(open_incidents, key=lambda i: list(SEVERITY_STATE).index(i.severity))
    state = SEVERITY_STATE[worst.severity]
    plural = "incident" if len(open_incidents) == 1 else "incidents"
    return state, f"{len(open_incidents)} open {plural}"


def render(components: dict, history_root: Path, incidents_root: Path, built_at) -> str:
    store = Store(incidents_root)
    all_incidents = store.all()
    open_incidents = [i for i in all_incidents if not i.is_resolved]
    recent_closed = [i for i in all_incidents if i.is_resolved][:10]

    state, headline = banner(open_incidents)
    today = built_at.date()

    blocks = "".join(
        _component_block(cid, meta.get("display", cid), window(load_history(history_root, cid), today))
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

<div class="banner" style="border-left-color:{STATE_COLOURS[state]}">
  {_swatch(state)} <strong>{_esc(headline)}</strong>
</div>

<h2>Current incidents</h2>
{incidents_html}

<h2>Uptime, last {WINDOW_DAYS} days</h2>
{blocks}
<div class="legend">{legend}</div>

<h2>Recent incidents</h2>
{past_html}

<footer>
<p>Each day is measured every 15 minutes from three probe locations. A day counts as up in an
interval when at least one probe succeeded, so one probe's own network trouble is not reported
here as an outage. Hover a bar for that day's figures, or open the numbers under any component.</p>
<p><strong>This page is a snapshot, not a live view.</strong> It is rebuilt whenever an incident is
posted and once nightly, and it is published on infrastructure separate from everything it reports
on. Built {_esc(built_at.strftime('%Y-%m-%d %H:%M UTC'))}.</p>
<p>Found a security problem? Please report it to
<a href="mailto:security@bakobo.com">security@bakobo.com</a>
(<a href="https://bakobo.com/.well-known/security.txt">security.txt</a>).</p>
</footer>
</main></body></html>
"""
