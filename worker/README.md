# NBA Career Map — data-proxy Worker

Cloudflare Worker that gates the site's JSON data files behind an
Origin/Referer check, so pulling them requires actually loading the site in
a browser instead of a bare `curl <url>`. See the header comment in
`src/index.js` for the full design writeup, and the "What this does and
doesn't achieve" section in the delivery notes for an honest read on how
strong this actually is (short version: real friction, not a lock — see
below).

## What this does and doesn't achieve

- **Stops**: `curl https://.../data/dashboard_data.json` with no extra
  flags — the zero-effort case, and what most casual scraping/hotlinking
  looks like in practice.
- **Does not stop**: anyone willing to open the site in a real browser (the
  data is public *on the site* by design — that's the product), or anyone
  willing to add one header: `curl -H "Origin: https://jsierrahoopshype.github.io" <worker-url>/data/dashboard_data.json`
  gets through identically to a real browser. Origin/Referer are
  request headers the client controls; nothing server-side can force them
  to be honest. This is friction, not a lock, by design — see the task
  framing this was built against.
- The rate limiter and Cloudflare's platform DDoS/bot mitigation help against
  volumetric abuse (a script hammering the endpoint), not against a single,
  deliberate, low-and-slow scrape.

## Deploying (steps only you can do — no Cloudflare account exists in the
sandbox this was built in)

1. `npm install` in this directory.
2. `npx wrangler login` — opens a browser to authenticate with your
   Cloudflare account (free account is enough, no billing info needed for
   Workers Free).
3. `npx wrangler deploy` — deploys to
   `https://nba-career-map-data-proxy.<your-subdomain>.workers.dev`. Wrangler
   prints the exact URL on success; `<your-subdomain>` is assigned to your
   Cloudflare account the first time you deploy any Worker (Cloudflare picks
   it, e.g. from your account name — you'll see it in the dashboard under
   Workers & Pages too).
4. Copy that URL into `/data-proxy.js` at the repo root (the
   `DATA_PROXY_BASE` constant near the top) — replace the
   `YOUR-SUBDOMAIN` placeholder — then commit and push so the live site
   picks it up.
5. That's it — no custom domain, no DNS, no `[routes]` section in
   `wrangler.toml` needed.

To redeploy after any change to `src/index.js`: just `npx wrangler deploy`
again.

## Local testing (no Cloudflare account needed)

`npm test` runs `test/proxy.test.mjs`, which spins up a plain local static
file server standing in for GitHub Pages plus `wrangler dev --local` running
the real Worker code against `wrangler.test.toml` (which points the Worker
at that local stand-in instead of the real site). It exercises the Origin/
Referer gate, the path whitelist, method restrictions, CORS headers, rate
limiting, and pass-through content fidelity — everything about the Worker's
own logic that's testable without a live deployment. It does **not** exercise
the real GitHub Pages hop or Cloudflare's actual edge cache/DDoS mitigation,
since those only exist once actually deployed.

`npm run dev:test` runs the same local setup interactively if you want to
`curl` it yourself; start a static server on port 8973 serving copies of
the JSON files first (see `wrangler.test.toml`'s comment).

## Tuning

- `ALLOWED_PATHS` in `src/index.js` — add a path here if the frontend ever
  fetches a new data file through the proxy; anything not listed 404s.
- `CACHE_TTL_SECONDS` — how long Cloudflare's edge caches a response before
  re-checking GitHub Pages. Pipeline runs at most a few times/day, so the
  default (300s) is a safe, generous margin; lower it if fresher data after
  a pipeline run matters more than shaving subrequests.
- The rate limit (`[[ratelimits]] simple = { limit, period }` in
  `wrangler.toml`) — 60 req/60s per IP by default. This is a genuine
  Cloudflare platform primitive (the Workers Rate Limiting binding), not
  Durable Objects, and is free-tier eligible.
