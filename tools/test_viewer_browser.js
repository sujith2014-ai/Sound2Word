// Browser interaction tests for the Sound2Word Acoustic Viewer (tools/s2w_viewer.py). SYNTHETIC fixture only.
//
//   node tools/test_viewer_browser.js
//
// Needs Playwright for Node and a Chromium build. Not part of `make test` (extra dependency).
//   S2W_PLAYWRIGHT  path to the playwright module   (default: require('playwright'), else /opt/node-tools/node_modules/playwright)
//   S2W_CHROMIUM    path to a Chromium executable   (default: /opt/pw-browsers/chromium-1194/chrome-linux/chrome if present)
//   S2W_REVIEW      optional review workbook to show in the verification panel (read-only)
//   S2W_BUILD       build directory with the C tools (default: build)
//   S2W_PORT        viewer port (default: 8765)
// Audio OUTPUT is not tested (no audio device): playback position and stop points are.
const fs = require('fs'), os = require('os'), path = require('path');
const { spawn, spawnSync } = require('child_process');
const net = require('net');
let playwright;
try { playwright = require(process.env.S2W_PLAYWRIGHT || 'playwright'); } catch (e) { playwright = require('/opt/node-tools/node_modules/playwright'); }
const { chromium } = playwright;
const R = path.resolve(__dirname, '..');
const BUILD = path.resolve(R, process.env.S2W_BUILD || 'build');
const PORT = +(process.env.S2W_PORT || 8765);
const REVIEW = process.env.S2W_REVIEW || '';
const CHROMIUM = process.env.S2W_CHROMIUM || (fs.existsSync('/opt/pw-browsers/chromium-1194/chrome-linux/chrome') ? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' : undefined);
const results = [];
const rec = (name, ok, detail) => { results.push({ name, ok, detail }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  -- ' + detail : '')); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const refused = port => new Promise(res => { const s = net.connect(port, '127.0.0.1'); s.on('connect', () => { s.destroy(); res(false); }); s.on('error', () => res(true)); });

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 's2w-viewer-browser-'));
  const sh = (cmd, args) => { const r = spawnSync(path.join(BUILD, cmd), args, { cwd: R, encoding: 'utf8' }); if (r.status !== 0 && cmd !== 's2w_dataset_check') throw new Error(cmd + ' failed: ' + r.stderr); };
  sh('s2w_make_fixture', [path.join(tmp, 'ds')]); sh('s2w_dataset_check', [path.join(tmp, 'ds'), '--emit-hashes']);
  fs.writeFileSync(path.join(tmp, 'base.cfg'), '#s2w-settings v1\n');
  sh('s2w_eval', ['run', '--dataset', path.join(tmp, 'ds'), '--settings', path.join(tmp, 'base.cfg'), '--split', 'calib', '--out-dir', path.join(tmp, 'runs'), '--run-id', 't']);
  const args = ['tools/s2w_viewer.py', '--build', BUILD, '--dataset', path.join(tmp, 'ds'), '--run', path.join(tmp, 'runs', 't'), '--port', String(PORT)];
  if (REVIEW) args.push('--review', REVIEW);
  const srv = spawn('python3', args, { cwd: R });
  let srvErr = ''; srv.stderr.on('data', d => srvErr += d);
  let exitCode = null; srv.on('exit', c => exitCode = c);
  for (let i = 0; i < 50 && await refused(PORT); i++) await sleep(100);

  const browser = await chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] });
  const page = await browser.newPage({ viewport: { width: 1300, height: 1500 } });
  const consoleErrors = [], pageErrors = [], bad = [], seen = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', e => pageErrors.push(String(e)));
  page.on('requestfailed', r => bad.push('FAILED ' + r.url() + ' ' + (r.failure() || {}).errorText));
  page.on('response', r => { seen.push(r.url()); if (r.status() >= 400) bad.push(r.status() + ' ' + r.url()); });

  await page.goto(`http://127.0.0.1:${PORT}/`);
  await page.waitForSelector('.q');
  const nq = await page.locator('.q').count();
  rec('query list loads', nq === 99, nq + ' queries');
  rec('summary count shown', (await page.textContent('#runinfo')).includes('72/99'), await page.textContent('#runinfo'));
  rec('synthetic banner shown', (await page.textContent('#banner')).includes('SYNTHETIC DEMO DATA'));

  await page.click('.q[data-id="rec0097"]');
  await page.waitForSelector('#wave');
  await sleep(300);
  const segCards = await page.locator('.seg').count();
  rec('3 segment cards for SYNTH_V1', segCards === 3, segCards + ' cards');

  const nonBlank = id => page.evaluate(id => { const c = document.getElementById(id), g = c.getContext('2d'), d = g.getImageData(0, 0, c.width, c.height).data; const seen = new Set(); for (let i = 0; i < d.length; i += 4 * 97) seen.add(d[i] + ',' + d[i + 1] + ',' + d[i + 2]); return seen.size; }, id);
  rec('waveform canvas has content', (await nonBlank('wave')) > 3, (await nonBlank('wave')) + ' distinct colours');
  rec('spectrogram canvas has content', (await nonBlank('spec')) > 20, (await nonBlank('spec')) + ' distinct colours');

  // segment bar vs waveform time axis: both are drawn by the same X(t); verify against real pixels
  const align = () => page.evaluate(() => {
    const out = []; const { rec } = CUR; const [t0, t1] = view(); const bar = document.getElementById('segbar'), wv = document.getElementById('wave');
    const dpr = devicePixelRatio || 1, W = bar.getBoundingClientRect().width, Ww = wv.getBoundingClientRect().width;
    const px = (c, x, y) => { const d = c.getContext('2d').getImageData(Math.round(x * dpr), Math.round(y * dpr), 1, 1).data; return [d[0], d[1], d[2]]; };
    const hex = h => [1, 3, 5].map(i => parseInt(h.substr(i, 2), 16));
    const near = (a, b) => Math.abs(a[0] - b[0]) < 6 && Math.abs(a[1] - b[1]) < 6 && Math.abs(a[2] - b[2]) < 6;
    rec.segments.forEach((s, i) => {
      const xs = (s.start - t0) / (t1 - t0) * W, xe = (s.end - t0) / (t1 - t0) * W, col = hex(COL[i % 5]);
      const startVisible = xs >= 8 && xs <= W - 14, endVisible = xe >= 14 && xe <= W - 8;
      if (!startVisible && !endVisible) return;
      const r = { i, sameWidth: Math.abs(W - Ww) < 1 };
      if (startVisible) { r.barInsideStart = near(px(bar, xs + 4, 15), col); r.barBeforeStart = !near(px(bar, xs - 4, 15), col); r.waveLine = [-1.5, -0.5, 0.5, 1.5].some(dx => { const q = px(wv, xs + dx, 3); return Math.abs(q[0] - col[0]) + Math.abs(q[1] - col[1]) + Math.abs(q[2] - col[2]) < 90; }); }
      if (endVisible) { r.barInsideEnd = near(px(bar, xe - 4, 15), col); r.barAfterEnd = !near(px(bar, xe + 4, 15), col); }
      out.push(r);
    });
    return out;
  });
  let al = await align();
  rec('segment bar aligned with waveform time axis (zoom 1)', al.length === 3 && al.every(a => Object.entries(a).every(([k, v]) => k === 'i' || v === true)), JSON.stringify(al));

  // does the estimated first segment start where the signal really starts? compare with measured peaks
  const onset = await page.evaluate(() => { const { rec } = CUR; const p = rec.peaks; let i = 0; while (i < p.length && Math.max(Math.abs(p[i][0]), Math.abs(p[i][1])) < 0.05) i++; return { sig: i / p.length * rec.duration, seg: rec.segments[0].start }; });
  rec('first segment start matches measured signal onset (+/-40 ms)', Math.abs(onset.sig - onset.seg) < 0.04, JSON.stringify(onset));

  // selection by dragging
  const box = await page.locator('#wave').boundingBox();
  await page.mouse.move(box.x + box.width * 0.30, box.y + 60); await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.45, box.y + 60); await page.mouse.move(box.x + box.width * 0.55, box.y + 60); await page.mouse.up();
  const sel = await page.evaluate(() => ({ sel, dur: CUR.rec.duration, txt: document.getElementById('selinfo').textContent }));
  const expA = sel.dur * 0.30, expB = sel.dur * 0.55;
  rec('drag selects the expected region', sel.sel && Math.abs(sel.sel[0] - expA) < 0.03 && Math.abs(sel.sel[1] - expB) < 0.03, JSON.stringify(sel));
  rec('selection text updates', /selection \d/.test(sel.txt), sel.txt);

  // play selection: starts at selection start, stops at selection end
  const s1 = await page.evaluate(() => new Promise(res => { document.getElementById('psel').click(); setTimeout(() => res({ t: audio.currentTime, paused: audio.paused }), 40); }));
  rec('play selection starts inside the selection', s1.t >= sel.sel[0] - 0.01 && s1.t <= sel.sel[1], JSON.stringify(s1));
  await sleep((sel.sel[1] - sel.sel[0]) * 1000 + 600);
  const s2 = await page.evaluate(() => ({ t: audio.currentTime, paused: audio.paused }));
  rec('play selection stops at the selection end', s2.paused && s2.t >= sel.sel[1] - 0.02 && s2.t < sel.sel[1] + 0.2, JSON.stringify(s2));

  // whole word play + stop
  await page.click('#pall'); await sleep(350);
  const w1 = await page.evaluate(() => ({ t: audio.currentTime, paused: audio.paused }));
  rec('play whole word advances', !w1.paused && w1.t > 0.15, JSON.stringify(w1));
  await page.click('#stop'); await sleep(100);
  rec('stop pauses playback', await page.evaluate(() => audio.paused));

  // segment play
  const g1 = await page.evaluate(() => new Promise(res => { document.querySelector('[data-p="1"]').click(); setTimeout(() => res({ t: audio.currentTime, a: CUR.rec.segments[1].start, b: CUR.rec.segments[1].end }), 40); }));
  rec('segment 2 play starts in segment 2', g1.t >= g1.a - 0.01 && g1.t <= g1.b, JSON.stringify(g1));
  await sleep((g1.b - g1.a) * 1000 + 500);
  const g2 = await page.evaluate(() => ({ t: audio.currentTime, paused: audio.paused, b: CUR.rec.segments[1].end }));
  rec('segment 2 play stops at its end', g2.paused && g2.t >= g2.b - 0.02 && g2.t < g2.b + 0.2, JSON.stringify(g2));

  // zoom + scroll
  await page.locator('#zoom').evaluate(e => { e.value = 2; e.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.locator('#scr').evaluate(e => { e.value = 50; e.dispatchEvent(new Event('input', { bubbles: true })); });
  const vz = await page.evaluate(() => ({ v: view(), d: CUR.rec.duration, zoom, scroll }));
  rec('zoom x2 narrows the visible span', Math.abs((vz.v[1] - vz.v[0]) - vz.d / 2) < 1e-6 && vz.zoom === 2, JSON.stringify(vz));
  rec('scroll moves the visible window', Math.abs(vz.v[0] - 0.5 * (vz.d - vz.d / 2)) < 1e-6, 'start=' + vz.v[0].toFixed(3));
  rec('waveform still drawn after zoom', (await nonBlank('wave')) > 3);
  await page.evaluate(() => { sel = null; draw(); });   // the selection overlay would darken boundary pixels
  al = await align();
  rec('segment bar aligned after zoom/scroll', al.length >= 1 && al.every(a => Object.entries(a).every(([k, v]) => k === 'i' || v === true)), JSON.stringify(al));

  // template play triggers a real audio request
  const before = seen.length;
  await page.click('[data-t]'); await sleep(500);
  rec('template play requests its audio', seen.slice(before).some(u => /\/audio\/rec\d+$/.test(u)), seen.slice(before).join(','));

  // failed query display + switching resets state
  await page.click('.q[data-id="rec0121"]'); await page.waitForFunction(() => CUR && CUR.rec.id === 'rec0121'); await sleep(300);
  const fq = await page.evaluate(() => ({ title: document.querySelector('#view h2:nth-of-type(1)') && document.querySelectorAll('#view .card h2')[1].textContent, bad: document.querySelector('.q[data-id="rec0121"] .bad') !== null,
    txt: document.querySelectorAll('#view .card')[2].textContent, sel, zoom, paused: audio.paused, top: CUR.q.cands[0].label, truth: CUR.q.label }));
  rec('failed query shown as wrong', fq.bad && fq.top !== fq.truth, JSON.stringify({ top: fq.top, truth: fq.truth }));
  rec('failed query panel shows true word and decision', fq.txt.includes('true word') && fq.txt.includes(fq.truth), '');
  rec('switching query resets selection, zoom and playback', fq.sel === null && fq.zoom === 1 && fq.paused, JSON.stringify({ sel: fq.sel, zoom: fq.zoom, paused: fq.paused }));

  // quality + verification panels
  const panels = await page.evaluate(() => ({ qa: [...document.querySelectorAll('#view .card')].find(c => c.textContent.includes('Signal quality')).textContent, ver: [...document.querySelectorAll('#view .card')].find(c => c.textContent.includes('Vocabulary verification')).textContent }));
  rec('signal-quality panel populated', /Duration/.test(panels.qa) && /SNR estimate/.test(panels.qa) && /QA verdict/.test(panels.qa));
  rec('verification panel: nothing shown as approved/verified' + (REVIEW ? ' (workbook words all pending)' : ' (no workbook given)'), (REVIEW ? (panels.ver.match(/Pending review/g) || []).length >= 1 : /No review workbook/.test(panels.ver)) && !/Approved|verified\b/i.test(panels.ver.replace('cannot approve', '')), '');

  // unknown recording: error card, no crash
  await page.goto(`http://127.0.0.1:${PORT}/#nonexistent`); await page.reload(); await sleep(800);
  const errCard = await page.textContent('#view');
  rec('unknown recording shows a clear error card', /Cannot show this recording/.test(errCard) && !errCard.includes(R), errCard.slice(0, 80));

  const realErrors = bad.filter(b => !b.includes('/nonexistent') && !/api\/(recording|match)\/nonexistent/.test(b));
  rec('no page errors', pageErrors.length === 0, pageErrors.join('|'));
  rec('no console errors (excluding the deliberate unknown-id request)', consoleErrors.filter(e => !/404/.test(e)).length === 0, consoleErrors.join('|'));
  rec('no failed network requests (excluding the deliberate unknown-id request)', realErrors.length === 0, realErrors.join('|'));

  await browser.close();
  srv.kill('SIGTERM');
  for (let i = 0; i < 50 && exitCode === null; i++) await sleep(100);
  rec('server exits cleanly on SIGTERM (code 0)', exitCode === 0, 'exit=' + exitCode + (srvErr ? ' stderr=' + srvErr.slice(0, 120) : ''));
  rec('viewer port released, no process left', await refused(PORT));
  fs.rmSync(tmp, { recursive: true, force: true });
  const fails = results.filter(r => !r.ok).length;
  console.log('\n' + (results.length - fails) + '/' + results.length + ' passed');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR', e); process.exit(2); });
