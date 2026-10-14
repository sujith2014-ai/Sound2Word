// Browser test for the /alphabet page of tools/s2w_recorder.py with MADE-UP clips (tones). No real speech or TTS output; it cannot
// judge whether a sound is right, only that the page, graphs, look-alike tables, playback requests and the human verdict store work.
//   node tools/test_alphabet_browser.js        (env vars as in tools/test_viewer_browser.js)
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
function wav(file, hz, sec, rate) { const n = Math.floor(sec * rate), b = Buffer.alloc(44 + 2 * n);
  b.write('RIFF', 0); b.writeUInt32LE(36 + 2 * n, 4); b.write('WAVEfmt ', 8); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(2 * n, 40);
  for (let i = 0; i < n; i++) b.writeInt16LE(Math.round(9000 * Math.min(1, i / 400, (n - i) / 400) * Math.sin(2 * Math.PI * hz * i / rate)), 44 + 2 * i); fs.writeFileSync(file, b); }

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 's2w-alphabet-browser-')), audio = path.join(tmp, 'audio', 'voiceA'), dir = path.join(tmp, 'voice');
  fs.mkdirSync(audio, { recursive: true });
  const table = { 'ശ': 3000, 'ഷ': 1500, 'സ': 6000, 'ക': 800, 'കാ': 900 }; const items = [];
  Object.entries(table).forEach(([t, hz], i) => { const fn = `u${i}.wav`; wav(path.join(audio, fn), hz, 0.4, 16000); items.push({ unit_id: i, word_ml: t, file: fn, voice: 'voiceA', model: 'made-up-tones', synthetic: true }); });
  fs.writeFileSync(path.join(audio, 'meta.json'), JSON.stringify({ items }));
  const srv = spawn('python3', ['tools/s2w_recorder.py', '--build', BUILD, '--record-dir', dir, '--word', 'കലം|kalam', '--alphabet-audio-dir', path.join(tmp, 'audio'), '--port', String(PORT)], { cwd: R });
  global.__srv = srv; let err = ''; srv.stderr.on('data', d => err += d); let code = null; srv.on('exit', c => code = c);
  for (let i = 0; i < 60 && await refused(PORT); i++) await sleep(100);
  const browser = await chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] });
  const page = await (await browser.newContext()).newPage(); await page.setViewportSize({ width: 1500, height: 1000 });
  const cerr = [], perr = [], bad = [], urls = [];
  page.on('console', m => { if (m.type() === 'error') cerr.push(m.text()); }); page.on('pageerror', e => perr.push(String(e)));
  page.on('requestfailed', r => bad.push('FAILED ' + r.url())); page.on('response', r => { urls.push(r.url()); if (r.status() >= 400) bad.push(r.status() + ' ' + r.url()); });
  const colours = sel => page.evaluate(s => { const c = document.querySelector(s), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, set = new Set(); for (let i = 0; i < d.length; i += 4 * 53) set.add(d[i] + ',' + d[i + 1] + ',' + d[i + 2] + ',' + d[i + 3]); return set.size; }, sel);

  await page.goto(`http://127.0.0.1:${PORT}/alphabet`); await page.waitForSelector('.cell');
  rec('summary lists the groups, the audio coverage and the look-alike count', /entries/.test(await page.textContent('#summary')) && /Audio for 5/.test(await page.textContent('#summary')) && /Look-alikes/.test(await page.textContent('#summary')), (await page.textContent('#summary')).slice(0, 160));
  const nCells = await page.locator('.cell').count();
  rec('the consonant table has all 36 consonants x 16 forms (576 cells)', nCells === 576 && (await page.locator('.rowh').count()) === 36, nCells + ' cells');
  rec('entries without audio say so', (await page.locator('.cell.noaudio').count()) === 576 - 5 && /no audio/.test(await page.textContent('.cell.noaudio')));
  rec('look-alike cells are marked', (await page.locator('.cell.look').count()) > 100);
  await page.waitForFunction(() => document.querySelectorAll('canvas[data-p]').length >= 1); await sleep(700);
  rec('small audio graph drawn in the grid cell', (await colours('canvas[data-p]')) > 1);

  await page.click('.cell[data-x="ശ"]'); await page.waitForSelector('#clips .clip'); await sleep(500);
  const det = await page.textContent('#detail');
  rec('detail shows the glyph, code point, label and IPA', /ശ/.test(det) && /U\+0D36/.test(det) && /sha \(soft sha\)/.test(det) && /ɕa/.test(det), det.slice(0, 120));
  rec('"same Manglish, different letter" chip for ഷ', (await page.locator('#detail [data-go="ഷ"]').count()) >= 1);
  rec('the clip has a graph and measurements', (await colours('canvas.wv')) > 3 && /spectral centre \d+ Hz/.test(await page.textContent('#detail')));
  rec('the page says only a listener can confirm', /Does it sound like/.test(det));
  rec('look-alike table lists ഷ (same Manglish) and സ (neighbour)', await page.evaluate(() => { const t = document.getElementById('grp').textContent; return /ഷ/.test(t) && /സ/.test(t) && /same Manglish/.test(t); }));
  rec('similarity column says differs for clearly different clips', /differs/.test(await page.textContent('#grp')) && !/acoustically similar/.test(await page.textContent('#grp')));
  const before = urls.length; await page.click('[data-pl="0"]'); await sleep(250);
  rec('play requests the clip and starts playing', urls.slice(before).some(u => /api\/alphabet\/clip\?text=%E0%B4%B6&k=0/.test(u)) && await page.evaluate(() => !audio.paused || audio.currentTime > 0));
  const b2 = urls.length; await page.click('[data-ab]'); for (let i = 0; i < 20 && !urls.slice(b2).some(u => /text=%E0%B4%B7&k=0/.test(u)); i++) await sleep(150);
  rec('A/B plays this clip, then the look-alike clip is requested', urls.slice(b2).some(u => /api\/alphabet\/clip\?text=%E0%B4%B7&k=0/.test(u)));

  await page.fill('input[data-rv="0"]', 'native-1'); await page.fill('input[data-nt="0"]', 'sounds like sa');
  await page.click('[data-vd="wrong"]'); await sleep(500);
  const rf = path.join(dir, 'alphabet_review.json');
  const saved = fs.existsSync(rf) ? JSON.parse(fs.readFileSync(rf, 'utf8')) : {};
  const entry = (saved['ശ'] || {}); const v0 = Object.values(entry)[0] || {};
  rec('a verdict is saved with note, reviewer and time', v0.verdict === 'wrong' && v0.note === 'sounds like sa' && v0.reviewer === 'native-1' && /^\d{4}-\d\d-\d\dT/.test(v0.utc || ''), JSON.stringify(v0));
  rec('the grid cell shows the verdict mark', /✗/.test(await page.textContent('.cell[data-x="ശ"]')));
  await page.reload(); await page.waitForSelector('.cell'); await sleep(900);
  rec('a deep link (#ശ) reopens the entry and the verdict survives a reload', /Does it sound like/.test(await page.textContent('#detail')) && await page.evaluate(() => document.querySelector('[data-vd="wrong"]').classList.contains('on')));
  await page.click('[data-vd="wrong"]'); await sleep(400);
  rec('clicking the same verdict again clears it (and the file)', !fs.existsSync(rf) || !JSON.parse(fs.readFileSync(rf, 'utf8'))['ശ']);

  await page.click('.tab[data-t="look"]'); await sleep(300);
  const groups = await page.evaluate(() => [...document.querySelectorAll('#body > div')].map(d => d.textContent.replace(/\s+/g, ' ').slice(0, 60)));
  rec('Look-alikes tab groups letters that share a Manglish (sha: ശ ഷ)', groups.some(g => /^sha\b/.test(g) && /ശ/.test(g) && /ഷ/.test(g)), JSON.stringify(groups.slice(0, 4)));
  await page.click('.tab[data-t="cv"]');
  await page.fill('#q', 'U+0D36'); await sleep(300);
  rec('search by code point finds ശ and its 16 forms only', (await page.locator('.cell[data-x]').count()) === 16 && (await page.locator('.cell[data-x] .g').evaluateAll(es => es.every(e => e.textContent.startsWith('ശ')))));
  await page.fill('#q', 'soft'); await sleep(300);
  rec('search by description finds the soft sha forms', (await page.locator('.cell[data-x]').count()) >= 1 && (await page.locator('.cell[data-x] .g').first().textContent()).startsWith('ശ'));
  await page.fill('#q', ''); await page.check('#onlyNoAudio'); await sleep(300);
  rec('"without audio only" hides the entries that have clips', (await page.locator('.cell:not(.noaudio)').count()) === 0);
  await page.uncheck('#onlyNoAudio'); await page.check('#onlyLook'); await sleep(300);
  rec('"look-alikes only" shows only look-alike cells', (await page.locator('.cell[data-x]:not(.look)').count()) === 0 && (await page.locator('.cell.look').count()) > 100);
  await page.uncheck('#onlyLook');
  await page.click('.tab[data-t="vowel"]'); await sleep(200);
  rec('Vowels tab lists the independent vowels', (await page.locator('.list .cell').count()) >= 16);
  await page.click('.tab[data-t="cluster"]'); await sleep(200);
  rec('Conjuncts tab explains how to get the list when no lexicon is given', /--alphabet-lexicon/.test(await page.textContent('#body')));

  rec('the Unicode section partitions the block', /118/.test(await page.evaluate(() => document.getElementById('uni').textContent)) && /chillu letters/.test(await page.evaluate(() => document.getElementById('uni').textContent)));
  const csv = await (await fetch(`http://127.0.0.1:${PORT}/api/alphabet/export.csv`)).text();
  rec('the CSV export has one row per entry plus the header', csv.trim().split('\n').length === 1 + 576 + 16 + 2 + 6 && /U\+0D36/.test(csv));
  rec('no page errors', perr.length === 0, perr.join('|')); rec('no console errors', cerr.length === 0, cerr.join('|')); rec('no failed requests', bad.length === 0, bad.join('|'));
  await browser.close(); srv.kill('SIGTERM'); for (let i = 0; i < 60 && code === null; i++) await sleep(100);
  rec('server exits cleanly', code === 0, 'exit=' + code + (err ? ' stderr=' + err.slice(0, 120) : '')); rec('port released', await refused(PORT));
  fs.rmSync(tmp, { recursive: true, force: true });
  const fails = results.filter(x => !x).length; console.log('\n' + (results.length - fails) + '/' + results.length + ' passed'); process.exit(fails ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR', e); process.exit(2); });
process.on('exit', () => { try { if (global.__srv) global.__srv.kill('SIGTERM'); } catch (e) {} });
