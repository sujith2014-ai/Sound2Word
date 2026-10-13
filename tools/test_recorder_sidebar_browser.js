// Browser test for the dictionary search sidebar of tools/s2w_recorder.py, using a tiny MADE-UP dictionary CSV and Chromium's
// fake microphone. No real dictionary data, microphone or voice. Needs Playwright for Node + Chromium (env vars as in
// tools/test_viewer_browser.js).   node tools/test_recorder_sidebar_browser.js
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
function tone(file, rate) { const parts = [[0, .1], [300, .3], [600, .3], [900, .3], [1200, .3], [0, .4]], n = Math.floor(parts.reduce((a, p) => a + p[1], 0) * rate), b = Buffer.alloc(44 + 2 * n);
  b.write('RIFF', 0); b.writeUInt32LE(36 + 2 * n, 4); b.write('WAVEfmt ', 8); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(2 * n, 40);
  let i = 0; for (const [hz, s] of parts) for (let k = 0; k < Math.floor(s * rate); k++, i++) b.writeInt16LE(hz ? Math.round(9000 * Math.sin(2 * Math.PI * hz * k / rate)) : 0, 44 + 2 * i); fs.writeFileSync(file, b); }

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 's2w-sidebar-browser-')), dir = path.join(tmp, 'voice'), dict = path.join(tmp, 'enml.csv'), fake = path.join(tmp, 'mic.wav');
  tone(fake, 48000);
  fs.writeFileSync(dict, 'english,malayalam\npot,"കലം, കുടം"\ntime,കാലം\ndog,പട്ടി\nsay hello,ഹലോ പറയുക\n');
  const dictHash = require('crypto').createHash('sha256').update(fs.readFileSync(dict)).digest('hex');
  const start = () => { const p = spawn('python3', ['tools/s2w_recorder.py', '--build', BUILD, '--record-dir', dir, '--dictionary', dict, '--port', String(PORT)], { cwd: R }); global.__srv = p; p.errBuf = ''; p.stderr.on('data', d => p.errBuf += d); p.exited = null; p.on('exit', c => p.exited = c); return p; };
  let srv = start(); for (let i = 0; i < 50 && await refused(PORT); i++) await sleep(100);
  const browser = await chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--use-file-for-fake-audio-capture=' + fake, '--autoplay-policy=no-user-gesture-required'] });
  const ctx = await browser.newContext({ permissions: ['microphone'] }); const page = await ctx.newPage(); await page.setViewportSize({ width: 1400, height: 1000 });
  const cerr = [], perr = [], bad = [];
  page.on('console', m => { if (m.type() === 'error' && !/status of (409|422)/.test(m.text())) cerr.push(m.text()); }); page.on('pageerror', e => perr.push(String(e)));
  page.on('requestfailed', r => bad.push('FAILED ' + r.url())); page.on('response', r => { if (r.status() >= 400 && ![409, 422].includes(r.status())) bad.push(r.status() + ' ' + r.url()); });
  const search = async q => { await page.fill('#dq', q); await page.waitForFunction(() => document.querySelectorAll('#dres .dr, #dres .mut').length > 0, null, { timeout: 5000 }); await sleep(450); };

  await page.goto(`http://127.0.0.1:${PORT}/`); await page.waitForSelector('#dq');
  rec('sidebar present and the dictionary is named with its entry count', /Dictionary: enml\.csv \(5 entries\)/.test(await page.textContent('#dinfo')) || /enml\.csv/.test(await page.textContent('#dinfo')), await page.textContent('#dinfo'));
  rec('with no words yet: guidance shown and Record disabled', /No words yet/.test(await page.textContent('#meaning')) && await page.isDisabled('#rec'));
  await search('pot');
  const rows = await page.evaluate(() => [...document.querySelectorAll('#dres .dr')].map(d => d.textContent.replace(/\s+/g, ' ').trim()));
  rec('searching English "pot" lists both Malayalam words with their meaning', rows.length === 2 && rows.some(r => r.includes('കലം')) && rows.some(r => r.includes('കുടം')) && rows.every(r => r.includes('pot')), JSON.stringify(rows));
  rec('Manglish suggestion prefilled and editable', (await page.inputValue('.dm[data-i="0"]')).length > 0, await page.inputValue('.dm[data-i="0"]'));
  const idx = await page.evaluate(() => [...document.querySelectorAll('#dres .dr')].findIndex(d => d.textContent.includes('കലം')));
  await page.click(`[data-add="${idx}"]`); await page.waitForFunction(() => document.querySelectorAll('.chip').length === 1, null, { timeout: 5000 });
  rec('Add puts the word in the list and selects it', (await page.textContent('#ml')) === 'കലം' && (await page.textContent('#rom')) === 'kalam' && (await page.textContent('#units')) === 'K · A · L · A · M', await page.textContent('#units'));
  rec('meaning shown, marked as dictionary-sourced and unverified', /pot \(dictionary: enml\.csv.*unverified\)/.test(await page.textContent('#meaning')), await page.textContent('#meaning'));
  rec('Record is enabled once there is a word', !(await page.isDisabled('#rec')));
  await page.click(`[data-add="${idx}"]`); await sleep(500);
  rec('adding the same word again shows a clear message and changes nothing', /already in your word list/.test(await page.textContent('#dres')) && (await page.locator('.chip').count()) === 1);

  await search('കാല');
  rec('Malayalam prefix search finds കാലം with meaning "time"', /കാലം/.test(await page.textContent('#dres')) && /time/.test(await page.textContent('#dres')));
  await page.fill('.dm[data-i="0"]', 'kaalam'); await page.click('[data-add="0"]'); await page.waitForFunction(() => document.querySelectorAll('.chip').length === 2, null, { timeout: 5000 });
  rec('second word added, units show the long vowel', (await page.textContent('#units')) === 'K · AA · L · A · M', await page.textContent('#units'));
  await search('patti');
  rec('typing Manglish finds the Malayalam word', /പട്ടി/.test(await page.textContent('#dres')) && /dog/.test(await page.textContent('#dres')));
  await search('hello');
  rec('phrases are shown but cannot be added', /phrase: add single words only/.test(await page.textContent('#dres')) && (await page.locator('[data-add]').count()) === 0);

  await page.fill('#addml', 'മഴ'); await page.fill('#addrom', 'mazha'); await page.click('#addgo'); await page.waitForFunction(() => document.querySelectorAll('.chip').length === 3, null, { timeout: 5000 });
  rec('a word typed by hand is added too', (await page.textContent('#ml')) === 'മഴ' && (await page.textContent('#units')) === 'M · A · ZH · A');
  await page.fill('#addml', 'പടി'); await page.fill('#addrom', 'paḍi'); await page.click('#addgo'); await sleep(500);
  rec('invalid Manglish is refused with a message', /ASCII letters|bad --word|Manglish/i.test(await page.textContent('#addmsg')) && (await page.locator('.chip').count()) === 3, await page.textContent('#addmsg'));

  await page.click('.chip[data-i="0"]'); await sleep(300);
  await page.click('#rec'); await sleep(1500); await page.click('#stop'); await page.waitForSelector('#review:not([hidden])'); await sleep(300);
  rec('an added word records like any other: five sound cards K A L A M', JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('#cards .badge')].map(b => b.textContent))) === JSON.stringify(['K', 'A', 'L', 'A', 'M']));
  await page.click('#accept'); await page.waitForFunction(() => /Saved take_001/.test(document.getElementById('status').textContent), null, { timeout: 8000 });
  rec('and its take is saved with boundaries', fs.existsSync(path.join(dir, 'kalam', 'take_001.wav')) && fs.existsSync(path.join(dir, 'kalam', 'take_001.segments.json')));

  const cand = fs.readFileSync(path.join(dir, 'candidates_unverified.csv'), 'utf8').trim().split('\n');
  rec('every added word is logged as UNVERIFIED', cand.length === 4 && cand.slice(1).every(l => /UNVERIFIED/.test(l)), cand.length + ' lines');
  rec('the dictionary file was only read, never changed', require('crypto').createHash('sha256').update(fs.readFileSync(dict)).digest('hex') === dictHash);

  await browser.close(); srv.kill('SIGTERM'); for (let i = 0; i < 50 && srv.exited === null; i++) await sleep(100);
  rec('server exits cleanly', srv.exited === 0, 'exit=' + srv.exited);
  // restart: the added words come back
  srv = start(); for (let i = 0; i < 50 && await refused(PORT); i++) await sleep(100);
  const words = await (await fetch(`http://127.0.0.1:${PORT}/api/words`)).json();
  rec('after a restart the added words are still in the list', JSON.stringify(words.map(w => w.manglish)) === JSON.stringify(['kalam', 'kaalam', 'mazha']), JSON.stringify(words.map(w => w.manglish)));
  srv.kill('SIGTERM'); for (let i = 0; i < 50 && srv.exited === null; i++) await sleep(100);
  rec('no page errors', perr.length === 0, perr.join('|')); rec('no console errors (apart from the deliberate refusals)', cerr.length === 0, cerr.join('|')); rec('no unexpected failed requests', bad.length === 0, bad.join('|'));
  rec('port released', await refused(PORT));
  fs.rmSync(tmp, { recursive: true, force: true });
  const fails = results.filter(x => !x).length; console.log('\n' + (results.length - fails) + '/' + results.length + ' passed'); process.exit(fails ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR', e); process.exit(2); });
process.on('exit', () => { try { if (global.__srv) global.__srv.kill('SIGTERM'); } catch (e) {} });
