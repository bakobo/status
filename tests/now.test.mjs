// The Pages Function's tests.
//
// It sits beside a Python suite held at 100% branch coverage, and until now it had none at all --
// which is how it shipped answering `200 text/html` to a HEAD, a failure invisible to every check
// that had been run against it because browsers GET. The rule the Python side states is that a
// threshold nobody enforces is a preference; the same argument applies to a file being tested at
// all, and this file is production code that runs on every page load.
//
// No test framework: `node --test` is built in, `Response` and `Request` are globals, and a KV
// binding is an object with a `get`. Adding a runner and a mocking library here would be more
// dependency than the thing under test.
//
// Deliberately NOT inside functions/ -- the deploy copies functions/*.js into the published site,
// and a test file has no business being served from status.bakobo.com.

import { strict as assert } from "node:assert";
import test from "node:test";

import { onRequest } from "../functions/now.js";

const PAYLOAD = '{"taken_at":"2026-09-10T21:29:44+00:00","banner":{"state":"operational"}}';

const env = (value) => ({ STATUS: { get: async () => value } });
const call = (method, value) =>
  onRequest({ request: new Request("https://status.bakobo.com/now", { method }), env: env(value) });

test("GET returns the stored payload as JSON", async () => {
  const res = await call("GET", PAYLOAD);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-type"), "application/json; charset=utf-8");
  assert.equal(await res.text(), PAYLOAD);
});

test("GET is cacheable for less than the write interval", async () => {
  // Half of fifteen minutes, so a cached copy can never be old enough to trip the page's own
  // staleness gate. A max-age above the gate would let a reader be shown grey by their own cache.
  const res = await call("GET", PAYLOAD);
  assert.equal(res.headers.get("cache-control"), "public, max-age=450, must-revalidate");
});

test("HEAD answers with the same headers and no body", async () => {
  // The bug this file was written for. `onRequestGet` routed GET alone, so HEAD fell past the
  // Function to the static asset handler and came back 200 text/html -- an endpoint telling an
  // uptime monitor that a page of HTML was the status payload, and that all was well.
  const res = await call("HEAD", PAYLOAD);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-type"), "application/json; charset=utf-8");
  assert.equal(await res.text(), "");
});

test("an unwritten key is 503 rather than 404 or an empty 200", async () => {
  // The page treats any non-OK response as "no reading" and leaves its built-in state alone, so
  // this must not be a 200 with nothing in it -- that parses as JSON and would look like an
  // answer. 503 is also the honest status: the snapshot is coming, it just is not here.
  const res = await call("GET", null);
  assert.equal(res.status, 503);
  assert.deepEqual(await res.json(), { error: "no snapshot" });
  assert.equal(res.headers.get("cache-control"), "no-store");
});

test("any other method is refused rather than falling through to the site", async () => {
  const res = await call("POST", PAYLOAD);
  assert.equal(res.status, 405);
  assert.equal(res.headers.get("allow"), "GET, HEAD");
});

test("every response is readable cross-origin", async () => {
  // The page's own fetch is same-origin and needs none of this. It is set because the snapshot is
  // public information, and somebody else's dashboard finding out it is allowed by way of a
  // console error is a poor way to learn it.
  for (const [method, value] of [["GET", PAYLOAD], ["HEAD", PAYLOAD], ["GET", null], ["POST", PAYLOAD]]) {
    const res = await call(method, value);
    assert.equal(res.headers.get("access-control-allow-origin"), "*", `${method} ${value}`);
  }
});
