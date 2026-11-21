#!/usr/bin/env node
/*
 * Unit tests for docs/test_bed_v2_explorer.html
 *
 * Runs the page's own <script> inside a Node vm with a minimal fake DOM, then checks:
 *   1. data integrity (nodes, credentials, outcomes, metadata)
 *   2. step expansion (Auth Provider, Model Armor, Agent Gateway hops are present where they must be)
 *   3. secrets (the Secure Web Proxy never "sees" tokens it only tunnels)
 *   4. map geometry (no animated path runs through an unrelated component box)
 *   5. filters (every enabled dropdown option yields flows, every combination stays consistent,
 *      quick buttons reset dropdowns, the loaded flow always matches the filters)
 *   6. coverage (every source → destination pair has flows or an explicit "not modeled" reason;
 *      agent-to-agent auth inside GCP is covered in both directions)
 *   7. rendering (map, sequence, detail, timeline render for every step of every scenario)
 *
 * Usage:  node docs/tests/test_bed_v2_explorer.test.cjs
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML = path.join(__dirname, '..', 'test_bed_v2_explorer.html');
const src = fs.readFileSync(HTML, 'utf8');
const script = src.match(/<script>([\s\S]*)<\/script>/)[1];

/* ------------------------------------------------------------------ */
/* Minimal fake DOM                                                    */
/* ------------------------------------------------------------------ */
function makeEl(id) {
  const cls = new Set();
  const el = {
    id, innerHTML: '', textContent: '', value: '', checked: true, title: '', disabled: false,
    style: {}, dataset: {}, attrs: {}, children: [],
    clientWidth: 1000, clientHeight: 600, offsetTop: 0, offsetLeft: 0, offsetWidth: 100, scrollTop: 0, scrollLeft: 0,
    classList: { add: c => cls.add(c), remove: c => cls.delete(c), toggle: (c, on) => (on === undefined ? (cls.has(c) ? cls.delete(c) : cls.add(c)) : on ? cls.add(c) : cls.delete(c)), contains: c => cls.has(c) },
    get className() { return [...cls].join(' '); }, set className(v) { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => cls.add(c)); },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; }, hasAttribute(k) { return k in this.attrs; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {}, scrollIntoView() {}, click() { if (this.onclick) this.onclick({}); },
  };
  return el;
}
const els = {};
const document = {
  getElementById: id => (els[id] ||= makeEl(id)),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  documentElement: makeEl('html'),
  body: makeEl('body'),
};
const ctx = vm.createContext({
  document, window: { addEventListener() {}, innerWidth: 1512, innerHeight: 900 },
  location: { hash: '' }, history: { replaceState() {} }, navigator: {},
  requestAnimationFrame: () => 0, cancelAnimationFrame() {}, performance: { now: () => 0 },
  setTimeout: () => 0, clearTimeout() {}, getComputedStyle: () => ({}), console,
});
vm.runInContext(script, ctx, { filename: 'test_bed_v2_explorer.html<script>' });
const run = code => vm.runInContext(code, ctx);

/* ------------------------------------------------------------------ */
/* Tiny test runner                                                    */
/* ------------------------------------------------------------------ */
const results = [];
function test(name, fn) {
  const fails = [];
  try { fn(msg => fails.push(msg)); } catch (e) { fails.push('threw: ' + (e.stack || e).toString().split('\n').slice(0, 3).join(' | ')); }
  results.push({ name, fails });
}

const FLOWS = run('FLOWS'), N = run('N'), CRED = run('CRED'), META = run('META');
const flowsById = Object.fromEntries(FLOWS.map(f => [f.id, f]));
const allSteps = () => FLOWS.flatMap(f => f.variants.flatMap((v, vi) => v.steps.map((s, si) => ({ f, v, vi, s, si }))));

