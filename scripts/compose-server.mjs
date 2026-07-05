#!/usr/bin/env node
// ============================================================
// compose-server.mjs — paste a script, get a video.
//
// Run:  node scripts/compose-server.mjs
// Then open http://localhost:3311 — paste your narration script
// (plus an optional dataset), hit Generate, and switch to the
// Remotion Studio tab: it hot-reloads with the new video.
//
// What it does on Generate:
//   1. Sends your script to the Claude relay Worker -> storyboard JSON
//   2. Resolves every photo beat via the hh-imagn-proxy Worker
//   3. Backs up src/beats.ts to src/beats.backup.ts
//   4. Rewrites ONLY the BEATS array in src/beats.ts
//      (VIDEO settings, timings, and type definitions stay untouched)
//
// Offline test mode: COMPOSE_MOCK=1 RESOLVE_MOCK=1 node scripts/compose-server.mjs
// Requires Node 18+. No dependencies. Binds to localhost only.
// ============================================================

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const PORT = 3311;
const IMAGN_PROXY = 'https://hh-imagn-proxy.thejorgesierra.workers.dev';

// ---- Claude relay (THE ONE THING TO ADJUST IF GENERATE FAILS) ----
// The Worker only answers requests from origins it trusts. The composer
// introduces itself as each origin below until one is accepted, then
// remembers the winner for the rest of the session.
const CLAUDE_RELAY = (
  process.env.CLAUDE_RELAY ||
  'https://hoopshype-claude-relay.thejorgesierra.workers.dev'
).replace(/\/$/, '');
const MODEL = 'claude-sonnet-4-6';
const ORIGIN_CANDIDATES = [
  'https://jsierrahoopshype.github.io', // GitHub Pages, home of the hub generator
  'http://localhost:3000',
  'http://localhost:8787', // wrangler dev
  'https://hoopshype.com',
];
let workingCombo = null; // cached after the first accepted origin+path

const BEATS_FILE = path.join('src', 'beats.ts');
const BACKUP_FILE = path.join('src', 'beats.backup.ts');

if (typeof fetch !== 'function') {
  console.error('This needs Node 18 or newer.');
  process.exit(1);
}
if (!fs.existsSync(BEATS_FILE)) {
  console.error(`Cannot find ${BEATS_FILE}. Run this from the project folder.`);
  process.exit(1);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toTimeString().slice(0, 8), ...a);

// ---------- Imagn helpers (same logic as resolve-images.mjs) ----------
function cleanUrl(input) {
  const s = String(input || '');
  if (/imagn\.com/i.test(s)) {
    const ids = s.match(/\d{5,}/g);
    if (ids && ids.length) return IMAGN_PROXY + '/img/' + ids[ids.length - 1];
  }
  const bare = s.match(/^\s*(\d{4,})\s*$/);
  if (bare) return IMAGN_PROXY + '/img/' + bare[1];
  return s;
}
function pickUrl(p) {
  const direct = cleanUrl(p.full || p.thumb || '');
  if (direct.startsWith(IMAGN_PROXY)) return direct;
  if (p.id && /^\d{4,}$/.test(String(p.id))) return IMAGN_PROXY + '/img/' + p.id;
  return direct;
}
function normalizeCredit(photographer) {
  if (!photographer) return null;
  const c = String(photographer).replace(/\s*[-/]\s*imagn images\s*$/i, '').trim();
  return c || null;
}
function escTs(v) {
  return String(v).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}
async function getJson(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 20000);
  const r = await fetch(url, {signal: ctrl.signal});
  clearTimeout(t);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}
async function searchImagn(q) {
  if (process.env.RESOLVE_MOCK) return mockSearch(q);
  const d = await getJson(IMAGN_PROXY + '/search?q=' + encodeURIComponent(q) + '&page=1');
  return d && Array.isArray(d.results) ? d : {results: []};
}
function mockSearch(q) {
  // Simulates Imagn's strict AND matching: long queries return nothing.
  if (String(q).trim().split(/\s+/).length > 4) return {results: []};
  const seed = [...q].reduce((a, c) => a + c.charCodeAt(0), 0);
  const mk = (n) => {
    const id = String(26000000 + seed * 7 + n);
    return {
      id,
      thumb: `https://cdn.imagn.com/preview/${id}.jpg`,
      full: `https://cdn.imagn.com/download/${id}.jpg`,
      photographer: n === 0 ? 'Kyle Terada-Imagn Images' : n === 1 ? 'Stephen Lew' : undefined,
    };
  };
  return {results: [mk(0), mk(1), mk(2), mk(3), mk(4)]};
}

