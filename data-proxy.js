/* Shared data-file fetch helper for index.html and teams.html.
 *
 * Routes fetches for the site's JSON data files through a Cloudflare Worker
 * (see worker/) that gates on Origin/Referer before proxying through to the
 * real files on GitHub Pages — raises the cost of casually scraping these
 * files above "one curl command". This is friction, not a lock: the Worker
 * is transparent to a real browser load (same-origin headers are sent
 * automatically) and adds no visible latency for a real visitor beyond one
 * extra network hop, cached at Cloudflare's edge.
 *
 * Falls back to the direct same-origin relative fetch (today's behavior) if
 * the Worker is ever unreachable or misconfigured, so an outage on the
 * proxy side degrades gracefully instead of breaking the site — reliability
 * for real visitors matters more here than closing that specific gap.
 */
(function () {
  // Replace after deploying (see worker/README.md) — `wrangler deploy`
  // prints the exact *.workers.dev URL assigned to your account.
  const DATA_PROXY_BASE = "https://nba-career-map-data-proxy.thejorgesierra.workers.dev";

  // The Worker only answers the github.io origin (its ALLOWED_ORIGIN). From
  // hoopsmatic.com every call is refused, which printed a CORS error per file
  // in the console before falling back, so there go straight to the
  // same-origin file (what visitors were already getting).
  const PROXY_ORIGIN = "https://jsierrahoopshype.github.io";

  async function fetchData(path) {
    if (location.origin !== PROXY_ORIGIN) return fetch(path);
    try {
      const r = await fetch(DATA_PROXY_BASE + "/" + path);
      if (r.ok) return r;
    } catch (e) {
      // Worker unreachable (DNS, network, not yet deployed) — fall through.
    }
    return fetch(path);
  }

  window.fetchData = fetchData;
})();
