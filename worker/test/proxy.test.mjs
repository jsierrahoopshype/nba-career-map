// Self-contained integration test for the data-proxy Worker.
//
// Spins up (a) a plain Node static file server standing in for GitHub Pages,
// serving copies of a few real data files, and (b) `wrangler dev --local`
// running the actual worker/src/index.js against wrangler.test.toml (which
// points UPSTREAM_BASE at that local stand-in). No live Cloudflare account
// or network access is required — this verifies the Worker's own logic
// (Origin/Referer gate, path whitelist, rate limiting, CORS headers,
// pass-through fidelity), which is what's actually testable without a real
// deployment. It does NOT verify the real GitHub Pages hop or Cloudflare's
// edge cache/DDoS mitigation — those only exist once actually deployed.
//
// Run:  cd worker && npm run test

import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const UPSTREAM_PORT = 8973;
const WORKER_PORT = 8791;
const WORKER_BASE = `http://127.0.0.1:${WORKER_PORT}`;
const ALLOWED_ORIGIN = 'https://jsierrahoopshype.github.io';

let pass = 0, fail = 0;
function ok(label, cond, extra = '') {
  if (cond) { pass++; console.log(`✓  ${label}${extra ? '  [' + extra + ']' : ''}`); }
  else { fail++; console.log(`✗ FAIL  ${label}${extra ? '  [' + extra + ']' : ''}`); }
}

function mockUpstreamFixtures() {
  const dir = join('/tmp', 'mock-upstream-ci');
  mkdirSync(join(dir, 'data', 'logs'), { recursive: true });
  writeFileSync(join(dir, 'data', 'nba_team_index.json'),
    readFileSync(join(ROOT, 'data', 'nba_team_index.json')));
  writeFileSync(join(dir, 'data', 'player_aliases.json'),
    readFileSync(join(ROOT, 'data', 'player_aliases.json')));
  writeFileSync(join(dir, 'data', 'dashboard_data.json'), '{"stub":"dashboard_data"}');
  writeFileSync(join(dir, 'data', 'player_index.json'), '["Stub Player"]');
  writeFileSync(join(dir, 'data', 'team_pages.json'), '{"stub":"team_pages"}');
  writeFileSync(join(dir, 'data', 'club_pages.json'), '{"stub":"club_pages"}');
  writeFileSync(join(dir, 'data', 'logs', 'transactions.json'), '{"stub":"transactions"}');
  writeFileSync(join(dir, 'nba_players_careers_READY.json'),
    '[{"player":"Stub Player","career_history":[]}]');
  return dir;
}

function startUpstreamServer(dir) {
  const mime = { '.json': 'application/json' };
  const server = createServer((req, res) => {
    try {
      const path = join(dir, decodeURIComponent(req.url.split('?')[0]));
      const body = readFileSync(path);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(body);
    } catch {
      res.writeHead(404); res.end('not found');
    }
  });
  return new Promise((resolve) => server.listen(UPSTREAM_PORT, '127.0.0.1', () => resolve(server)));
}

async function waitForReady(url, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try { await fetch(url); return true; } catch { /* not up yet */ }
    await new Promise(r => setTimeout(r, 300));
  }
  return false;
}