// Last-resort query: longest run of Capitalized words, first two of them.
// "Jaylen Brown Boston Celtics 2025 portrait" -> "Jaylen Brown"
// Returns null if no two-word name is found (a lone "NBA" is too generic to trust).
function simplifyQuery(q) {
  const tokens = String(q || '').split(/\s+/);
  const runs = [];
  let cur = [];
  for (const t of tokens) {
    if (/^[A-Z][A-Za-z.'-]*$/.test(t)) cur.push(t);
    else {
      if (cur.length) runs.push(cur);
      cur = [];
    }
  }
  if (cur.length) runs.push(cur);
  runs.sort((a, b) => b.length - a.length);
  if (!runs.length || runs[0].length < 2) return null;
  return runs[0].slice(0, 2).join(' ');
}

// ---------- Claude relay call ----------
async function callClaude(prompt) {
  if (process.env.COMPOSE_MOCK) return mockClaude();
  const body = JSON.stringify({
    model: MODEL,
    max_tokens: 4000,
    messages: [{role: 'user', content: prompt}],
  });
  const combos = workingCombo
    ? [workingCombo]
    : ORIGIN_CANDIDATES.flatMap((origin) => [
        {url: CLAUDE_RELAY + '/v1/messages', origin},
        {url: CLAUDE_RELAY, origin},
      ]);
  const errors = [];
  for (const combo of combos) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 120000);
      const r = await fetch(combo.url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', Origin: combo.origin},
        body,
        signal: ctrl.signal,
      });
      clearTimeout(t);
      const text = await r.text();
      if (!r.ok) {
        errors.push(`[${combo.origin} -> ${combo.url}] HTTP ${r.status}: ${text.slice(0, 160)}`);
        continue;
      }
      if (!workingCombo) {
        workingCombo = combo;
        log(`Relay accepted origin ${combo.origin} at ${combo.url}`);
      }
      return extractText(text);
    } catch (e) {
      errors.push(`[${combo.origin} -> ${combo.url}] ${e.message}`);
    }
  }
  workingCombo = null;
  throw new Error('Claude relay refused all origins. Attempts:\n' + errors.join('\n'));
}

// Accepts standard Anthropic responses plus a few relay variants.
function extractText(raw) {
  let d;
  try {
    d = JSON.parse(raw);
  } catch {
    return raw; // relay returned plain text
  }
  if (typeof d === 'string') return d;
  if (Array.isArray(d.content)) {
    return d.content.map((c) => (typeof c === 'string' ? c : c.text || '')).join('\n');
  }
  if (typeof d.text === 'string') return d.text;
  if (typeof d.completion === 'string') return d.completion;
  if (d.error) {
    throw new Error('Relay error: ' + (typeof d.error === 'string' ? d.error : JSON.stringify(d.error).slice(0, 300)));
  }
  return raw;
}

function mockClaude() {
  return JSON.stringify({
    beats: [
      {kind: 'photo', narration: 'Executives around the league were stunned by the Jaylen Brown return, and Paul George instantly became the headline of the summer.', caption: 'The league reacts', queries: ['Jaylen Brown celebration', 'Paul George bench', 'NBA front office executives war room 2025'], queryFallback: 'Jaylen Brown'},
      {kind: 'stat', narration: 'Seventy-three wins.', value: 73, label: 'Regular-season wins', context: 'Best record in NBA history'},
      {kind: 'photo', narration: 'But the story did not end the way anyone expected, and Boston kept everyone in the league guessing about what the corresponding move would eventually look like.', caption: 'And then it happened', query: 'Jaylen Brown Boston Celtics 2025 portrait'},
    ],
  });
}

// ---------- Recency + ladder helpers ----------
function idOf(p) {
  const m = String(pickUrl(p)).match(/\/img\/(\d+)/);
  if (m) return Number(m[1]);
  if (p && /^\d+$/.test(String(p.id || ''))) return Number(p.id);
  return 0;
}
// Imagn photo IDs grow over time; highest ID first = newest first.
function sortByRecency(results) {
  return [...results].sort((a, b) => idOf(b) - idOf(a));
}
async function resolveQueryLadder(query, beatFallback) {
  const tries = [query, beatFallback, simplifyQuery(query)]
    .filter(Boolean)
    .map((v) => String(v).trim())
    .filter((v, i, arr) => v && arr.indexOf(v) === i);
  for (const q of tries) {
    try {
      const d = await searchImagn(q);
      if (d.results.length) return {query: q, results: sortByRecency(d.results)};
    } catch (e) {
      // network hiccup on this variant: try the next one
    }
    await sleep(250);
  }
  return {query, results: []};
}
// Declares the shots field in beats.ts type definitions (one-time, additive).
function ensureShotsType(fileText) {
  if (fileText.includes('shots?:')) return fileText;
  const anchor = '  focusY?: number;';
  const i = fileText.indexOf(anchor);
  if (i === -1) return fileText; // renderer carries a local fallback type
  const at = i + anchor.length;
  return (
    fileText.slice(0, at) +
    "\n\n  // Montage: multiple photos hard-cut inside this beat (set by the composer).\n  shots?: {image: string; credit?: string; focusX?: number; focusY?: number}[];" +
    fileText.slice(at)
  );
}