/* ================================================================== */
/* 1. Data integrity                                                   */
/* ================================================================== */
test('every step references known components and credentials', fail => {
  for (const { f, vi, s, si } of allSteps()) {
    if (!N[s.f]) fail(`${f.id}#${vi} step ${si + 1}: unknown from "${s.f}"`);
    if (!N[s.t]) fail(`${f.id}#${vi} step ${si + 1}: unknown to "${s.t}"`);
    if (!CRED[s.cred]) fail(`${f.id}#${vi} step ${si + 1}: unknown credential "${s.cred}"`);
    if (!s.label || !s.label.trim()) fail(`${f.id}#${vi} step ${si + 1}: empty label`);
    if (s.kind && !['ret', 'block', 'risk', 'gov'].includes(s.kind)) fail(`${f.id}#${vi} step ${si + 1}: bad kind ${s.kind}`);
  }
});
test('every flow has metadata, scenarios and an outcome per scenario', fail => {
  const ids = new Set();
  for (const f of FLOWS) {
    if (ids.has(f.id)) fail(`duplicate flow id ${f.id}`); ids.add(f.id);
    if (!META[f.id]) fail(`${f.id}: no META entry`);
    if (!f.variants.length) fail(`${f.id}: no scenarios`);
    if (!f.tryit || !f.tryit.length) fail(`${f.id}: no "Try it" commands`);
    for (const v of f.variants) {
      if (!v.out || !['ok', 'bad', 'warn'].includes(v.out.cls)) fail(`${f.id} "${v.name}": missing or bad outcome`);
      if (v.steps.length < 2) fail(`${f.id} "${v.name}": fewer than 2 steps`);
    }
  }
  for (const id of Object.keys(META)) if (!flowsById[id]) fail(`META entry ${id} has no flow`);
});
test('outcome colour matches the steps (blocked ⇔ bad, ok ⇒ no block)', fail => {
  for (const f of FLOWS) for (const v of f.variants) {
    const hasBlock = v.steps.some(s => s.kind === 'block'), hasRisk = v.steps.some(s => s.kind === 'risk');
    if (v.out.cls === 'bad' && !hasBlock) fail(`${f.id} "${v.name}": outcome bad but no blocking step`);
    if (v.out.cls === 'ok' && hasBlock) fail(`${f.id} "${v.name}": outcome ok but contains a blocking step`);
    if (hasBlock && v.out.cls === 'ok') fail(`${f.id} "${v.name}": blocked scenario marked ok`);
    if (v.risk && !hasRisk && v.out.cls !== 'warn') fail(`${f.id} "${v.name}": risk scenario without risk step or warn outcome`);
    if (v.neg && v.out.cls === 'ok') fail(`${f.id} "${v.name}": negative scenario has ok outcome`);
  }
});

/* ================================================================== */
/* 2. Expansion invariants                                             */
/* ================================================================== */
test('every broker credential request shows its Auth Provider lookup', fail => {
  for (const { f, v, s, si } of allSteps()) {
    if (s.t === 'BROKER' && ['CR_ID', 'AR', 'CR_LEG'].includes(s.f) && /retrieve_credentials|gemini-api-key|crm-api-key|crm-2lo|crm-3lo|github-3lo|gh-mcp-token/.test(s.label)) {
      const n1 = v.steps[si + 1], n2 = v.steps[si + 2];
      if (!(n1 && n1.f === 'BROKER' && n1.t.startsWith('P_') && n2 && n2.f === n1.t && n2.t === 'BROKER'))
        fail(`${f.id} "${v.name}" step ${si + 1}: broker request without BROKER→provider→BROKER lookup`);
    }
  }
});
test('every request through the ALB is screened by ingress Model Armor before reaching the agent', fail => {
  for (const f of FLOWS) for (const v of f.variants) {
    const st = v.steps;
    st.forEach((s, i) => {
      if (s.f === 'ALB' && s.t === 'CR_ID') {
        const before = st.slice(0, i);
        const lastIn = before.map(x => x.f + '>' + x.t).lastIndexOf('MAC>ALB');
        const lastIn2 = Math.max(lastIn, before.map(x => x.t).lastIndexOf('ALB'));
        const screened = before.slice(Math.max(0, lastIn2 - 6)).some(x => x.f === 'ALB' && x.t === 'MA_IN');
        const isCallback = /callback|forward/.test(s.label) && before.some(x => x.f === 'BROWSER' && x.t === 'ALB');
        if (!screened && !isCallback) fail(`${f.id} "${v.name}" step ${i + 1}: ALB forwards to crm-agent-id without a Model Armor check`);
      }
    });
  }
});
test('every hop through the managed egress gateway is screened by egress Model Armor', fail => {
  for (const f of FLOWS) for (const v of f.variants) {
    if (!v.steps.some(s => s.t === 'MGW')) continue;
    if (!v.steps.some(s => s.f === 'MGW' && s.t === 'MA_OUT')) fail(`${f.id} "${v.name}": crm-egress-gw in path but no Model Armor call`);
  }
});
test('every Secure Web Proxy CONNECT shows its self-managed Agent Gateway', fail => {
  for (const f of FLOWS) for (const v of f.variants) v.steps.forEach((s, i) => {
    if (s.t === 'SWP' && /CONNECT/.test(s.label)) {
      const n = v.steps[i + 1];
      if (!(n && n.f === 'SWP' && n.t === 'AGW_SWP' && n.kind === 'gov')) fail(`${f.id} "${v.name}" step ${i + 1}: CONNECT without crm-egress-swp-gw governance hop`);
    }
  });
});
test('requests from the client into crm-agent-id pass the ingress Agent Gateway governance hop', fail => {
  for (const f of FLOWS) for (const v of f.variants) {
    const fwd = v.steps.findIndex(s => s.f === 'ALB' && s.t === 'CR_ID');
    if (fwd < 0) continue;
    if (!v.steps.slice(0, fwd).some(s => s.t === 'INGRESS')) fail(`${f.id} "${v.name}": ALB → crm-agent-id without crm-ingress-gw hop`);
  }
});

