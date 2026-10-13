// Browser test for the live recorder + sound-by-sound editor + compare page (tools/s2w_recorder.py) using Chromium's FAKE
// microphone (a WAV of five tone steps), so no real microphone or voice is used. Needs Playwright for Node + Chromium
// (see tools/test_viewer_browser.js for the env vars).   node tools/test_recorder_browser.js
const fs = require('fs'), os = require('os'), path = require('path');
const { spawn } = require('child_process');
const net = require('net');
let playwright;
try { playwright = require(process.env.S2W_PLAYWRIGHT || 'playwright'); } catch (e) { playwright = require('/opt/node-tools/node_modules/playwright'); }
const { chromium } = playwright;
const R = path.resolve(__dirname, '..'), BUILD = path.resolve(R, process.env.S2W_BUILD || 'build'), PORT = +(process.env.S2W_PORT || 8765);
const CHROMIUM = process.env.S2W_CHROMIUM || (fs.existsSync('/opt/pw-browsers/chromium-1194/chrome-linux/chrome') ? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' : undefined);
const results = [];
const rec = (n, ok, d) => { results.push(ok); console.log((ok ? 'PASS ' : 'FAIL ') + n + (d ? '  -- ' + d : '')); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const refused = port => new Promise(res => { const s = net.connect(port, '127.0.0.1'); s.on('connect', () => { s.destroy(); res(false); }); s.on('error', () => res(true)); });

function stepsWav(file, rate) {   // 0.1 s silence, five 0.3 s tones (300..1500 Hz), silence: the fake microphone loops this file
  const parts = [[0, 0.1], [300, 0.3], [600, 0.3], [900, 0.3], [1200, 0.3], [1500, 0.3], [0, 0.4]];
  const n = Math.floor(parts.reduce((a, p) => a + p[1], 0) * rate), b = Buffer.alloc(44 + 2 * n);
  b.write('RIFF', 0); b.writeUInt32LE(36 + 2 * n, 4); b.write('WAVEfmt ', 8); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(2 * n, 40);
  let i = 0; for (const [hz, sec] of parts) for (let k = 0; k < Math.floor(sec * rate); k++, i++) b.writeInt16LE(hz ? Math.round(9000 * Math.sin(2 * Math.PI * hz * k / rate)) : 0, 44 + 2 * i);
  fs.writeFileSync(file, b);
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 's2w-recorder-browser-')), dir = path.join(tmp, 'voice'), fake = path.join(tmp, 'mic.wav');
  stepsWav(fake, 48000);
  const srv = spawn('python3', ['tools/s2w_recorder.py', '--build', BUILD, '--record-dir', dir, '--word', 'കലം|kalam', '--word', 'കാലം|kaalam', '--port', String(PORT)], { cwd: R });
  let err = ''; srv.stderr.on('data', d => err += d); let code = null; srv.on('exit', c => code = c);
  for (let i = 0; i < 50 && await refused(PORT); i++) await sleep(100);
  const browser = await chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--use-file-for-fake-audio-capture=' + fake, '--autoplay-policy=no-user-gesture-required'] });
  const ctx = await browser.newContext({ permissions: ['microphone'] });
  const page = await ctx.newPage(); await page.setViewportSize({ width: 1200, height: 1700 });
  const cerr = [], perr = [], bad = [], urls = [];
  page.on('console', m => { if (m.type() === 'error') cerr.push(m.text()); }); page.on('pageerror', e => perr.push(String(e)));
  page.on('requestfailed', r => bad.push('FAILED ' + r.url())); page.on('response', r => { urls.push(r.url()); if (r.status() >= 400) bad.push(r.status() + ' ' + r.url()); });
  const colours = id => page.evaluate(id => { const c = document.getElementById(id), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, s = new Set(); for (let i = 0; i < d.length; i += 4 * 97) s.add(d[i] + ',' + d[i + 1] + ',' + d[i + 2]); return s.size; }, id);
  const wavs = () => { const out = []; if (fs.existsSync(dir)) for (const w of fs.readdirSync(dir)) { const p = path.join(dir, w); if (fs.statSync(p).isDirectory()) for (const f of fs.readdirSync(p)) out.push(w + '/' + f); } return out.sort(); };
  const recordOnce = async () => { await page.click('#rec'); await sleep(1900); await page.click('#stop'); await page.waitForSelector('#review:not([hidden])'); await sleep(300); };

  await page.goto(`http://127.0.0.1:${PORT}/`); await page.waitForSelector('.chip');
  rec('word shown in Malayalam, Manglish and split into its sounds', (await page.textContent('#ml')) === 'കലം' && (await page.textContent('#rom')) === 'kalam' && (await page.textContent('#units')) === 'K · A · L · A · M', await page.textContent('#units'));
  rec('banner says personal test, not experiment data', /PERSONAL TEST RECORDINGS/.test(await page.textContent('header')) && /Not experiment data/.test(await page.textContent('header')));

  await page.click('#rec'); await sleep(1300);
  rec('live graph draws while recording', (await colours('live')) > 3 && !(await page.isDisabled('#stop')));
  await page.click('#stop'); await page.waitForSelector('#review:not([hidden])'); await sleep(400);
  rec('recorded waveform, spectrogram and QA shown', (await colours('wave')) > 3 && (await colours('spec')) > 20 && /QA verdict/.test(await page.textContent('#qa')));
  rec('combined bar drawn', (await colours('segbar')) > 3);
  const cards = await page.evaluate(() => [...document.querySelectorAll('#cards .badge')].map(b => b.textContent));
  rec('one card per sound: K A L A M', JSON.stringify(cards) === JSON.stringify(['K', 'A', 'L', 'A', 'M']), JSON.stringify(cards));
  const st0 = await page.evaluate(() => ({ edges: edges.slice(), method, dur: dur(), view: view.slice() }));
  rec('graph starts zoomed to the word and always shows every boundary', st0.view[1] - st0.view[0] <= st0.dur + 1e-9 && st0.view[0] <= st0.edges[0] + 1e-9 && st0.view[1] >= st0.edges[5] - 1e-9 && /zoomed to the word/.test(await page.textContent('#vinfo')), JSON.stringify(st0.view.map(x => +x.toFixed(2))) + ' of ' + st0.dur.toFixed(2));
  const zz = await page.evaluate(() => { const keep = edges.slice(); edges = [0.4, 0.45, 0.5, 0.55, 0.6, 0.65]; zoomed = true; setView(); const out = { v: view.slice(), d: dur() }; edges = keep; setView(); return out; });
  rec('with silence around the word, the view shrinks to the word plus 150 ms margins', Math.abs(zz.v[0] - 0.25) < 1e-9 && Math.abs(zz.v[1] - 0.8) < 1e-9 && zz.v[1] - zz.v[0] < zz.d, JSON.stringify(zz));
  rec('combined word caption shown', (await page.textContent('#comb')) === 'KALAM');
  rec('first-guess boundaries: 6 edges, increasing, inside the recording, tagged as a guess', st0.edges.length === 6 && st0.edges.every((x, i) => i === 0 || x > st0.edges[i - 1]) && st0.edges[5] <= st0.dur + 1e-6 && st0.edges[0] >= 0 && st0.method === 'suggested' && /first guess/.test(await page.textContent('#cards')), JSON.stringify(st0.edges.map(x => +x.toFixed(2))));
  rec('nothing saved yet', wavs().length === 0);

  // drag boundary 2 (between L... index 2) by 40 px
  const box = await page.locator('#wave').boundingBox();
  const X0 = (tt, vw, bx) => bx.x + (tt - vw[0]) / (vw[1] - vw[0]) * bx.width, span0 = st0.view[1] - st0.view[0];
  const hx = X0(st0.edges[2], st0.view, box);
  await page.mouse.move(hx, box.y + 7); await page.mouse.down(); await page.mouse.move(hx + 20, box.y + 7); await page.mouse.move(hx + 40, box.y + 7); await page.mouse.up();
  const st1 = await page.evaluate(() => ({ edges: edges.slice(), method }));
  const want = st0.edges[2] + 40 / box.width * span0;
  rec('dragging a handle moves exactly that boundary', Math.abs(st1.edges[2] - want) < 0.03 && st1.edges.every((x, i) => i === 2 || Math.abs(x - st0.edges[i]) < 1e-9), `moved ${(st1.edges[2] - st0.edges[2]).toFixed(3)} s, wanted ${(want - st0.edges[2]).toFixed(3)}`);
  rec('after a drag the boundaries are tagged as placed by you', st1.method === 'edited' && /placed by you/.test(await page.textContent('#cards')));
  const bx = X0(st1.edges[2], st0.view, box);
  await page.mouse.move(bx, box.y + 7); await page.mouse.down(); await page.mouse.move(box.x + box.width * 0.99, box.y + 7); await page.mouse.up();
  const st2 = await page.evaluate(() => edges.slice());
  rec('a boundary cannot be dragged past its neighbour', st2[2] < st2[3] - 0.011 && st2.every((x, i) => i === 0 || x > st2[i - 1]), JSON.stringify(st2.map(x => +x.toFixed(3))));
  const cx = X0(st2[2], st0.view, box);                                  // put it back with a real drag from where it is now
  await page.mouse.move(cx, box.y + 7); await page.mouse.down(); await page.mouse.move(hx, box.y + 7); await page.mouse.up();
  const st3 = await page.evaluate(() => edges.slice());
  rec('the boundary can be dragged back', Math.abs(st3[2] - st0.edges[2]) < 0.03 && st3[3] - st3[2] > 0.1, JSON.stringify(st3.map(x => +x.toFixed(3))));

  // the narrow last sound (M) and the end handle: grabbable and movable; dragging on the colour bar works; no page scroll
  const sy0 = await page.evaluate(() => window.scrollY);
  const bar = await page.locator('#segbar').boundingBox(), cur = await page.evaluate(() => ({ e: edges.slice(), v: view.slice() }));
  await page.mouse.move(X0(cur.e[4], cur.v, bar), bar.y + 14); await page.mouse.down(); await page.mouse.move(X0(cur.e[4], cur.v, bar) - 12, bar.y + 14); await page.mouse.up();
  const mv4 = await page.evaluate(() => edges.slice());
  rec('the handle in front of the last (narrow) sound can be dragged on the colour bar', mv4[4] < cur.e[4] - 0.005 && mv4.every((x, i) => i === 4 || Math.abs(x - cur.e[i]) < 1e-9), `${cur.e[4].toFixed(3)} -> ${mv4[4].toFixed(3)}`);
  await page.mouse.move(X0(mv4[5], cur.v, box), box.y + 7); await page.mouse.down(); await page.mouse.move(X0(mv4[5], cur.v, box) - 10, box.y + 7); await page.mouse.up();
  const mv5 = await page.evaluate(() => edges.slice());
  rec('the end handle can be dragged', mv5[5] < mv4[5] - 0.003 && mv5[5] > mv5[4], `${mv4[5].toFixed(3)} -> ${mv5[5].toFixed(3)}`);
  rec('dragging handles does not scroll the page', (await page.evaluate(() => window.scrollY)) === sy0);
  await page.evaluate(e => { edges = e.slice(); renderCards(); drawReview(); }, cur.e);

  // nudge buttons: exact 10 ms / 50 ms steps and clamping
  const n0 = await page.evaluate(() => edges.slice());
  await page.click('[data-n="2:1"]'); const n1 = await page.evaluate(() => edges.slice());
  await page.click('[data-n="2:-1"]', { modifiers: ['Shift'] }); const n2 = await page.evaluate(() => edges.slice());
  rec('nudge moves one boundary by exactly 10 ms, Shift by 50 ms', Math.abs(n1[2] - n0[2] - 0.01) < 1e-9 && Math.abs(n2[2] - n1[2] + 0.05) < 1e-9 && n2.every((x, i) => i === 2 || x === n0[i]), `${n0[2].toFixed(3)} ${n1[2].toFixed(3)} ${n2[2].toFixed(3)}`);
  await page.evaluate(() => { for (let i = 0; i < 80; i++) document.querySelector('[data-n="2:-1"]').click(); });
  const n3 = await page.evaluate(() => edges.slice());
  rec('nudging can never push a boundary past its neighbour', n3[2] >= n3[1] + 0.0119 && n3.every((x, i) => i === 0 || x > n3[i - 1]), JSON.stringify(n3.map(x => +x.toFixed(3))));
  await page.evaluate(() => { for (let i = 0; i < 80; i++) document.querySelector('[data-n="2:1"]').click(); });
  const n4 = await page.evaluate(() => edges.slice());
  rec('and not past the other neighbour either', n4[2] <= n4[3] - 0.0119 && n4.every((x, i) => i === 0 || x > n4[i - 1]));
  await page.click('#vz'); const vw1 = await page.evaluate(() => ({ v: view.slice(), d: dur() }));
  rec('Show whole recording switches the view to the full recording and back', vw1.v[0] === 0 && Math.abs(vw1.v[1] - vw1.d) < 1e-9 && /whole recording/.test(await page.textContent('#vinfo')));
  await page.click('#vz'); rec('and back to the zoomed word', await page.evaluate(() => zoomed === true && /zoomed to the word/.test(document.getElementById('vinfo').textContent)));
  // put boundary 2 back where the test below expects it
  await page.evaluate(w => { edges[2] = w; method = 'edited'; renderCards(); drawReview(); }, st0.edges[2]);

  // play one sound on its own
  const p3 = await page.evaluate(() => new Promise(res => { document.querySelector('[data-u="3"]').click(); setTimeout(() => res({ t: rev.currentTime, paused: rev.paused, a: edges[3], b: edges[4] }), 40); }));
  rec('play sound 4 starts at that sound', !p3.paused && p3.t >= p3.a - 0.01 && p3.t <= p3.b, JSON.stringify(p3));
  await sleep((p3.b - p3.a) * 1000 + 500);
  const p3b = await page.evaluate(() => ({ t: rev.currentTime, paused: rev.paused, b: edges[4] }));
  rec('play sound 4 stops at the end of that sound', p3b.paused && p3b.t >= p3b.b - 0.02 && p3b.t < p3b.b + 0.2, JSON.stringify(p3b));
  const w1 = await page.evaluate(() => new Promise(res => { document.getElementById('play').click(); setTimeout(() => res({ t: rev.currentTime, paused: rev.paused, btn: document.getElementById('play').textContent }), 60); }));
  rec('Play whole word restarts from the beginning after a single-sound play', !w1.paused && w1.t < 0.4, JSON.stringify(w1));
  await sleep(150);
  const w2 = await page.evaluate(() => new Promise(res => { document.getElementById('play').click(); res({ t: rev.currentTime, paused: rev.paused, btn: document.getElementById('play').textContent }); }));
  await sleep(250);
  const w3 = await page.evaluate(() => ({ t: rev.currentTime, paused: rev.paused, btn: document.getElementById('play').textContent }));
  rec('Pause really pauses and offers Resume', w2.paused && w3.paused && Math.abs(w3.t - w2.t) < 0.02 && /Resume/.test(w3.btn), JSON.stringify([w2, w3]));
  const w4 = await page.evaluate(() => new Promise(res => { document.getElementById('play').click(); setTimeout(() => res({ t: rev.currentTime, paused: rev.paused }), 100); }));
  rec('Resume continues from where it paused', !w4.paused && w4.t > w3.t && w4.t < w3.t + 0.6, JSON.stringify(w4));
  await page.evaluate(() => document.getElementById('play').click());

  await page.click('#discard'); await sleep(200);
  rec('Discard writes nothing and allows a new recording', wavs().length === 0 && (await page.isHidden('#review')) && !(await page.isDisabled('#rec')) && /nothing was saved/.test(await page.textContent('#status')));

  await recordOnce();
  const box2 = await page.locator('#wave').boundingBox(), e2 = await page.evaluate(() => ({ edges: edges.slice(), dur: dur(), view: view.slice() }));
  await page.mouse.move(X0(e2.edges[1], e2.view, box2), box2.y + 7); await page.mouse.down(); await page.mouse.move(X0(e2.edges[1], e2.view, box2) + 25, box2.y + 7); await page.mouse.up();
  const sent = await page.evaluate(() => ({ edges: edges.map(x => +x.toFixed(4)), method }));
  await page.click('#accept'); await page.waitForFunction(() => /Saved take_001|Could not save/.test(document.getElementById('status').textContent), null, { timeout: 8000 });
  rec('Accept saves the take and its sound boundaries', /Saved take_001/.test(await page.textContent('#status')) && JSON.stringify(wavs()) === JSON.stringify(['kalam/take_001.segments.json', 'kalam/take_001.wav']), JSON.stringify(wavs()));
  const seg = JSON.parse(fs.readFileSync(path.join(dir, 'kalam', 'take_001.segments.json'), 'utf8'));
  rec('saved boundaries are exactly what was on screen', JSON.stringify(seg.units) === JSON.stringify(['k', 'a', 'l', 'a', 'm']) && JSON.stringify(seg.edges) === JSON.stringify(sent.edges) && seg.method === 'edited', JSON.stringify(seg.edges));
  const f = fs.readFileSync(path.join(dir, 'kalam', 'take_001.wav')), rate = f.readUInt32LE(24), frames = f.readUInt32LE(40) / 2;
  rec('saved audio is valid mono 16-bit WAV, not silent', f.toString('ascii', 0, 4) === 'RIFF' && f.readUInt16LE(22) === 1 && f.readUInt16LE(34) === 16 && Math.abs(frames / rate - e2.dur) < 0.05 && (() => { let m = 0; for (let i = 44; i < f.length; i += 2) m = Math.max(m, Math.abs(f.readInt16LE(i))); return m > 1000; })());
  rec('accepted take listed as having sound boundaries', /sounds marked/.test(await page.textContent('#takes')));

  await page.click('.chip[data-i="1"]'); await sleep(300);
  rec('switching word shows K · AA · L · A · M', (await page.textContent('#ml')) === 'കാലം' && (await page.textContent('#units')) === 'K · AA · L · A · M', await page.textContent('#units'));
  await recordOnce(); await page.click('#accept'); await page.waitForFunction(() => /Saved take_001|Could not save/.test(document.getElementById('status').textContent), null, { timeout: 8000 });
  rec('second word accepted with its own boundaries', wavs().includes('kaalam/take_001.segments.json'));

  // compare page
  await page.goto(`http://127.0.0.1:${PORT}/compare`); await page.waitForSelector('#a option', { state: 'attached' });
  rec('compare lists both accepted takes', (await page.locator('#a option').count()) === 2);
  await page.click('#go'); await page.waitForSelector('#out table'); await sleep(300);
  const rows = await page.evaluate(() => [...document.querySelectorAll('#out table tr')].slice(1).map(tr => [...tr.children].map(td => td.textContent.trim())));
  rec('compare table has one row per aligned sound (5)', rows.length === 5, JSON.stringify(rows.map(r => r.slice(0, 3))));
  rec('compare marks A vs AA as the same unit with different length', /same unit, different length/.test(rows[1][2]) && rows[1][0] === 'A' && rows[1][1] === 'AA', JSON.stringify(rows[1]));
  rec('other units marked as the same unit', rows.filter((r, i) => i !== 1).every(r => /same unit/.test(r[2])));
  rec('compare shows both waveforms and a plain-language note', (await colours('ca')) > 3 && (await colours('cb')) > 3 && /AA/.test(await page.textContent('#out ul')));
  const before = urls.length; await page.click('[data-s="a"][data-i="1"]'); await sleep(500);
  rec('playing one sound of word A requests that take', urls.slice(before).some(u => /\/audio\/kalam\/take_001\.wav$/.test(u)));
  rec('compare states that differences are uncalibrated', /uncalibrated/.test(await page.textContent('#out')));

  rec('no page errors', perr.length === 0, perr.join('|'));
  rec('no console errors', cerr.length === 0, cerr.join('|'));
  rec('no failed requests', bad.length === 0, bad.join('|'));
  await browser.close(); srv.kill('SIGTERM');
  for (let i = 0; i < 50 && code === null; i++) await sleep(100);
  rec('server exits cleanly (code 0)', code === 0, 'exit=' + code + (err ? ' stderr=' + err.slice(0, 150) : ''));
  rec('port released', await refused(PORT));
  fs.rmSync(tmp, { recursive: true, force: true });
  const fails = results.filter(x => !x).length; console.log('\n' + (results.length - fails) + '/' + results.length + ' passed'); process.exit(fails ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR', e); process.exit(2); });