// ---------- Storyboard prompt ----------
function buildPrompt(script, dataset) {
  return `You convert a narration script into a JSON storyboard for a vertical NBA explainer video (photo montages with motion, animated bar charts, big stat counters).

Return ONLY a JSON object, no prose, no markdown fences, exactly this shape:
{
  "beats": [
    {"kind":"photo","narration":"<the sentence(s) this beat covers>","caption":"<optional on-screen text, max 7 words>","queries":["<2-4 words: one person or team + one moment word>"],"queryFallback":"<bare person or team name>"},
    {"kind":"stat","narration":"...","value":73,"label":"<what the number is>","context":"<optional supporting line>","valueSuffix":""},
    {"kind":"bar-chart","narration":"...","title":"<chart title>","data":[{"label":"","value":0,"highlight":true}],"valueSuffix":"","source":"<data source>"}
  ]
}

Rules:
- Split the ENTIRE script into beats, in order. Every sentence must appear in exactly one beat's "narration", unchanged. Do not rewrite the script.
- Keep each beat's narration under about 28 words. Split long paragraphs across consecutive beats.
- 5 to 14 beats total. Most beats are "photo".
- Photo "queries": 1 to 3 searches per beat. If the narration mentions several people or teams, give one query per person. The video cuts between one photo per query.
- Each query: the search is strict AND matching, every extra word removes results. Use 2 to 4 words: ONE person's full name (or one team) plus at most one or two concrete moment words (celebration, dunk, press conference, bench, warmup). Add a year only for historical moments. NEVER abstract scenes: no "front office", "war room", "executives", "sideline reaction", "roster", "portrait". If the narration names nobody, anchor on the most relevant star or team.
- "queryFallback": just the bare person or team name most central to the beat.
- Give a "caption" to most photo beats; captions drive the video's energy. Max 7 words. No hashtags, no emojis.
- Use "stat" when the narration centers on one striking number from the script or dataset.
- Use "bar-chart" only when the DATASET supports it or the script itself lists comparable values. NEVER invent numbers.
- Chart "data": 4 to 8 rows; mark the row the story is about with "highlight": true. Labels and values from the dataset verbatim.

SCRIPT:
${script}

${dataset ? 'DATASET:\n' + dataset : 'DATASET: (none provided)'}`;
}

function parseBeatsJson(text) {
  const t = String(text).replace(/```json|```/g, '').trim();
  const a = t.indexOf('{');
  const b = t.lastIndexOf('}');
  if (a === -1 || b === -1) throw new Error('No JSON in model reply. Raw start: ' + t.slice(0, 300));
  const obj = JSON.parse(t.slice(a, b + 1));
  if (!Array.isArray(obj.beats) || !obj.beats.length) throw new Error('Model JSON has no beats[].');
  return obj;
}

// ---------- Timing (estimated narration pace; whisper replaces this later) ----------
const wordCount = (s) => String(s || '').trim().split(/\s+/).filter(Boolean).length;
function computeDuration(beat) {
  let d = wordCount(beat.narration) / 2.4 + 0.4; // ~145 wpm
  if (beat.kind === 'bar-chart') d = Math.max(d + 1.2, 5.5);
  if (beat.kind === 'stat') d = Math.min(Math.max(d, 3), 4.5);
  if (beat.kind === 'photo') d = Math.min(d, 8.5);
  d = Math.min(Math.max(d, 2.2), 10);
  return Math.round(d * 10) / 10;
}