test('every call into Vertex AI shows the IAM check on the caller', fail => {
  for (const f of FLOWS) for (const v of f.variants) v.steps.forEach((s, i) => {
    if (s.t === 'VERTEX' && s.f !== 'VERTEX' && !s.kind) {
      const n = v.steps[i + 1];
      if (s.cred !== 'gtok') fail(`${f.id} "${v.name}" step ${i + 1}: call to Vertex without a Google token`);
      if (!(n && ((n.f === 'VERTEX' && n.t === 'VERTEX' && /IAM/.test(n.label)) || (n.f === 'VERTEX' && n.kind === 'block'))))
        fail(`${f.id} "${v.name}" step ${i + 1}: Vertex call not followed by an IAM check`);
    }
  });
});
test('crm-agent-id is only reachable through the ALB (ingress internal-and-cloud-load-balancing)', fail => {
  for (const f of FLOWS) for (const v of f.variants) v.steps.forEach((s, i) => {
    if (s.t !== 'CR_ID' || s.f === 'CR_ID' || s.f === 'ALB' || s.kind) return;
    const n = v.steps[i + 1];
    if (!(n && n.f === 'CR_ID' && n.kind === 'block')) fail(`${f.id} "${v.name}" step ${i + 1}: ${s.f} reaches crm-agent-id directly`);
  });
});
test('Agent Engine is only reachable through the Vertex AI API', fail => {
  for (const f of FLOWS) for (const v of f.variants) v.steps.forEach((s, i) => {
    if (s.t === 'AR' && s.f !== 'AR' && !s.kind && s.f !== 'VERTEX') fail(`${f.id} "${v.name}" step ${i + 1}: ${s.f} calls test-agent-ar without Vertex`);
  });
});
test('every agent-to-agent flow inside GCP states the caller credential on its first hop', fail => {
  for (const f of FLOWS) {
    const m = META[f.id];
    if (m.pat !== 'a2a' || !m.src.some(x => ['cr', 'ar', 'leg'].includes(x))) continue;
    for (const v of f.variants) {
      const first = v.steps.find(s => s.f !== s.t);
      if (!first) { fail(`${f.id} "${v.name}": no hop`); continue; }
      if (!['gtok', 'none', 'spiffe'].includes(first.cred)) fail(`${f.id} "${v.name}": first hop carries ${first.cred}`);
      if (first.cred === 'none' && !/no token|ALB|anonymous|http:\/\//i.test(first.label + ' ' + (first.what || ''))) fail(`${f.id} "${v.name}": anonymous first hop not explained`);
    }
  }
});

/* ================================================================== */
/* 3. Secrets                                                          */
/* ================================================================== */
test('the Secure Web Proxy never holds a token (TLS is not inspected)', fail => {
  for (const f of FLOWS) for (const v of f.variants) for (const s of v.steps) {
    if ((s.f === 'SWP' || s.t === 'SWP') && ['gho', 'mcp', 'dual', 'crm', 'apikey', 'secret'].includes(s.cred) && !(s.blind || []).includes('SWP'))
      fail(`${f.id} "${v.name}": "${s.label}" gives the SWP a readable ${s.cred}`);
  }
});
test('the client secret only travels between the broker and the token endpoint', fail => {
  for (const f of FLOWS) for (const v of f.variants) for (const s of v.steps) {
    if (s.cred !== 'secret') continue;
    const ok = [s.f, s.t].every(n => ['BROKER', 'CRM', 'GH_OAUTH', 'P_2LO', 'P_3LO', 'P_GH3'].includes(n));
    if (!ok) fail(`${f.id} "${v.name}": client secret on hop ${s.f} → ${s.t}`);
  }
});

/* ================================================================== */
/* 4. Map geometry                                                     */
/* ================================================================== */
function segHitsRect(x1, y1, x2, y2, r) {
  // Liang–Barsky clip
  let t0 = 0, t1 = 1; const dx = x2 - x1, dy = y2 - y1;
  const p = [-dx, dx, -dy, dy], q = [x1 - r.x0, r.x1 - x1, y1 - r.y0, r.y1 - y1];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) { if (q[i] < 0) return false; }
    else { const t = q[i] / p[i]; if (p[i] < 0) { if (t > t1) return false; if (t > t0) t0 = t; } else { if (t < t0) return false; if (t < t1) t1 = t; } }
  }
  return t1 - t0 > 0.002;
}
test('no animated hop crosses an unrelated component box', fail => {
  const pairs = new Set();
  for (const { s } of allSteps()) if (s.f !== s.t) pairs.add([s.f, s.t].sort().join('|'));
  run('STATIC').forEach(([a, b]) => pairs.add([a, b].sort().join('|')));
  for (const key of pairs) {
    const [a, b] = key.split('|');
    const pts = run(`pathPts(${JSON.stringify(a)}, ${JSON.stringify(b)})`);
    for (const [k, n] of Object.entries(N)) {
      if (k === a || k === b) continue;
      const w = run(`nodeW(${JSON.stringify(k)})`) / 2 - 3, h = run(`nodeH(${JSON.stringify(k)})`) / 2 - 3;
      const r = { x0: n.x - w, x1: n.x + w, y0: n.y - h, y1: n.y + h };
      for (let i = 0; i < pts.length - 1; i++) {
        if (segHitsRect(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], r)) { fail(`${a} ↔ ${b} crosses ${k}`); break; }
      }
    }
  }
});

