/**
 * NBA Career Map — data-proxy Worker.
 *
 * Purpose (be honest about scope): raise the cost of casually scraping the
 * site's JSON data files above "one curl command", not build a real lock.
 * Origin/Referer headers are trivially spoofable with `curl -H`, so a
 * motivated scraper still gets through in one extra line. What this
 * actually stops is the zero-effort case: `curl <url>` with no headers at
 * all, which is what most casual scraping/hotlinking looks like in
 * practice.
 *
 * Architecture:
 *   - Deployed standalone on the free *.workers.dev subdomain, no custom
 *     domain/zone required.
 *   - Fetches the real JSON from GitHub Pages (UPSTREAM_BASE below) on every
 *     request and streams it through — this Worker holds no copy of the
 *     data, so there is exactly one source of truth (the repo's own
 *     GitHub Pages deploy). A short edge cache (see CACHE_TTL_SECONDS) is
 *     the one exception: it's ephemeral (falls out on its own) and exists
 *     purely so a burst of visitors doesn't re-fetch GitHub Pages on every
 *     single request — not a copy anyone has to maintain.
 *   - Only proxies the specific data-file paths the site actually fetches
 *     (ALLOWED_PATHS below) — this is not a general-purpose CORS proxy for
 *     arbitrary GitHub Pages content.
 *   - Primary gate: Origin (falling back to Referer) must match the site's
 *     real origin. Secondary: Cloudflare's native per-IP Rate Limiting
 *     binding (a free-tier platform primitive, NOT Durable Objects — see
 *     wrangler.toml) as a light backstop against a single IP hammering the
 *     endpoint. The main defense against real volumetric abuse is
 *     Cloudflare's always-on network-level DDoS/bot mitigation, which
 *     requires no configuration here and applies to every Worker
 *     regardless of plan.
 */

// Overridable via [vars] in wrangler.toml (see wrangler.test.toml for the
// local-dev override used to test against a mock upstream instead of the
// real GitHub Pages site) — production deploys use these two defaults as-is,
// no [vars] section needed.
const DEFAULT_ALLOWED_ORIGIN = "https://jsierrahoopshype.github.io";
const DEFAULT_UPSTREAM_BASE = "https://jsierrahoopshype.github.io/nba-career-map";

// The exact set of JSON files index.html / teams.html fetch. Anything else
// gets a 404 — this Worker is a narrow proxy for these files, not an open
// relay for the rest of the site.
const ALLOWED_PATHS = new Set([
  "/nba_players_careers_READY.json",
  "/data/dashboard_data.json",
  "/data/nba_team_index.json",
  "/data/player_aliases.json",
  "/data/player_index.json",
  "/data/team_pages.json",
  "/data/club_pages.json",
  "/data/logs/transactions.json",
]);

// Pipeline runs at most a few times a day (see .github/workflows/update-
// careers.yml), so a few minutes of edge-cache staleness is an easy trade
// for not re-fetching GitHub Pages on every single visitor request.
const CACHE_TTL_SECONDS = 300;

const RATE_LIMIT_STATUS = 429;
const FORBIDDEN_STATUS = 403;

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Vary": "Origin",
  };
}

/** True if the request's Origin (preferred) or Referer names our site. */
function isAllowedCaller(request, allowedOrigin) {
  const origin = request.headers.get("Origin");
  if (origin) return origin === allowedOrigin;
  const referer = request.headers.get("Referer");
  if (referer) return referer.startsWith(allowedOrigin + "/") || referer === allowedOrigin;
  return false; // bare curl/wget: neither header present -> rejected
}

function forbidden() {
  // A plain, honest 403 rather than a truncated/corrupted 200: it's just as
  // easy for anyone who reads the response to route around (spoof a header
  // and retry) as a mangled body would be, but it doesn't risk confusing a
  // legitimate edge case (e.g. a privacy-focused browser that strips
  // Referer) with what looks like a data bug.
  return new Response(JSON.stringify({ error: "forbidden" }), {
    status: FORBIDDEN_STATUS,
    headers: { "Content-Type": "application/json" },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const allowedOrigin = env.ALLOWED_ORIGIN || DEFAULT_ALLOWED_ORIGIN;
    const upstreamBase = env.UPSTREAM_BASE || DEFAULT_UPSTREAM_BASE;

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(allowedOrigin) });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405 });
    }
    if (!ALLOWED_PATHS.has(url.pathname)) {
      return new Response("Not Found", { status: 404 });
    }
    if (!isAllowedCaller(request, allowedOrigin)) {
      return forbidden();
    }

    // Light per-IP backstop. env.RATE_LIMITER is absent unless wrangler.toml
    // configures it (and it's optional-by-design here) — skip cleanly
    // rather than erroring if it's ever unbound.
    if (env.RATE_LIMITER) {
      const ip = request.headers.get("cf-connecting-ip") || "unknown";
      const { success } = await env.RATE_LIMITER.limit({ key: ip });
      if (!success) {
        return new Response(JSON.stringify({ error: "rate limited" }), {
          status: RATE_LIMIT_STATUS,
          headers: { "Content-Type": "application/json", "Retry-After": "30" },
        });
      }
    }

    const upstreamUrl = upstreamBase + url.pathname;
    const upstreamResp = await fetch(upstreamUrl, {
      cf: { cacheTtl: CACHE_TTL_SECONDS, cacheEverything: true },
    });

    const headers = new Headers(upstreamResp.headers);
    for (const [k, v] of Object.entries(corsHeaders(allowedOrigin))) headers.set(k, v);
    headers.set("Cache-Control", `public, max-age=${CACHE_TTL_SECONDS}`);

    return new Response(upstreamResp.body, { status: upstreamResp.status, headers });
  },
};