// ---------- Serialize to beats.ts ----------
function serializeBeats(beats) {
  const L = ['export const BEATS: Beat[] = ['];
  for (const b of beats) {
    L.push('  {');
    if (b.kind === 'photo') {
      L.push(`    image: '${escTs(b._image || 'AUTO')}',`);
      if (b._shots && b._shots.length) {
        L.push('    shots: [');
        for (const sh of b._shots) {
          const parts = [`image: '${escTs(sh.image)}'`];
          if (sh.credit) parts.push(`credit: '${escTs(sh.credit)}'`);
          L.push(`      {${parts.join(', ')}},`);
        }
        L.push('    ],');
      }
      L.push(`    duration: ${b._duration},`);
      if (!(b._shots && b._shots.length) && b._credit) L.push(`    credit: '${escTs(b._credit)}',`);
      if (b.caption) L.push(`    text: '${escTs(b.caption)}',`);
      if (b.query) L.push(`    query: '${escTs(b.query)}',`);
      if (typeof b.focusX === 'number') L.push(`    focusX: ${b.focusX},`);
      if (typeof b.focusY === 'number') L.push(`    focusY: ${b.focusY},`);
      if (b.zoom === 'in' || b.zoom === 'out') L.push(`    zoom: '${b.zoom}',`);
    } else if (b.kind === 'stat') {
      L.push(`    type: 'stat',`);
      L.push(`    duration: ${b._duration},`);
      L.push(`    value: ${Number(b.value) || 0},`);
      if (b.valuePrefix) L.push(`    valuePrefix: '${escTs(b.valuePrefix)}',`);
      if (b.valueSuffix) L.push(`    valueSuffix: '${escTs(b.valueSuffix)}',`);
      if (typeof b.decimals === 'number') L.push(`    decimals: ${b.decimals},`);
      L.push(`    label: '${escTs(b.label || '')}',`);
      if (b.context) L.push(`    context: '${escTs(b.context)}',`);
    } else if (b.kind === 'bar-chart') {
      L.push(`    type: 'bar-chart',`);
      L.push(`    duration: ${b._duration},`);
      L.push(`    title: '${escTs(b.title || '')}',`);
      L.push('    data: [');
      for (const row of b.data || []) {
        L.push(`      {label: '${escTs(row.label)}', value: ${Number(row.value) || 0}${row.highlight ? ', highlight: true' : ''}},`);
      }
      L.push('    ],');
      if (b.valuePrefix) L.push(`    valuePrefix: '${escTs(b.valuePrefix)}',`);
      if (b.valueSuffix) L.push(`    valueSuffix: '${escTs(b.valueSuffix)}',`);
      if (typeof b.decimals === 'number') L.push(`    decimals: ${b.decimals},`);
      if (b.source) L.push(`    source: '${escTs(b.source)}',`);
    } else {
      continue;
    }
    if (b.narration) L.push(`    narration: '${escTs(b.narration)}',`);
    L.push('  },');
  }
  L.push('];');
  return L.join('\n');
}

// Replace ONLY the BEATS array; everything else in beats.ts stays as-is.
function spliceBeatsFile(newArrayText) {
  const txt = fs.readFileSync(BEATS_FILE, 'utf8');
  const start = txt.indexOf('export const BEATS');
  if (start === -1) throw new Error('export const BEATS not found in ' + BEATS_FILE);
  const end = txt.indexOf('\n];', start);
  if (end === -1) throw new Error('End of BEATS array not found in ' + BEATS_FILE);
  return txt.slice(0, start) + newArrayText + txt.slice(end + 3);
}

// ---------- Generate pipeline ----------
async function handleGenerate(payload) {
  const script = String(payload.script || '').trim();
  const dataset = String(payload.dataset || '').trim();
  if (!script) throw new Error('Script is empty.');
  const warnings = [];

  log('Building storyboard via Claude relay...');
  const raw = await callClaude(buildPrompt(script, dataset));
  const beats = parseBeatsJson(raw).beats.filter((b) =>
    ['photo', 'stat', 'bar-chart'].includes(b.kind)
  );
  if (!beats.length) throw new Error('No usable beats in model output.');
  for (const b of beats) b._duration = computeDuration(b);

  log(`Storyboard: ${beats.length} beats. Resolving photos on Imagn...`);
  const usedUrls = new Set();
  for (const b of beats) {
    if (b.kind !== 'photo') continue;
    const queries = (Array.isArray(b.queries) && b.queries.length
      ? b.queries
      : b.query
        ? [b.query]
        : []
    )
      .slice(0, 3)
      .map((q) => String(q).trim())
      .filter(Boolean);
    b.query = queries.join(' | ');
    if (!queries.length) {
      b._shots = [];
      b._image = 'AUTO';
      warnings.push('A photo beat came without a search query.');
      continue;
    }
    // Target one cut every ~2.2 seconds, up to 4 shots per beat.
    const wanted = Math.max(1, Math.min(4, Math.round(b._duration / 2.2)));
    const pools = [];
    for (const q of queries) {
      const r = await resolveQueryLadder(q, b.queryFallback);
      if (r.results.length) {
        pools.push(r);
        log(`  OK "${r.query}" (${r.results.length} results, newest first)`);
      } else {
        warnings.push(`No Imagn results for "${q}" (all variants).`);
      }
      await sleep(350);
    }
    const shots = [];
    const takeFrom = (pool) => {
      for (const p of pool.results) {
        const url = pickUrl(p);
        if (usedUrls.has(url)) continue;
        usedUrls.add(url);
        shots.push({
          image: url,
          credit: normalizeCredit(p.photographer),
          candidates: pool.results.slice(0, 8).map((c) => ({
            url: pickUrl(c),
            credit: normalizeCredit(c.photographer),
          })),
        });
        return true;
      }
      return false;
    };
    for (const pool of pools) {
      if (shots.length >= wanted) break;
      takeFrom(pool);
    }
    let guard = 0;
    while (shots.length < wanted && pools.length && guard < 12) {
      takeFrom(pools[guard % pools.length]);
      guard++;
    }
    if (!shots.length) {
      b._shots = [];
      b._image = 'AUTO';
      warnings.push(`No usable photos for "${(b.caption || b.narration || '').slice(0, 40)}" — beat left as AUTO.`);
      continue;
    }
    b._shots = shots;
    b._image = shots[0].image;
    b._credit = shots[0].credit;
    log(`    -> ${shots.length} shot(s) for this beat`);
  }

  const newFile = ensureShotsType(spliceBeatsFile(serializeBeats(beats)));
  fs.copyFileSync(BEATS_FILE, BACKUP_FILE);
  fs.writeFileSync(BEATS_FILE, newFile);
  log(`Wrote ${BEATS_FILE} (backup at ${BACKUP_FILE}). Studio will hot-reload.`);

  const totalSeconds =
    beats.reduce((a, b) => a + b._duration, 0) - beats.length * 0.5 + 2.5;
  return {
    ok: true,
    warnings,
    totalSeconds: Math.round(totalSeconds * 10) / 10,
    beats: beats.map((b, i) => ({
      index: i,
      kind: b.kind,
      seconds: b._duration,
      narration: b.narration || '',
      caption: b.caption || b.title || b.label || '',
      query: b.query || null,
      image: b._image || null,
      shots: (b._shots || []).map((sh, n) => ({
        shot: n,
        image: sh.image,
        credit: sh.credit || null,
        candidates: sh.candidates || [],
      })),
    })),
  };
}