/* ================================================================== */
/* 5. Filters                                                          */
/* ================================================================== */
const KEYS = ['src', 'dst', 'rt', 'auth', 'meth'];
let OPTS; try { OPTS = run('FILTER_OPTIONS'); } catch (e) { const O = run('OPT'); OPTS = {src:O.fSrc,dst:O.fDst,rt:O.fRt,auth:O.fAuth,meth:O.fMeth}; }
test('filter options exist for every dropdown', fail => {
  for (const k of KEYS) if (!OPTS[k] || OPTS[k].length < 2) fail(`no options for ${k}`);
});
test('tags are derived from the steps (auth and method match what the animation shows)', fail => {
  for (const f of FLOWS) {
    const t = run(`flowTags(FLOWS.find(x=>x.id===${JSON.stringify(f.id)}))`);
    const st = f.variants.flatMap(v => v.steps);
    if (st.some(s => s.cred === 'spiffe') !== t.auth.includes('spiffe')) fail(`${f.id}: spiffe tag mismatch`);
    if (st.some(s => s.cred === 'gtok' && s.f !== 'CR_LEG') !== t.auth.includes('iam')) fail(`${f.id}: iam tag mismatch`);
    if (st.some(s => /^CONNECT/.test(s.label)) !== t.meth.includes('CONNECT')) fail(`${f.id}: CONNECT tag mismatch`);
    if (st.some(s => s.t === 'P_GH3' || s.t === 'P_3LO') !== t.auth.includes('3lo')) fail(`${f.id}: 3lo tag mismatch`);
    if (st.some(s => s.t === 'P_2LO') !== t.auth.includes('2lo')) fail(`${f.id}: 2lo tag mismatch`);
  }
});
test('every single dropdown option shows a correct count; zero-count options are disabled', fail => {
  run('resetFilters()');
  for (const k of KEYS) for (const [val] of OPTS[k]) {
    if (!val) continue;
    const c = run(`optionCount(${JSON.stringify(k)}, ${JSON.stringify(val)})`);
    const brute = FLOWS.filter(f => run(`flowMatches(FLOWS.find(x=>x.id===${JSON.stringify(f.id)}), Object.assign(blankFilter(), {${k}: ${JSON.stringify(val)}}))`)).length;
    if (c !== brute) fail(`${k}=${val}: count ${c} ≠ brute ${brute}`);
    if (c === 0) fail(`${k}=${val}: option never matches any flow (remove it or add a flow)`);
  }
});
test('every reachable combination of all five dropdowns loads a matching flow', fail => {
  let combos = 0;
  const walk = (i, f) => {
    if (i === KEYS.length) {
      combos++;
      run(`resetFilters(); ${KEYS.map(k => `setFilter(${JSON.stringify(k)}, ${JSON.stringify(f[k])});`).join(' ')}`);
      const list = run('filteredFlows().map(x=>x.id)');
      const shown = run('flow.id');
      if (!list.length) fail(`empty list for ${JSON.stringify(f)}`);
      else if (!list.includes(shown)) fail(`loaded ${shown} does not match ${JSON.stringify(f)}`);
      return;
    }
    for (const [val] of OPTS[KEYS[i]]) {
      const next = Object.assign({}, f, { [KEYS[i]]: val });
      // only walk options the UI would enable at this point
      run(`resetFilters(); ${KEYS.slice(0, i).map(k => `setFilter(${JSON.stringify(k)}, ${JSON.stringify(next[k])});`).join(' ')}`);
      if (val && run(`optionCount(${JSON.stringify(KEYS[i])}, ${JSON.stringify(val)})`) === 0) continue;
      walk(i + 1, next);
    }
  };
  walk(0, {});
  if (combos < 50) fail(`only ${combos} combinations walked`);
});
test('quick buttons reset every dropdown and load a matching flow', fail => {
  const Q = run('QUICK');
  Q.forEach((q, i) => {
    run(`resetFilters(); setFilter('auth','spiffe'); setFilter('meth','POST'); applyQuick(${i});`);
    const fl = run('filt');
    for (const k of KEYS) if (fl[k] !== (q[1][k] || '')) fail(`quick "${q[0]}" left ${k}=${fl[k]}`);
    const list = run('filteredFlows().map(x=>x.id)');
    if (!list.length) fail(`quick "${q[0]}" shows no flows`);
    if (!list.includes(run('flow.id'))) fail(`quick "${q[0]}" loaded ${run('flow.id')} which is not in its list`);
  });
});
test('changing a dropdown clears the quick-button selection; reset clears everything', fail => {
  run(`applyQuick(1); setFilter('rt','cr');`);
  if (run('filt.q') !== -1) fail('quick highlight not cleared after dropdown change');
  if (run('filt.pat')) fail('quick pattern filter still active after dropdown change (hidden filter)');
  if (run('filt.rt') !== 'cr') fail('dropdown value not applied');
  run('resetFilters()');
  const fl = run('filt');
  for (const k of KEYS) if (fl[k]) fail(`reset left ${k}=${fl[k]}`);
  if (fl.pat || fl.cmp || fl.ext || fl.q !== 0) fail('reset left quick state');
});
test('opening a flow that the filters hide resets the filters', fail => {
  run(`resetFilters(); applyQuick(1);`);   // Client → Agent
  run(`select('M2')`);                      // an Agent → MCP flow
  if (run('flow.id') !== 'M2') fail('select did not load M2');
  if (!run('filteredFlows().some(x=>x.id==="M2")')) fail('M2 loaded but hidden by filters');
});
test('changing flow resets scenario and animation', fail => {
  run(`select('M1'); vIdx = 2; go(5); select('K3');`);
  if (run('vIdx') !== 0) fail('scenario not reset');
  if (run('cur') !== -1) fail('animation not reset');
});

