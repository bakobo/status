// GET /now — the current-status snapshot, served from KV on the page's own origin.
//
// This exists so the page stops being rebuilt to change data. Before it, saying anything about
// *now* meant baking a fresh snapshot into the HTML and republishing the whole site every fifteen
// minutes: ninety-six Cloudflare deployments a day to change a kilobyte, with a build system doing
// a database's job. The static page is now rebuilt when its *content* changes -- an incident, a
// rolled-up day, a code change -- and this endpoint carries everything that changes on a clock.
//
// Same origin as the page, deliberately, and that is the whole reason it is a Pages Function
// rather than a Worker. A Worker would need either a Custom Domain, which requires the zone to be
// on Cloudflare and bakobo.com's is not, or a *.workers.dev hostname, which some corporate filters
// block wholesale -- a bad property for a status page whose readers are strangers at work. Served
// from status.bakobo.com there is no second hostname, no CORS, and nothing extra for a proxy to
// refuse.
//
// It computes NOTHING. The writer stores a payload whose verdicts are already decided, so the
// uptime thresholds and the projection rule stay in Python where the strip and the banner get them
// (see state_for_uptime in rollup.py). A second implementation here would eventually disagree with
// the bars beside it about what "up" means, and the reader would have no way to tell which half
// was lying.

export async function onRequestGet({ env }) {
  const body = await env.STATUS.get("now");

  if (body === null) {
    // 503 rather than 404 or an empty 200. The page treats any non-OK response as "no reading",
    // which renders as the same honest grey it shows when a snapshot is stale -- and 503 is the
    // status that says "ask again shortly" to anything between us and the reader.
    return json({ error: "no snapshot" }, 503);
  }

  return new Response(body, {
    headers: {
      "content-type": "application/json; charset=utf-8",
      // Half the write interval, so a cached copy can never be old enough to trip the page's own
      // staleness gate. `must-revalidate` because a stale reading is worse than a slow one here:
      // the entire value of this endpoint is that its timestamp can be believed.
      "cache-control": "public, max-age=450, must-revalidate",
      // The page fetching this is same-origin, so CORS is not needed for the status page itself.
      // It is set anyway because the snapshot is public information and somebody -- a dashboard, a
      // customer's own monitoring -- will eventually want to read it from elsewhere, and finding
      // out by way of a console error is a poor way to learn a thing is allowed.
      "access-control-allow-origin": "*",
    },
  });
}

function json(payload, status) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": "*",
    },
  });
}