// ---------- Targeted beat updates (photo swap / focus point) ----------
const unescTs = (v) => String(v).replace(/\\'/g, "'").replace(/\\\\/g, '\\');

function parseBeatBlocks(lines) {
  const beatsStart = lines.findIndex((l) => l.includes('export const BEATS'));
  if (beatsStart === -1) throw new Error('export const BEATS not found in ' + BEATS_FILE);
  const stripStrings = (l) => l.replace(/'(?:\\'|[^'])*'/g, "''");
  const blocks = [];
  let depth = 0;
  let current = null;
  for (let i = beatsStart + 1; i < lines.length; i++) {
    const raw = lines[i];
    if (depth === 0 && /^\s*\];/.test(raw)) break;
    const stripped = stripStrings(raw);
    const opens = (stripped.match(/\{/g) || []).length;
    const closes = (stripped.match(/\}/g) || []).length;
    const prev = depth;
    depth += opens - closes;
    if (prev === 0 && depth >= 1) {
      current = {start: i, end: -1, imageIdx: -1, creditIdx: -1, focusXIdx: -1, focusYIdx: -1, shotIdxs: [], inShots: false};
    }
    if (current) {
      if (/^\s*shots:\s*\[/.test(raw)) current.inShots = true;
      else if (current.inShots && /^\s*\{image:/.test(raw)) current.shotIdxs.push(i);
      else if (current.inShots && /^\s*\],/.test(raw)) current.inShots = false;
      if (/^\s*image:\s*'/.test(raw)) current.imageIdx = i;
      if (/^\s*credit:\s*'/.test(raw)) current.creditIdx = i;
      if (/^\s*focusX:\s*/.test(raw)) current.focusXIdx = i;
      if (/^\s*focusY:\s*/.test(raw)) current.focusYIdx = i;
      if (prev >= 1 && depth === 0) {
        current.end = i;
        blocks.push(current);
        current = null;
      }
    }
  }
  return blocks;
}

function updateBeatFile(payload) {
  const idx = Number(payload.index);
  const text = fs.readFileSync(BEATS_FILE, 'utf8');
  const lines = text.split('\n');
  const blocks = parseBeatBlocks(lines);
  if (!Number.isInteger(idx) || idx < 0 || idx >= blocks.length) {
    throw new Error('Bad beat index: ' + payload.index);
  }
  const blk = blocks[idx];
  if (blk.imageIdx === -1) throw new Error('Beat ' + (idx + 1) + ' is not a photo beat.');

  // ---- Shot-level edit (montage beats) ----
  if (payload.shot !== undefined && payload.shot !== null && payload.shot !== '') {
    const sn = Number(payload.shot);
    if (!Number.isInteger(sn) || sn < 0 || sn >= blk.shotIdxs.length) {
      throw new Error('Bad shot index: ' + payload.shot);
    }
    const li = blk.shotIdxs[sn];
    const line = lines[li];
    const indentS = (line.match(/^\s*/) || ['      '])[0];
    const cur = {
      image: unescTs((line.match(/image:\s*'((?:\\'|[^'])*)'/) || ['', ''])[1]),
      credit: unescTs((line.match(/credit:\s*'((?:\\'|[^'])*)'/) || ['', ''])[1]),
      focusX: (line.match(/focusX:\s*([\d.]+)/) || [])[1],
      focusY: (line.match(/focusY:\s*([\d.]+)/) || [])[1],
    };
    if (typeof payload.image === 'string' && payload.image) cur.image = payload.image;
    if (typeof payload.credit === 'string') cur.credit = payload.credit;
    if (typeof payload.focusX === 'number') cur.focusX = String(Math.round(payload.focusX * 100) / 100);
    if (typeof payload.focusY === 'number') cur.focusY = String(Math.round(payload.focusY * 100) / 100);
    const parts = [`image: '${escTs(cur.image)}'`];
    if (cur.credit) parts.push(`credit: '${escTs(cur.credit)}'`);
    if (cur.focusX !== undefined) parts.push(`focusX: ${cur.focusX}`);
    if (cur.focusY !== undefined) parts.push(`focusY: ${cur.focusY}`);
    lines.splice(li, 1, `${indentS}{${parts.join(', ')}},`);
    // keep the legacy beat-level image mirrored to shot 0
    if (sn === 0 && typeof payload.image === 'string' && payload.image) {
      const ind2 = (lines[blk.imageIdx].match(/^\s*/) || ['    '])[0];
      lines.splice(blk.imageIdx, 1, `${ind2}image: '${escTs(payload.image)}',`);
    }
    fs.writeFileSync(BEATS_FILE, lines.join('\n'));
    return {ok: true};
  }

  // ---- Legacy beat-level edit (single-image beats) ----
  const indent = (lines[blk.imageIdx].match(/^\s*/) || ['    '])[0];
  const ops = [];
  if (typeof payload.image === 'string' && payload.image) {
    ops.push({idx: blk.imageIdx, type: 'replace', line: indent + "image: '" + escTs(payload.image) + "',"});
    if (typeof payload.credit === 'string') {
      if (blk.creditIdx !== -1) ops.push({idx: blk.creditIdx, type: 'replace', line: indent + "credit: '" + escTs(payload.credit) + "',"});
      else ops.push({idx: blk.imageIdx, type: 'insertAfter', line: indent + "credit: '" + escTs(payload.credit) + "',"});
    }
  }
  if (typeof payload.focusX === 'number') {
    const v = Math.round(payload.focusX * 100) / 100;
    if (blk.focusXIdx !== -1) ops.push({idx: blk.focusXIdx, type: 'replace', line: indent + 'focusX: ' + v + ','});
    else ops.push({idx: blk.imageIdx, type: 'insertAfter', line: indent + 'focusX: ' + v + ','});
  }
  if (typeof payload.focusY === 'number') {
    const v = Math.round(payload.focusY * 100) / 100;
    if (blk.focusYIdx !== -1) ops.push({idx: blk.focusYIdx, type: 'replace', line: indent + 'focusY: ' + v + ','});
    else ops.push({idx: blk.imageIdx, type: 'insertAfter', line: indent + 'focusY: ' + v + ','});
  }
  if (!ops.length) throw new Error('Nothing to update.');
  ops.sort((a, b) => b.idx - a.idx || (a.type === 'replace' ? -1 : 1));
  for (const op of ops) {
    if (op.type === 'replace') lines.splice(op.idx, 1, op.line);
    else lines.splice(op.idx + 1, 0, op.line);
  }
  fs.writeFileSync(BEATS_FILE, lines.join('\n'));
  return {ok: true};
}

// ---------- Web UI ----------
const PAGE = `<!doctype html>
<html><head><meta charset="utf-8"><title>HoopsHype Video Composer</title>
<script src="https://cdn.jsdelivr.net/npm/face-api.js@0.22.2/dist/face-api.min.js"></script>
<style>
  body{background:#0B0D10;color:#eee;font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:30px auto;padding:0 16px}
  h1{font-size:22px;letter-spacing:1px} h1 b{color:#FF7A00}
  label{display:block;margin:16px 0 6px;color:#bbb;font-size:13px;letter-spacing:.5px;text-transform:uppercase}
  textarea{width:100%;background:#14171c;color:#eee;border:1px solid #2a2f36;border-radius:8px;padding:12px;font-size:15px;box-sizing:border-box}
  #script{height:220px} #dataset{height:110px}
  button{margin-top:16px;background:#FF7A00;color:#111;border:0;border-radius:8px;padding:12px 26px;font-size:16px;font-weight:700;cursor:pointer}
  button:disabled{opacity:.5;cursor:wait}
  .opt{display:inline-block;margin-left:16px;color:#bbb;font-size:13px}
  #status{margin-top:14px;color:#9ad;white-space:pre-wrap;font-size:14px}
  .beat{background:#14171c;border:1px solid #2a2f36;border-radius:10px;padding:14px;margin-top:12px}
  .k{color:#FF7A00;font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:1px}
  .cap{font-weight:700;margin-top:4px}
  .n{color:#ccc;font-size:13px;margin-top:4px}
  .c{color:#888;font-size:12px;margin-top:6px}
  .shots{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}
  .shotcard{background:#101318;border:1px solid #262b32;border-radius:8px;padding:8px}
  .imgwrap{position:relative;display:block;cursor:crosshair}
  .imgwrap img{width:190px;height:auto;display:block;border-radius:6px}
  .dot{position:absolute;width:16px;height:16px;border:3px solid #FF7A00;border-radius:50%;transform:translate(-50%,-50%);pointer-events:none;box-shadow:0 0 6px rgba(0,0,0,.8)}
  .thumbs{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;max-width:190px}
  .thumbs img{width:40px;height:40px;object-fit:cover;border-radius:4px;cursor:pointer;border:2px solid transparent}
  .thumbs img.sel{border-color:#FF7A00}
  .hint{color:#777;font-size:11px;margin-top:6px}
  .st{color:#7fd48a;font-size:12px;margin-left:10px}
  .warn{color:#f6c445;font-size:13px;margin-top:10px}
</style></head><body>
<h1>HOOPSHYPE <b>VIDEO COMPOSER</b></h1>
<div style="color:#999;font-size:13px">Paste the script, Generate. Each beat becomes a photo montage. Fine-tune below: click a small thumbnail to swap that shot, click ON a photo where the face is to set its zoom focus. Faces are detected automatically when possible. Studio reloads by itself after every change.</div>
<label>Script</label><textarea id="script" placeholder="Boston's Jaylen Brown trade return..."></textarea>
<label>Dataset (optional, for charts)</label><textarea id="dataset" placeholder="Warriors 2015-16: 73 wins&#10;..."></textarea>
<br><button id="go">Generate video</button><span class="opt"><input type="checkbox" id="autofocus" checked> Auto face focus</span>
<div id="status"></div><div id="out"></div>
<script>
var go=document.getElementById('go'),st=document.getElementById('status'),out=document.getElementById('out');
var WEIGHTS='https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js/weights';
var modelsPromise=null;
function loadModels(){
  if(typeof faceapi==='undefined')return Promise.reject(new Error('face-api CDN unavailable'));
  if(!modelsPromise)modelsPromise=faceapi.nets.tinyFaceDetector.loadFromUri(WEIGHTS);
  return modelsPromise;
}
function detectFocus(url,cb){
  loadModels().then(function(){
    var im=new Image();im.crossOrigin='anonymous';
    im.onload=function(){
      faceapi.detectAllFaces(im,new faceapi.TinyFaceDetectorOptions({scoreThreshold:0.4}))
      .then(function(ds){
        if(!ds||!ds.length)return cb(null);
        var best=ds[0];
        for(var k=1;k<ds.length;k++){if(ds[k].box.width*ds[k].box.height>best.box.width*best.box.height)best=ds[k];}
        var bx=best.box;
        var fx=(bx.x+bx.width/2)/im.naturalWidth;
        var fy=(bx.y+bx.height*0.38)/im.naturalHeight;
        cb({fx:Math.min(0.95,Math.max(0.05,fx)),fy:Math.min(0.95,Math.max(0.05,fy))});
      }).catch(function(){cb(null);});
    };
    im.onerror=function(){cb(null);};
    im.src=url;
  }).catch(function(){cb(null);});
}
function post(url,body,cb){
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(function(r){return r.json();}).then(cb)
  .catch(function(e){cb({ok:false,error:e.message});});
}
function setStatus(i,msg){var el=document.getElementById('st'+i);if(el){el.textContent=msg;setTimeout(function(){el.textContent='';},2600);}}
function placeDot(dot,fx,fy){dot.style.display='block';dot.style.left=(fx*100)+'%';dot.style.top=(fy*100)+'%';}
var focusJobs=[];
function runFocusJobs(){
  if(!document.getElementById('autofocus').checked)return;
  var job=focusJobs.shift();
  if(!job)return;
  detectFocus(job.url,function(res){
    if(res&&!job.manual()){
      placeDot(job.dot,res.fx,res.fy);
      post('/update-beat',{index:job.index,shot:job.shot,focusX:res.fx,focusY:res.fy},function(r){
        if(r.ok)setStatus(job.index,'face lock: shot '+(job.shot+1));
      });
    }
    setTimeout(runFocusJobs,120);
  });
}
function buildShotCard(b,sh){
  var card=document.createElement('div');card.className='shotcard';
  var manual=false;
  var wrap=document.createElement('div');wrap.className='imgwrap';
  var img=document.createElement('img');img.src=sh.image;wrap.appendChild(img);
  var dot=document.createElement('span');dot.className='dot';dot.style.display='none';wrap.appendChild(dot);
  wrap.onclick=function(ev){
    var r=wrap.getBoundingClientRect();
    var fx=Math.max(0,Math.min(1,(ev.clientX-r.left)/r.width));
    var fy=Math.max(0,Math.min(1,(ev.clientY-r.top)/r.height));
    manual=true;placeDot(dot,fx,fy);
    post('/update-beat',{index:b.index,shot:sh.shot,focusX:fx,focusY:fy},function(res){setStatus(b.index,res.ok?'focus saved':'ERROR: '+res.error);});
  };
  card.appendChild(wrap);
  var cr=document.createElement('div');cr.className='c';
  cr.textContent=sh.credit?(sh.credit+'-Imagn Images'):'Imagn Images';
  if(sh.candidates&&sh.candidates.length>1){
    var row=document.createElement('div');row.className='thumbs';
    sh.candidates.forEach(function(c){
      var t=document.createElement('img');t.src=c.url;
      if(c.url===sh.image)t.className='sel';
      t.onclick=function(){
        post('/update-beat',{index:b.index,shot:sh.shot,image:c.url,credit:c.credit||''},function(res){
          if(res.ok){
            img.src=c.url;
            var sel=row.querySelector('.sel');if(sel)sel.className='';
            t.className='sel';
            cr.textContent=c.credit?(c.credit+'-Imagn Images'):'Imagn Images';
            manual=false;dot.style.display='none';
            setStatus(b.index,'shot '+(sh.shot+1)+' swapped');
            if(document.getElementById('autofocus').checked){
              detectFocus(c.url,function(res2){
                if(res2&&!manual){
                  placeDot(dot,res2.fx,res2.fy);
                  post('/update-beat',{index:b.index,shot:sh.shot,focusX:res2.fx,focusY:res2.fy},function(){});
                }
              });
            }
          } else setStatus(b.index,'ERROR: '+res.error);
        });
      };
      row.appendChild(t);
    });
    card.appendChild(row);
  }
  card.appendChild(cr);
  focusJobs.push({index:b.index,shot:sh.shot,url:sh.image,dot:dot,manual:function(){return manual;}});
  return card;
}
function renderBeats(d){
  out.innerHTML='';focusJobs=[];
  d.warnings.forEach(function(w){var p=document.createElement('div');p.className='warn';p.textContent='Warning: '+w;out.appendChild(p);});
  d.beats.forEach(function(b){
    var div=document.createElement('div');div.className='beat';
    var head='<span class="k">'+(b.index+1)+'. '+b.kind+' \u00b7 '+b.seconds+'s'+(b.shots&&b.shots.length?(' \u00b7 '+b.shots.length+' shots'):'')+'<\/span><span class="st" id="st'+b.index+'"><\/span>';
    div.innerHTML=head+(b.caption?'<div class="cap">'+b.caption+'<\/div>':'')+'<div class="n">'+b.narration+'<\/div>';
    if(b.kind==='photo'){
      if(b.shots&&b.shots.length){
        var srow=document.createElement('div');srow.className='shots';
        b.shots.forEach(function(sh){srow.appendChild(buildShotCard(b,sh));});
        div.appendChild(srow);
        var hint=document.createElement('div');hint.className='hint';hint.textContent='Small thumbnails swap that shot \u00b7 click on a photo to set its zoom focus';
        div.appendChild(hint);
      } else {
        var warn=document.createElement('div');warn.className='warn';warn.textContent='No photos resolved for this beat (AUTO).';div.appendChild(warn);
      }
      if(b.query){var q=document.createElement('div');q.className='c';q.textContent='queries: '+b.query;div.appendChild(q);}
    }
    out.appendChild(div);
  });
  setTimeout(runFocusJobs,300);
}
go.onclick=function(){
  var script=document.getElementById('script').value, dataset=document.getElementById('dataset').value;
  go.disabled=true; out.innerHTML=''; st.textContent='Working... (storyboard + photo search, 20-90s)';
  post('/generate',{script:script,dataset:dataset},function(d){
    go.disabled=false;
    if(!d.ok){st.textContent='ERROR: '+d.error; return;}
    st.textContent='Done. '+d.beats.length+' beats, ~'+d.totalSeconds+'s. beats.ts written; check the Studio tab, then fine-tune below.';
    renderBeats(d);
  });
};
</script></body></html>`;

// ---------- Server ----------
const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && (req.url === '/' || req.url === '/index.html')) {
    res.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
    return res.end(PAGE);
  }
  if (req.method === 'POST' && req.url === '/generate') {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', async () => {
      try {
        const result = await handleGenerate(JSON.parse(body || '{}'));
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify(result));
      } catch (e) {
        log('ERROR:', e.message);
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify({ok: false, error: e.message}));
      }
    });
    return;
  }
  if (req.method === 'POST' && req.url === '/update-beat') {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      try {
        const result = updateBeatFile(JSON.parse(body || '{}'));
        log('update-beat OK:', body.slice(0, 140));
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify(result));
      } catch (e) {
        log('update-beat ERROR:', e.message);
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify({ok: false, error: e.message}));
      }
    });
    return;
  }
  res.writeHead(404);
  res.end('Not found');
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('');
  console.log('  HoopsHype Video Composer');
  console.log(`  Open:  http://localhost:${PORT}`);
  console.log('  Keep this window open. Ctrl+C to stop.');
  console.log('');
});