/* ================================================================== */
/* 6. Coverage                                                         */
/* ================================================================== */
test('every source → destination pair has flows or an explicit "not modeled" reason', fail => {
  const S = OPTS.src.map(o => o[0]).filter(Boolean), D = OPTS.dst.map(o => o[0]).filter(Boolean);
  const NM = run('NOT_MODELED');
  for (const s of S) for (const d of D) {
    const n = run(`filteredFlows(Object.assign(blankFilter(), {src:${JSON.stringify(s)}, dst:${JSON.stringify(d)}})).length`);
    if (!n && !NM[`${s}>${d}`]) fail(`${s} → ${d}: no flow and no "not modeled" reason`);
    if (n && NM[`${s}>${d}`]) fail(`${s} → ${d}: has flows but is also marked not modeled`);
  }
});
test('agent-to-agent auth inside GCP is covered in every direction', fail => {
  for (const [s, d] of [['cr', 'ar'], ['ar', 'cr'], ['leg', 'ar'], ['cr', 'leg'], ['ar', 'leg'], ['leg', 'cr']]) {
    const fs_ = run(`filteredFlows(Object.assign(blankFilter(), {src:${JSON.stringify(s)}, dst:${JSON.stringify(d)}})).map(f=>f.id)`);
    if (!fs_.length) fail(`no flow for ${s} → ${d}`);
  }
  const iam = run(`filteredFlows(Object.assign(blankFilter(), {src:'cr', dst:'ar', auth:'iam'})).length`);
  if (!iam) fail('no Cloud Run → Agent Engine flow tagged with Google IAM');
});

