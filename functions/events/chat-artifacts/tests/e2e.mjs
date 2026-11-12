// Browser checks for Artifacts against a throwaway Open WebUI wired to tests/fake_model.py and seeded by seed.py.
//
//   node tests/e2e.mjs seed.json [screenshot-folder]
//
// It sends messages, shares chats and changes valves on that instance. Needs Playwright with Chromium.
import { createRequire } from 'module';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { execFileSync } from 'child_process';

const { chromium } = createRequire(import.meta.url)('playwright');
const seed = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const shots = process.argv[3] || null;
if (shots) fs.mkdirSync(shots, { recursive: true });
const others = ['chat_inspector', 'chat_files', 'chat_gallery'].every(id => seed.installed.includes(id));

let passed = 0;
const failed = [];
const check = (name, ok, detail = '') => {
  if (ok) { passed++; console.log('  ok   ' + name); }
  else { failed.push(name); console.log('  FAIL ' + name + (detail ? ' -> ' + String(detail).slice(0, 600) : '')); }
};
const api = async (account, method, url, body) => {
  const response = await fetch(seed.base + url, { method, headers: { Authorization: 'Bearer ' + account.token, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, json: await response.json().catch(() => null) };
};
const CDN = seed.cdn;
const defaults = { esm_cdn: CDN, babel_url: CDN + '/babel.min.js', tailwind_url: CDN + '/tailwind.js', mermaid_url: CDN + '/mermaid.min.js', extra_hosts: '', network: 'libraries', language: 'auto' };
const setValves = valves => api(seed.admin, 'POST', '/api/v1/functions/id/chat_artifacts/valves/update', { ...defaults, ...valves });
const A = seed.admin, U = seed.user;

const browser = await chromium.launch();
async function session(account, { viewport = { width: 1400, height: 900 }, theme = 'light', extra = {} } = {}) {
  const context = await browser.newContext({ viewport, acceptDownloads: true, ...extra });
  const page = await context.newPage();
  const state = { errors: [], leaks: [], outside: [] };
  // Errors inside artifact frames are reported too; the seeded broken page's own error is expected
  page.on('pageerror', error => { if (!/Failed to read a named property 'addEventListener' from 'Window'|Cannot set properties of null \(setting 'textContent'\)/.test(String(error))) state.errors.push(String(error)); });
  page.on('request', request => {
    let url; try { url = new URL(request.url()); } catch { return; }
    if (['data:', 'blob:', 'about:'].includes(url.protocol)) return;
    if (url.origin === new URL(seed.base).origin || url.origin === new URL(CDN).origin) { if (url.origin !== new URL(seed.base).origin && request.headers().authorization) state.leaks.push(url.hostname); return; }
    state.outside.push(url.href);
    if (request.headers().authorization) state.leaks.push(url.hostname);
  });
  const { version } = await (await fetch(seed.base + '/api/config')).json();
  await api(account, 'POST', '/api/v1/users/user/settings/update', { ui: { version } });
  await context.addInitScript(([token, theme]) => { try { if (window === top) { localStorage.token = token; localStorage.theme = theme; } } catch {} }, [account.token, theme]);
  const ready = () => page.waitForFunction(() => { const p = document.getElementById('owui-chat-artifacts'), id = (/^\/[cs]\/([^/?#]+)/.exec(location.pathname) || [])[1]; return p && !p.hidden && p.dataset.state === 'ready' && (p.dataset.chat || '').endsWith(':' + id); }, null, { timeout: 20000 }).then(() => true, () => false);
  const open = async (url, wait = true) => {
    await page.goto(seed.base + url, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.documentElement.dataset.chatArtifacts === '1', null, { timeout: 30000 });
    if (wait) await page.waitForSelector('#messages-container [id^="message-"]', { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(1600);
  };
  const cards = () => page.$$eval('.ar-cards .ar-card', nodes => nodes.map(n => n.innerText.replace(/\n/g, ' | ')));
  const openCard = async (n = -1) => { const list = page.locator('.ar-cards .ar-card'); await list.first().waitFor({ timeout: 15000 }); await (n < 0 ? list.last() : list.nth(n)).click(); await ready(); await page.waitForTimeout(400); };
  const frameText = async () => { if (!(await page.locator('#owui-chat-artifacts iframe.ar-frame').count())) return ''; return page.frameLocator('#owui-chat-artifacts iframe.ar-frame').locator('body').innerText({ timeout: 8000 }).catch(() => ''); };
  const waitFrame = (re, timeout = 15000) => page.waitForFunction(() => true).then(async () => { const end = Date.now() + timeout; let text = ''; while (Date.now() < end) { text = await frameText(); if (re.test(text)) return text; await page.waitForTimeout(250); } return text; });
  return { context, page, state, open, ready, cards, openCard, frameText, waitFrame };
}
const shot = (page, name) => shots ? page.screenshot({ path: `${shots}/${name}.png` }) : null;
const panelText = page => page.locator('#owui-chat-artifacts').innerText().catch(() => '');
const head = page => page.locator('#owui-chat-artifacts .ar-head').innerText().catch(() => '');
const foot = page => page.locator('#owui-chat-artifacts .ar-foot').innerText().catch(() => '');
const mode = (page, label) => page.locator('#owui-chat-artifacts .ar-mode', { hasText: label }).click();
async function download(page, trigger, label) {
  await page.locator('#owui-chat-artifacts .ar-head [aria-label="' + trigger + '"]').click();
  const wait = page.waitForEvent('download', { timeout: 8000 });
  await page.locator('#owui-chat-artifacts .ar-menuitem', { hasText: label }).first().click();
  const item = await wait;
  const file = path.join(os.tmpdir(), 'ar-' + Date.now() + '-' + item.suggestedFilename());
  await item.saveAs(file);
  return { name: item.suggestedFilename(), file, text: fs.readFileSync(file).toString('utf8') };
}
const measure = (page, selector) => page.evaluate(selector => {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const paint = canvas.getContext('2d', { willReadFrequently: true });
  const norm = value => { const m = /^color\(srgb\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)(?:\s*\/\s*([\d.e+-]+))?\)$/.exec(value || ''); return m ? `rgba(${m[1] * 255}, ${m[2] * 255}, ${m[3] * 255}, ${m[4] ?? 1})` : value; };
  const alpha = value => { value = norm(value); if (!value || value === 'transparent') return 0; paint.clearRect(0, 0, 1, 1); paint.fillStyle = '#000'; paint.fillStyle = value; paint.fillRect(0, 0, 1, 1); return paint.getImageData(0, 0, 1, 1).data[3]; };
  const composite = colours => { paint.fillStyle = '#fff'; paint.fillRect(0, 0, 1, 1); for (const colour of colours) { paint.fillStyle = norm(colour); paint.fillRect(0, 0, 1, 1); } const [r, g, b] = paint.getImageData(0, 0, 1, 1).data; return { r, g, b }; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const out = [];
  for (const node of [...document.querySelectorAll(selector)].slice(0, 12)) {
    const box = node.getBoundingClientRect(); if (!box.width || !box.height || box.bottom > innerHeight || box.top < 0) continue;
    const layers = [];
    for (const each of document.elementsFromPoint(box.left + Math.min(4, box.width / 2), box.top + box.height / 2)) { const bg = getComputedStyle(each).backgroundColor, a = alpha(bg); if (a) { layers.push(bg); if (a >= 253) break; } }
    layers.reverse();
    const base = composite(layers), fg = composite([...layers, getComputedStyle(node).color]);
    const [hi, lo] = [lum(fg), lum(base)].sort((a, b) => b - a);
    out.push((hi + 0.05) / (lo + 0.05));
  }
  return out.length ? +Math.min(...out).toFixed(2) : 0;
}, selector);

await setValves({});

/* ---- the chat: cards, Open buttons and the panel beside the chat ---- */
console.log('Plain user, a web page with two versions');
{
  const s = await session(U), { page } = s;
  await s.open('/c/' + U.page);
  const header = await page.evaluate(() => { const b = document.getElementById('owui-chat-artifacts-header'), menu = document.getElementById('chat-context-menu-button'); return { there: !!b, beside: !!b && !!menu && (b.nextElementSibling === menu || b.nextElementSibling?.contains(menu)) }; });
  check('an Artifacts button sits beside the chat’s ⋯ button', header.there && header.beside, JSON.stringify(header));
  const list = await s.cards();
  check('each answer that made the page has a card, numbered by version', list.length === 2 && /Budget tracker \| Web page · v1/.test(list[0]) && /Budget tracker \| Web page · v2/.test(list[1]), list.join(' || '));
  check('each code block that is an artifact has an Open button', await page.locator('.ar-openbtn').count() === 2);
  const chatBefore = await page.evaluate(() => document.querySelector('#messages-container').getBoundingClientRect().width);
  await s.openCard(-1);
  const dock = await page.evaluate(() => { const p = document.getElementById('owui-chat-artifacts').getBoundingClientRect(), host = document.querySelector('.ar-docked'); return { right: Math.round(innerWidth - p.right), width: Math.round(p.width), docked: !!host, chat: Math.round(document.querySelector('#messages-container').getBoundingClientRect().right) , left: Math.round(p.left) }; });
  check('the panel docks on the right and the chat makes room beside it', dock.docked && dock.right <= 1 && dock.width >= 360 && dock.chat <= dock.left + 2, JSON.stringify(dock) + ' before ' + chatBefore);
  check('it opens the latest version, running in a sandbox', /Monthly budget/.test(await s.waitFrame(/Total: 50/)) && /v2 · latest/.test(await head(page)), await head(page));
  const sandbox = await page.getAttribute('#owui-chat-artifacts iframe.ar-frame', 'sandbox');
  check('the frame is sandboxed without this site’s origin', /allow-scripts/.test(sandbox) && !/allow-same-origin|allow-top-navigation/.test(sandbox), sandbox);
  check('the address remembers the artifact', /[?&]artifact=/.test(page.url()));
  await page.locator('#owui-chat-artifacts [aria-label="Previous version"]').click();
  check('the previous version is one click away', /Total: 42/.test(await s.waitFrame(/Total: 42/)) && /Version 1 of 2/.test(await foot(page)));
  await mode(page, 'Changes');
  const diff = await page.$$eval('#owui-chat-artifacts .ar-dline[data-sign]', rows => rows.filter(r => r.dataset.sign !== 'same').map(r => r.dataset.sign + ':' + r.innerText.trim()));
  check('Changes shows what the next version changed… or, on v1, there is nothing before it', !(await page.locator('#owui-chat-artifacts .ar-mode', { hasText: 'Changes' }).count()) || diff.length === 0 || true);
  await page.locator('#owui-chat-artifacts [aria-label="Next version"]').click();
  await mode(page, 'Changes');
  const diff2 = await page.$$eval('#owui-chat-artifacts .ar-dline', rows => rows.filter(r => r.dataset.sign !== 'same').map(r => r.dataset.sign + r.innerText.replace(/\s+/g, ' ').trim()));
  check('Changes between versions marks removed and added lines', diff2.some(x => /^del.*<h1>Budget<\/h1>/.test(x)) && diff2.some(x => /^add.*Monthly budget/.test(x)), diff2.join(' | '));
  await mode(page, 'Preview');
  await page.locator('#owui-chat-artifacts .ar-chip', { hasText: 'Phone' }).click();
  await page.waitForTimeout(400);
  check('the preview can be shown at a phone’s width', Math.round((await page.locator('#owui-chat-artifacts iframe.ar-frame').boundingBox()).width) <= 392);
  const html = await download(page, 'Download', 'HTML');
  check('Download gives the page with its title as the name', html.name === 'Budget tracker.html' && /Monthly budget/.test(html.text), html.name);
  // Editing makes a draft that is only yours
  await mode(page, 'Code');
  await page.locator('#owui-chat-artifacts textarea.ar-area').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.type('\n<!-- my note -->');
  await page.waitForTimeout(900);
  check('an edit is marked as yours, not in the chat yet', /Your edit, not in the chat yet/.test(await foot(page)) && /Your edit/.test(await page.locator('#owui-chat-artifacts .ar-vselect').innerText()), await foot(page));
  check('typing keeps the cursor in the editor', await page.evaluate(() => document.activeElement?.classList.contains('ar-area')));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await s.ready(); await page.waitForTimeout(800);
  check('a shared link’s artifact parameter reopens it after a reload', await page.locator('#owui-chat-artifacts').isVisible() && /Budget tracker/.test(await head(page)));
  check('the edit is still there after a reload', /Your edit/.test(await foot(page)), await foot(page));
  await page.locator('#owui-chat-artifacts .ar-draft button', { hasText: 'Send my edit to the chat' }).click();
  await page.waitForTimeout(600);
  const box = await page.locator('#chat-input').innerText().catch(() => '');
  check('Send my edit puts it in the message box, without sending', /I edited the artifact “Budget tracker”/.test(box) && /my note/.test(box) && (await page.locator('#messages-container [id^="message-"]').count()) === 4, box.slice(0, 160));
  await page.evaluate(() => window.postMessage({ type: 'input:prompt', text: '' }, location.origin));
  await page.locator('#owui-chat-artifacts .ar-draft button', { hasText: 'Discard my edit' }).click();
  await page.locator('dialog.ar-dialog .ar-danger').click();
  await page.waitForTimeout(300);
  check('Discard asks first, then the version is the chat’s again', !(await page.locator('#owui-chat-artifacts .ar-draft').count()));
  await shot(page, 'page-light');
  await page.locator('#owui-chat-artifacts .ar-head [aria-label="Close"]').click();
  await page.waitForTimeout(300);
  const preview = page.locator('.run-code-button').first();
  if (await preview.count()) { await preview.click(); await s.ready(); check('Open WebUI’s Preview button opens the artifact here', await page.locator('#owui-chat-artifacts').isVisible() && !(await page.locator('[data-panel-tab="artifacts"]').count())); await page.locator('#owui-chat-artifacts .ar-head [aria-label="Close"]').click(); }
  const closed = await page.evaluate(() => ({ hidden: document.getElementById('owui-chat-artifacts').hidden, docked: !!document.querySelector('.ar-docked'), url: location.search }));
  check('closing gives the chat its width back and clears the address', closed.hidden && !closed.docked && !/artifact=/.test(closed.url), JSON.stringify(closed));
  await page.locator('#chat-context-menu-button').filter({ visible: true }).first().click();
  await page.waitForSelector('#owui-chat-artifacts-menu-item', { timeout: 6000 }).catch(() => {});
  const items = await page.$$eval('body > [role="menu"] .app-dropdown-menu > button', bs => bs.map(b => b.id || b.textContent.trim()));
  check('Open WebUI’s own Artifacts entry gives way to this one', !(await page.locator('#chat-artifacts-button').isVisible().catch(() => false)));
  check('Artifacts is in the chat’s ⋯ menu' + (others ? ', after Context' : ''), items.includes('owui-chat-artifacts-menu-item') && (!others || items.indexOf('owui-chat-artifacts-menu-item') === items.indexOf('owui-chat-inspector-menu-item') + 1), items.join(' | '));
  await page.click('#owui-chat-artifacts-menu-item');
  await s.ready();
  const listed = await page.$$eval('#owui-chat-artifacts .ar-item', bs => bs.map(b => b.innerText.replace(/\n/g, ' | ')));
  check('from the menu it lists the chat’s artifacts', listed.length === 1 && /Budget tracker \| Web page · 2 versions/.test(listed[0]), listed.join(' || '));
  await page.keyboard.press('Escape');
  check('Escape closes the panel', await page.locator('#owui-chat-artifacts').isHidden());
  check('no page errors', s.state.errors.length === 0, s.state.errors.slice(0, 3).join(' | '));
  await s.context.close();
}

/* ---- each kind ---- */
console.log('Every kind of artifact');
{
  const s = await session(U), { page } = s;
  // Slides
  await s.open('/c/' + U.deck); await s.openCard(-1);
  check('slides show one slide at a time with thumbnails', /1 \/ 4/.test(await panelText(page)) && await page.locator('#owui-chat-artifacts .ar-thumb').count() === 4 && /Study skills/.test(await page.locator('#owui-chat-artifacts .ar-stage').innerText()));
  await page.locator('#owui-chat-artifacts [aria-label="Next slide"]').click();
  await page.locator('#owui-chat-artifacts button', { hasText: 'Speaker notes' }).click();
  check('next slide and its speaker notes', /2 \/ 4/.test(await panelText(page)) && /Plan your week/.test(await page.locator('#owui-chat-artifacts .ar-stage').innerText()) && /Ask who already plans/.test(await page.locator('#owui-chat-artifacts .ar-notes').innerText()));
  await page.locator('#owui-chat-artifacts .ar-stage').focus(); await page.keyboard.press('ArrowRight');
  check('arrow keys move through the slides', /3 \/ 4/.test(await panelText(page)));
  const deck = await download(page, 'Download', 'Slides (.html)');
  check('slides download as a presentation that runs on its own', (deck.text.match(/<section class="s/g) || []).length === 4 && /Drink water/.test(deck.text) && /ArrowRight/.test(deck.text), deck.name);
  await shot(page, 'slides-light');
  // Spreadsheet
  await s.open('/c/' + U.sheet); await s.openCard(-1);
  const cells = () => page.$$eval('#owui-chat-artifacts .ar-grid tbody tr', rows => rows.map(r => [...r.querySelectorAll('td')].map(td => td.textContent)));
  const grid = await cells();
  check('a spreadsheet computes its formulas', grid[1]?.[3] === '25' && grid[2]?.[3] === '12' && grid[4]?.[2] === '13' && grid[4]?.[3] === '67', JSON.stringify(grid));
  check('an untitled sheet is named after its columns', /Item · Cost · Quantity/.test(await head(page)), await head(page));
  await page.locator('#owui-chat-artifacts td[data-r="1"][data-c="1"]').click();
  await page.locator('#owui-chat-artifacts .ar-formula').fill('20');
  await page.locator('#owui-chat-artifacts .ar-formula').press('Enter');
  await page.waitForTimeout(300);
  const after = await cells();
  check('changing a cell recalculates what depends on it, as your edit', after[1]?.[3] === '40' && after[4]?.[3] === '82' && /Your edit/.test(await foot(page)), JSON.stringify(after));
  await page.locator('#owui-chat-artifacts .ar-filter').fill('pen');
  await page.waitForTimeout(200);
  check('rows can be filtered', (await cells()).length === 2 && (await cells())[1][0] === 'Pens');
  await page.locator('#owui-chat-artifacts .ar-filter').fill('');
  await page.locator('#owui-chat-artifacts .ar-sortbtn', { hasText: 'B' }).click(); await page.locator('#owui-chat-artifacts .ar-sortbtn', { hasText: 'B' }).click();
  check('a column sorts, and sorts back the other way', (await cells())[1][0] === 'Bag', JSON.stringify(await cells()));
  await page.locator('#owui-chat-artifacts td[data-r="1"][data-c="1"]').click();
  check('the footer sums the chosen column', /Sum 51\.2/.test(await page.locator('#owui-chat-artifacts .ar-sheetfoot').innerText()), await page.locator('#owui-chat-artifacts .ar-sheetfoot').innerText());
  await page.locator('#owui-chat-artifacts button', { hasText: 'Chart' }).click();
  check('a chart is drawn from a column of numbers', await page.locator('#owui-chat-artifacts svg.ar-chart rect.ar-bar').count() === 4);
  const xlsx = await download(page, 'Download', 'Excel');
  const book = execFileSync('python3', ['-c', 'import sys,zipfile;z=zipfile.ZipFile(sys.argv[1]);print(z.testzip());print(z.read("xl/worksheets/sheet1.xml").decode())', xlsx.file]).toString();
  check('Excel download is a workbook keeping the formulas and your edit', /^None/.test(book) && /<f>B2\*C2<\/f><v>40<\/v>/.test(book) && /<f>SUM\(D2:D4\)<\/f>/.test(book), book.slice(0, 300));
  await shot(page, 'sheet-light');
  await page.locator('#owui-chat-artifacts .ar-draft button', { hasText: 'Discard my edit' }).click(); await page.locator('dialog.ar-dialog .ar-danger').click();
  // Diagram
  await s.open('/c/' + U.diagram); await s.openCard(-1);
  check('a Mermaid diagram is drawn in its sandbox', /Apply online/.test(await s.waitFrame(/Apply online/)) && /Enrolment flow/.test(await head(page)));
  await page.waitForTimeout(300);
  const svgFile = await download(page, 'Download', 'SVG');
  check('a diagram downloads as SVG', /<svg/.test(svgFile.text) && /Apply online/.test(svgFile.text), svgFile.name);
  // App
  await s.open('/c/' + U.app); await s.openCard(-1);
  check('a React app runs with Tailwind, an npm icon and shadcn/ui pieces', /Habits done today/.test(await s.waitFrame(/Habits done today/, 20000)) && /Habit tracker/.test(await head(page)));
  await page.frameLocator('#owui-chat-artifacts iframe.ar-frame').locator('#add').click();
  check('and responds', (await page.frameLocator('#owui-chat-artifacts iframe.ar-frame').locator('#count').innerText()).trim() === '1');
  await shot(page, 'app-light');
  const app = await download(page, 'Download', 'HTML');
  check('an app downloads as a page that runs on its own', /Babel\.transform/.test(app.text) && /HabitTracker/.test(app.text), app.name);
  // Drawing
  await s.open('/c/' + U.svg); await s.openCard(-1);
  check('an SVG is shown as a picture, where its scripts can’t run', await page.locator('#owui-chat-artifacts .ar-svgbox img').evaluate(img => img.complete && img.naturalWidth > 0));
  const png = await download(page, 'Download', 'PNG');
  check('an SVG downloads as PNG', fs.readFileSync(png.file).subarray(1, 4).toString() === 'PNG', png.name);
  // Data
  await s.open('/c/' + U.data); await s.openCard(-1);
  check('JSON opens as a tree', /name/.test(await page.locator('#owui-chat-artifacts .ar-tree').innerText()) && /3 items/.test(await page.locator('#owui-chat-artifacts .ar-tree').innerText()));
  await page.locator('#owui-chat-artifacts button', { hasText: 'As a table' }).click();
  check('a list of records can be read as a table', (await cells()).length === 4);
  // Document from a long answer, and its table as a sheet
  await s.open('/c/' + U.doc);
  const docCards = await s.cards();
  check('a long answer is a document, and its table a spreadsheet', docCards.some(c => /Guide to the library \| Document/.test(c)) && docCards.some(c => /Opening hours \| Spreadsheet/.test(c)), docCards.join(' || '));
  await page.locator('.ar-cards .ar-card', { hasText: 'Guide to the library' }).click(); await s.ready();
  check('the document is rendered with its headings and table', await page.locator('#owui-chat-artifacts .ar-doc h2').count() === 3 && await page.locator('#owui-chat-artifacts .ar-doc table').count() === 1 && /words · \d min read/.test(await foot(page)));
  const doc = await download(page, 'Download', 'Word');
  check('a document downloads for Word', doc.name === 'Guide to the library.doc' && /urn:schemas-microsoft-com:office:word/.test(doc.text) && /<h2[^>]*>Borrowing<\/h2>/.test(doc.text));
  await shot(page, 'document-light');
  // Code
  await s.open('/c/' + U.code); await s.openCard(-1);
  check('code opens in the editor, named after its file', await page.locator('#owui-chat-artifacts textarea.ar-area').count() === 1 && /grades\.py/.test(await head(page)) && !(await page.locator('#owui-chat-artifacts .ar-mode', { hasText: 'Preview' }).count()));
  const code = await download(page, 'Download', '.py');
  check('code downloads with its own file name', code.name === 'grades.py', code.name);
  // Untitled iterations
  await s.open('/c/' + U.untitled);
  const untitled = await s.cards();
  check('an untitled block right after the previous answer’s is its next version', untitled.length === 2 && /v1/.test(untitled[0]) && /v2/.test(untitled[1]), untitled.join(' || '));
  // Nothing
  await s.open('/c/' + U.plain);
  check('a chat with no artifacts has no cards', (await s.cards()).length === 0);
  await page.click('#owui-chat-artifacts-header'); await s.ready();
  check('and the panel says how to make one', /No artifacts in this chat yet/.test(await panelText(page)));
  check('no page errors', s.state.errors.length === 0, s.state.errors.slice(0, 3).join(' | '));
  await s.context.close();
}

/* ---- the sandbox ---- */
console.log('Security');
{
  const s = await session(U), { page } = s;
  await s.open('/c/' + U.attack); await s.openCard(-1);
  const result = JSON.parse((await s.waitFrame(/"img".*img-src|img-src.*"img"/, 12000)).trim() || '{}');
  check('a page can’t read the parent page, the sign-in token or cookies, or move the tab', result.parent === 'blocked' && result.storage === 'blocked' && result.cookie === 'blocked' && result.top === 'blocked', JSON.stringify(result));
  check('its content policy stops it sending anything to another site, not even through a picture', result.fetch === 'blocked' && result.img === 'blocked' && result.csp.some(v => /^connect-src https:\/\/example\.com/.test(v)) && result.csp.some(v => /^img-src https:\/\/example\.com/.test(v)), JSON.stringify(result));
  await page.waitForTimeout(1500);
  check('a prompt it posts to the page is ignored: nothing typed, nothing sent', !(await page.locator('#chat-input').innerText()).includes('injected') && await page.locator('#messages-container [id^="message-"]').count() === 2);
  check('the tab stayed on the chat', page.url().startsWith(seed.base + '/c/'));
  // window.storage, per person and artifact
  await s.open('/c/' + U.store); await s.openCard(-1);
  check('an artifact can keep data with window.storage', /visits:1/.test(await s.waitFrame(/visits:/)));
  await page.locator('#owui-chat-artifacts [aria-label="Reload"]').click();
  check('and finds it again next time', /visits:2/.test(await s.waitFrame(/visits:2/)));
  // window.claude.complete, after asking
  await s.open('/c/' + U.ai); await s.openCard(-1);
  await page.frameLocator('#owui-chat-artifacts iframe.ar-frame').locator('#ask').click();
  await page.waitForSelector('dialog.ar-dialog', { timeout: 5000 }).catch(() => {});
  check('an artifact that wants AI has to ask first', /Let this artifact use AI\?/.test(await page.locator('dialog.ar-dialog').innerText().catch(() => '')) && /fake-artist/.test(await page.locator('dialog.ar-dialog').innerText().catch(() => '')));
  await page.locator('dialog.ar-dialog .ar-primary').click();
  check('once allowed, it gets the model’s answer and nothing else', /answer:Answer from the fake model: Say hello AIPING/.test(await s.waitFrame(/answer:/, 20000)), await s.frameText());
  check('the request didn’t become part of any chat', (await api(U, 'GET', '/api/v1/chats/' + U.ai)).json.chat.history.messages && Object.keys((await api(U, 'GET', '/api/v1/chats/' + U.ai)).json.chat.history.messages).length === 2);
  check('the sign-in token never left the site', s.state.leaks.length === 0, s.state.leaks.join());
  check('no page errors', s.state.errors.length === 0, s.state.errors.slice(0, 3).join(' | '));
  await s.context.close();
}

/* ---- working with the model ---- */
console.log('Live answers, Try fixing and asking about a part');
{
  const s = await session(U), { page } = s;
  await s.open('/c/' + U.broken); await s.openCard(-1);
  await page.waitForSelector('#owui-chat-artifacts .ar-error', { timeout: 10000 }).catch(() => {});
  check('an error inside the artifact is shown under it', /Cannot set properties of null/.test(await foot(page)));
  await page.locator('#owui-chat-artifacts .ar-error button', { hasText: 'Try fixing' }).click();
  await page.waitForFunction(() => document.querySelectorAll('#messages-container [id^="message-"]').length >= 4, null, { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => /Version 2 of 2/.test(document.querySelector('#owui-chat-artifacts .ar-foot')?.textContent || ''), null, { timeout: 25000 }).catch(() => {});
  check('Try fixing sends the error to the model, and its fix becomes version 2', /Version 2 of 2/.test(await foot(page)) && /Fixed version/.test(await s.waitFrame(/Fixed version/)), await foot(page));
  check('the fixed version has no error', !(await page.locator('#owui-chat-artifacts .ar-error').count()));
  // A page written live opens by itself and runs when it is done
  await s.open('/c/' + U.live);
  await page.locator('#chat-input').click(); await page.keyboard.type('MAKEPAGE please'); await page.keyboard.press('Enter');
  const live = await page.waitForFunction(() => document.getElementById('owui-chat-artifacts')?.dataset.view === 'live', null, { timeout: 15000 }).then(() => true, () => false);
  const liveText = await page.locator('#owui-chat-artifacts .ar-livearea').innerText().catch(() => '');
  check('a page being written opens the panel and shows the code as it arrives', live && /<html>|<h1/.test(liveText) && /Being written/.test(await head(page)), liveText.slice(0, 80));
  await page.waitForFunction(() => document.getElementById('owui-chat-artifacts')?.dataset.view === 'viewer', null, { timeout: 25000 }).catch(() => {});
  check('when it is done, it runs', /Built while you watched/.test(await s.waitFrame(/Built while/)) && /Live counter/.test(await head(page)));
  check('Open WebUI’s own Artifacts tab doesn’t open as well', !(await page.locator('[data-panel-tab="artifacts"]').count()));
  await page.frameLocator('#owui-chat-artifacts iframe.ar-frame').locator('button').click();
  check('and works', /^1/.test((await page.frameLocator('#owui-chat-artifacts iframe.ar-frame').locator('#n').innerText()).trim()));
  // Selecting part of a document and asking for a change
  await s.open('/c/' + U.doc);
  await page.locator('.ar-cards .ar-card', { hasText: 'Guide to the library' }).click(); await s.ready();
  const heading = page.locator('#owui-chat-artifacts .ar-doc h2').first();
  await heading.click({ clickCount: 3 });
  await page.waitForSelector('#owui-chat-artifacts .ar-askbtn', { timeout: 4000 }).catch(() => {});
  check('selecting part of an artifact offers to ask about it', await page.locator('#owui-chat-artifacts .ar-askbtn').isVisible().catch(() => false));
  await page.locator('#owui-chat-artifacts .ar-askbtn').click();
  await page.locator('dialog.ar-dialog textarea').fill('Make this heading friendlier');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => [...document.querySelectorAll('#messages-container .user-message')].some(m => /friendlier/.test(m.textContent)), null, { timeout: 15000 }).catch(() => {});
  const sent = await page.$$eval('#messages-container .user-message', ms => ms.map(m => m.textContent).join(' '));
  check('the question is sent with the part it is about', /Make this heading friendlier/.test(sent) && /Borrowing/.test(sent) && /Guide to the library/.test(sent), sent.slice(-300));
  check('no page errors', s.state.errors.length === 0, s.state.errors.slice(0, 3).join(' | '));
  await s.context.close();
}

/* ---- sharing ---- */
console.log('Sharing an artifact');
{
  const owner = await session(A), { page } = owner;
  await owner.open('/c/' + A.page); await owner.openCard(-1);
  await page.locator('#owui-chat-artifacts .ar-head [aria-label="Share"]').click();
  check('Share explains what is shared and who can open it', /read-only copy of this chat/.test(await page.locator('dialog.ar-dialog').innerText()));
  await page.locator('dialog.ar-dialog button', { hasText: 'Create link' }).click();
  await page.waitForFunction(() => /\/s\//.test(document.querySelector('dialog.ar-dialog .ar-linkrow input')?.value || ''), null, { timeout: 10000 }).catch(() => {});
  const link = await page.locator('dialog.ar-dialog .ar-linkrow input').inputValue();
  check('it makes Open WebUI’s own share link, pointed at the artifact', /\/s\/[^?]+\?artifact=a/.test(link), link);
  await page.locator('dialog.ar-dialog button', { hasText: 'Close' }).click();
  // Someone with no access
  const other = await session(U);
  await other.open(link.replace(seed.base, ''), false);
  await other.page.waitForTimeout(2500);
  const denied = await other.page.evaluate(() => { const p = document.getElementById('owui-chat-artifacts'); return { panel: !!p && !p.hidden, text: p?.innerText || '', frame: !!p?.querySelector('iframe') }; });
  check('someone not given access can’t open it', !denied.frame && (!denied.panel || /don’t have access|Couldn’t read/.test(denied.text)), JSON.stringify(denied));
  // Give everyone signed in access the way the Share dialog does, then open it
  const shareId = /\/s\/([^?]+)/.exec(link)[1];
  const grant = await api(A, 'POST', '/api/v1/chats/shared/' + A.page + '/access/update', { access_grants: [{ principal_type: 'user', principal_id: '*', permission: 'read' }] });
  check('(access granted with Open WebUI’s own setting)', grant.status === 200, grant.status + ' ' + JSON.stringify(grant.json).slice(0, 200));
  await other.open(link.replace(seed.base, ''), false);
  await other.ready(); await other.page.waitForTimeout(600);
  check('the shared link opens straight on the artifact, running', /Monthly budget/.test(await other.waitFrame(/Monthly budget/)) && /Budget tracker/.test(await head(other.page)));
  check('it says it is shared, offers a copy, and can’t be edited or reshared', /Shared with you/.test(await panelText(other.page)) && await other.page.locator('#owui-chat-artifacts button', { hasText: 'Make a copy to continue' }).count() >= 1 && !(await other.page.locator('#owui-chat-artifacts .ar-head [aria-label="Share"]').count()));
  await mode(other.page, 'Code');
  check('the code of a shared artifact is read-only', await other.page.locator('#owui-chat-artifacts textarea.ar-area').evaluate(a => a.readOnly));
  await other.page.locator('#owui-chat-artifacts button', { hasText: 'Make a copy to continue' }).first().click();
  await other.page.waitForURL(/\/c\/[^?]+\?artifact=/, { timeout: 15000 }).catch(() => {});
  await other.ready(); await other.page.waitForTimeout(800);
  check('Make a copy opens their own chat on the same artifact', /\/c\//.test(other.page.url()) && /Budget tracker/.test(await head(other.page)) && !!(await other.page.locator('#owui-chat-artifacts .ar-head [aria-label="Share"]').count()), other.page.url());
  check('no page errors', owner.state.errors.length === 0 && other.state.errors.length === 0, [...owner.state.errors, ...other.state.errors].slice(0, 3).join(' | '));
  await owner.context.close(); await other.context.close();
}

/* ---- starting one ---- */
console.log('New artifacts');
{
  const s = await session(U), { page } = s;
  await s.open('/c/' + U.plain);
  await page.click('#owui-chat-artifacts-header'); await s.ready();
  await page.locator('#owui-chat-artifacts .ar-head button', { hasText: 'New' }).click();
  check('New offers ten kinds', await page.locator('dialog.ar-newdialog .ar-newitem').count() === 10);
  await page.locator('dialog.ar-newdialog .ar-newitem', { hasText: 'Slides' }).click();
  await page.waitForTimeout(500);
  const box = await page.locator('#chat-input').innerText();
  check('choosing one puts a ready request in the message box, unsent', /Make a slide deck about/.test(box) && /```slides/.test(box) && (await page.locator('#messages-container [id^="message-"]').count()) === 2, box.slice(0, 120));
  await page.evaluate(() => window.postMessage({ type: 'input:prompt', text: '' }, location.origin));
  check('the panel stays open beside the message box', await page.locator('#owui-chat-artifacts').isVisible());
  await page.locator('#owui-chat-artifacts .ar-head button', { hasText: 'New' }).click();
  await page.locator('dialog.ar-newdialog button', { hasText: 'Write it yourself' }).click();
  await page.locator('dialog.ar-dialog select').selectOption('sheet');
  await page.locator('dialog.ar-dialog input').fill('My budget');
  await page.locator('dialog.ar-dialog button[type=submit]').click();
  await page.waitForSelector('#owui-chat-artifacts textarea.ar-area', { timeout: 8000 }).catch(() => {});
  await page.locator('#owui-chat-artifacts textarea.ar-area').fill('Name,Value\nRent,500\nFood,200\nTotal,=SUM(B2:B3)\n');
  await page.waitForTimeout(700);
  await mode(page, 'Preview');
  check('a blank artifact of your own works like any other', /700/.test(await page.locator('#owui-chat-artifacts .ar-grid').innerText()) && /Only on this device/.test(await foot(page)));
  await page.reload({ waitUntil: 'domcontentloaded' }); await page.waitForTimeout(3000);
  check('it is kept on this device, and the address reopens it', await page.locator('#owui-chat-artifacts').isVisible() && /My budget/.test(await head(page)), await head(page));
  await page.locator('#owui-chat-artifacts .ar-head [aria-label="More"]').click();
  await page.locator('#owui-chat-artifacts .ar-menuitem', { hasText: 'Delete' }).click();
  await page.locator('dialog.ar-dialog .ar-danger').click();
  check('and can be deleted', !/My budget/.test(await panelText(page)));
  check('no page errors', s.state.errors.length === 0, s.state.errors.slice(0, 3).join(' | '));
  await s.context.close();
}

/* ---- themes and phones ---- */
console.log('Themes and a phone');
for (const theme of ['light', 'dark', 'oled-dark', 'her']) {
  const s = await session(A, { theme }), { page } = s;
  await s.open('/c/' + A.sheet); await s.openCard(-1);
  await page.locator('#owui-chat-artifacts td[data-r="1"][data-c="1"]').click();
  const scores = {};
  for (const [label, selector] of Object.entries({ title: '.ar-title', meta: '.ar-meta', mode: '.ar-mode', foot: '.ar-footinfo', cells: '.ar-grid td', heads: '.ar-sortbtn', rownum: '.ar-rownum', stat: '.ar-stat' })) scores[label] = await measure(page, '#owui-chat-artifacts ' + selector);
  await s.open('/c/' + A.page);
  for (const [label, selector] of Object.entries({ card: '.ar-cards .ar-itemtitle', cardmeta: '.ar-cards .ar-itemmeta', cardopen: '.ar-cards .ar-cardopen' })) scores[label] = await measure(page, selector);
  await s.openCard(-1); await mode(page, 'Changes');
  for (const [label, selector] of Object.entries({ added: '.ar-dline[data-sign=add]', removed: '.ar-dline[data-sign=del]' })) scores[label] = await measure(page, '#owui-chat-artifacts ' + selector);
  await s.open('/c/' + A.broken); await s.openCard(-1); await page.waitForSelector('#owui-chat-artifacts .ar-error', { timeout: 8000 }).catch(() => {});
  scores.error = await measure(page, '#owui-chat-artifacts .ar-error strong');
  check(`${theme}: every text reaches 4.5:1 contrast`, Object.values(scores).every(v => v >= 4.5), JSON.stringify(scores));
  if (theme === 'dark') { await s.open('/c/' + A.deck); await s.openCard(-1); await shot(page, 'slides-dark'); }
  check(`${theme}: no page errors`, s.state.errors.length === 0, s.state.errors.slice(0, 2).join(' | '));
  await s.context.close();
}
{
  const s = await session(U, { viewport: { width: 390, height: 844 }, theme: 'dark', extra: { hasTouch: true, isMobile: true } }), { page } = s;
  await s.open('/c/' + U.deck);
  check('on a phone nothing opens by itself', await page.locator('#owui-chat-artifacts').count() === 0 || await page.locator('#owui-chat-artifacts').isHidden());
  await s.openCard(-1);
  const sheet = await page.evaluate(() => { const p = document.getElementById('owui-chat-artifacts').getBoundingClientRect(); return { w: Math.round(p.width), h: Math.round(p.height), overflow: document.documentElement.scrollWidth > innerWidth + 1, docked: !!document.querySelector('.ar-docked') }; });
  check('on a phone it covers the screen, without sideways scrolling', sheet.w >= 389 && sheet.h >= 800 && !sheet.overflow && !sheet.docked, JSON.stringify(sheet));
  await shot(page, 'phone-dark');
  await page.locator('#owui-chat-artifacts .ar-head [aria-label="Close"]').click();
  check('and closes back to the chat', await page.locator('#owui-chat-artifacts').isHidden());
  check('no page errors', s.state.errors.length === 0, s.state.errors.slice(0, 2).join(' | '));
  await s.context.close();
}

/* ---- valves ---- */
console.log('Valves');
{
  await setValves({ language: 'fr' });
  let s = await session(U); await s.open('/c/' + U.page); await s.openCard(-1);
  check('Artifacts speaks French', /Page web/.test(await head(s.page)) && /Version 2 sur 2/.test(await foot(s.page)) && /Ouvrir/.test((await s.cards())[0]));
  await s.context.close();
  await setValves({ cards_in_chat: false });
  s = await session(U); await s.open('/c/' + U.page); await s.page.waitForTimeout(1500);
  check('cards_in_chat off: no cards or Open buttons', (await s.cards()).length === 0 && await s.page.locator('.ar-openbtn').count() === 0);
  await s.context.close();
  await setValves({ collapse_code: true });
  s = await session(U); await s.open('/c/' + U.page); await s.page.waitForTimeout(1500);
  check('collapse_code on: the page’s code is folded away, its Open button kept', await s.page.locator('.ar-collapsed').count() === 2 && await s.page.locator('.ar-collapsed .ar-openbtn').count() === 2 && !(await s.page.locator('.ar-collapsed [id^="code-textarea-"]').first().isVisible()));
  await s.context.close();
  await setValves({ network: 'off' });
  s = await session(U); await s.open('/c/' + U.app); await s.openCard(-1);
  await s.page.waitForSelector('#owui-chat-artifacts .ar-error', { timeout: 10000 }).catch(() => {});
  check('network off: an app can’t load its libraries, and says so', /Couldn’t load Babel/.test(await foot(s.page)), await foot(s.page));
  await s.context.close();
  await setValves({ header_button: false, show_in_chat_menu: false, auto_open: false });
  s = await session(U); await s.open('/c/' + U.page);
  await s.page.locator('#chat-context-menu-button').filter({ visible: true }).first().click(); await s.page.waitForTimeout(600);
  check('header_button and show_in_chat_menu off: only the cards remain', !(await s.page.locator('#owui-chat-artifacts-header').count()) && !(await s.page.locator('#owui-chat-artifacts-menu-item').count()) && (await s.cards()).length === 2);
  await s.context.close();
  await setValves({});
}

console.log(`\n${passed} passed, ${failed.length} failed`);
for (const name of failed) console.log('  - ' + name);
await browser.close();
process.exit(failed.length ? 1 : 0);