async function main() {
  const fixturesDir = mockUpstreamFixtures();
  const upstream = await startUpstreamServer(fixturesDir);

  // A fresh --persist-to per run: wrangler's local simulation (including the
  // rate limiter's counters) otherwise persists in a shared directory
  // (~/.config/.wrangler) across separate `wrangler dev` invocations, which
  // would let an earlier manual test run's rate-limit state leak into this
  // one and produce a flaky/wrong result.
  const persistDir = join('/tmp', `wrangler-persist-${Date.now()}`);
  const wranglerProc = spawn('npx', ['wrangler', 'dev', '--local',
    '--port', String(WORKER_PORT), '--ip', '127.0.0.1', '--persist-to', persistDir,
    '--config', join(__dirname, '..', 'wrangler.test.toml')],
    { cwd: join(__dirname, '..'), stdio: 'ignore', env: { ...process.env, CI: 'true' } });

  try {
    const ready = await waitForReady(`${WORKER_BASE}/data/nba_team_index.json`);
    if (!ready) throw new Error('worker dev server never became ready');

    // 1. bare request, no Origin/Referer -> 403
    {
      const r = await fetch(`${WORKER_BASE}/data/nba_team_index.json`);
      ok('bare request (no Origin/Referer) is rejected', r.status === 403, r.status);
    }

    // 2. correct Origin -> 200, real content, CORS header present
    {
      const r = await fetch(`${WORKER_BASE}/data/nba_team_index.json`,
        { headers: { Origin: ALLOWED_ORIGIN } });
      const body = await r.text();
      const real = readFileSync(join(ROOT, 'data', 'nba_team_index.json'), 'utf8');
      ok('correct Origin -> 200', r.status === 200, r.status);
      ok('correct Origin -> CORS header present',
        r.headers.get('access-control-allow-origin') === ALLOWED_ORIGIN);
      ok('correct Origin -> content byte-identical to source file', body === real);
    }

    // 3. wrong Origin -> 403
    {
      const r = await fetch(`${WORKER_BASE}/data/nba_team_index.json`,
        { headers: { Origin: 'https://evil.example.com' } });
      ok('wrong Origin is rejected', r.status === 403, r.status);
    }

    // 4. correct Referer, no Origin -> 200 (fallback path)
    {
      const r = await fetch(`${WORKER_BASE}/data/nba_team_index.json`,
        { headers: { Referer: `${ALLOWED_ORIGIN}/nba-career-map/index.html` } });
      ok('correct Referer (no Origin) is accepted', r.status === 200, r.status);
    }

    // 5. path outside the whitelist -> 404
    {
      const r = await fetch(`${WORKER_BASE}/data/some_random_file.json`,
        { headers: { Origin: ALLOWED_ORIGIN } });
      ok('non-whitelisted path -> 404', r.status === 404, r.status);
    }

    // 5b. newly-whitelisted transactions ledger path -> 200
    {
      const r = await fetch(`${WORKER_BASE}/data/logs/transactions.json`,
        { headers: { Origin: ALLOWED_ORIGIN } });
      ok('whitelisted transactions.json path -> 200', r.status === 200, r.status);
    }

    // 6. POST -> 405
    {
      const r = await fetch(`${WORKER_BASE}/data/nba_team_index.json`,
        { method: 'POST', headers: { Origin: ALLOWED_ORIGIN } });
      ok('POST method -> 405', r.status === 405, r.status);
    }

    // 7. OPTIONS preflight -> 204 with CORS headers
    {
      const r = await fetch(`${WORKER_BASE}/data/nba_team_index.json`,
        { method: 'OPTIONS', headers: { Origin: ALLOWED_ORIGIN } });
      ok('OPTIONS preflight -> 204', r.status === 204, r.status);
      ok('OPTIONS preflight -> CORS header present',
        r.headers.get('access-control-allow-origin') === ALLOWED_ORIGIN);
    }

    // 8. rate limit: wrangler.test.toml sets 3 req / 10s. Rate-limit key is
    // per-IP (cf-connecting-ip) — use a distinct fake IP here so this block
    // starts its own clean budget, independent of the "unknown" key the
    // earlier requests in this same run already consumed against.
    {
      const results = [];
      for (let i = 0; i < 4; i++) {
        const r = await fetch(`${WORKER_BASE}/data/player_aliases.json`,
          { headers: { Origin: ALLOWED_ORIGIN, 'cf-connecting-ip': '203.0.113.42' } });
        results.push(r.status);
      }
      ok('rate limit allows the configured burst then blocks',
        results.slice(0, 3).every(s => s === 200) && results[3] === 429,
        JSON.stringify(results));
    }

    console.log(`\n${pass} passed, ${fail} failed`);
    process.exitCode = fail ? 1 : 0;
  } finally {
    wranglerProc.kill('SIGKILL');
    upstream.close();
    rmSync(persistDir, { recursive: true, force: true });
  }
}

// Hard ceiling so a stuck child process (e.g. a port left over from a prior
// crashed run) can never hang this script forever — it force-exits instead.
const watchdog = setTimeout(() => {
  console.error('watchdog: test did not complete in time, forcing exit');
  process.exit(1);
}, 45000);
watchdog.unref?.();

main()
  .catch(e => { console.error(e); process.exitCode = 1; })
  .finally(() => clearTimeout(watchdog));