/* ================================================================== */
/* 7. Rendering every step                                             */
/* ================================================================== */
test('map, sequence, detail and timeline render for every step of every scenario', fail => {
  for (const f of FLOWS) f.variants.forEach((v, vi) => {
    try {
      run(`select(${JSON.stringify(f.id)}); vIdx = ${vi}; renderHead(); reset();`);
      for (let i = 0; i < v.steps.length; i++) {
        run(`go(${i})`);
        const map = document.getElementById('map').innerHTML, seq = document.getElementById('seq').innerHTML;
        if (!map.includes('mpkt')) fail(`${f.id} "${v.name}" step ${i + 1}: map not rendered`);
        if (!seq.includes(`data-step="${i}"`)) fail(`${f.id} "${v.name}" step ${i + 1}: sequence row missing`);
        if (!document.getElementById('nowbar').innerHTML.includes(`>${i + 1}<`)) fail(`${f.id} "${v.name}" step ${i + 1}: status bar not updated`);
      }
      const last = document.getElementById('pill').textContent;
      if (last !== v.out.code) fail(`${f.id} "${v.name}": outcome pill "${last}" ≠ "${v.out.code}"`);
    } catch (e) { fail(`${f.id} "${v.name}": ${e.message}`); }
  });
});
test('every hop has a recognised HTTP method / RPC label', fail => {
  const known = new Set(['POST', 'GET', 'CONNECT', 'DELETE', 'gRPC', '302', 'resp', 'internal', 'control']);
  for (const { f, v, s, si } of allSteps()) {
    const m = run(`methodOf(${JSON.stringify(s)})`);
    if (!known.has(m)) fail(`${f.id} "${v.name}" step ${si + 1}: method "${m}"`);
    if (s.f !== s.t && !s.kind && m === 'resp') fail(`${f.id} "${v.name}" step ${si + 1}: request "${s.label}" labelled as a response`);
  }
});

/* ------------------------------------------------------------------ */
let failed = 0;
for (const r of results) {
  const ok = !r.fails.length; if (!ok) failed++;
  console.log(`${ok ? '✓' : '✗'} ${r.name}${ok ? '' : `  (${r.fails.length})`}`);
  r.fails.slice(0, 25).forEach(m => console.log('    - ' + m));
  if (r.fails.length > 25) console.log(`    … ${r.fails.length - 25} more`);
}
const nSteps = allSteps().length, nVar = FLOWS.reduce((a, f) => a + f.variants.length, 0);
console.log(`\n${results.length - failed}/${results.length} tests passed · ${FLOWS.length} flows · ${nVar} scenarios · ${nSteps} steps`);
process.exit(failed ? 1 : 0);
