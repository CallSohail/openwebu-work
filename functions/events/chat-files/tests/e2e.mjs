// Browser checks for Chat Files against a throwaway Open WebUI seeded by seed.py.
//
//   python tests/seed.py http://127.0.0.1:8080 chat_files.py ../chat-gallery/chat_gallery.py > seed.json
//   node tests/e2e.mjs seed.json [screenshot-folder]
//
// It renames and deletes files and changes valves on that instance. Needs Playwright with Chromium.
import { createRequire } from 'module';
import fs from 'fs';

const { chromium } = createRequire(import.meta.url)('playwright');
const seed = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const shots = process.argv[3] || null;
if (shots) fs.mkdirSync(shots, { recursive: true });

let passed = 0;
const failed = [];
const check = (name, ok, detail = '') => {
  if (ok) { passed++; console.log('  ok   ' + name); }
  else { failed.push(name); console.log('  FAIL ' + name + (detail ? ' -> ' + String(detail).slice(0, 400) : '')); }
};
const api = async (account, method, path, body) => {
  const response = await fetch(seed.base + path, {
    method, headers: { Authorization: 'Bearer ' + account.token, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, json: await response.json().catch(() => null) };
};
const setValves = valves => api(seed.admin, 'POST', '/api/v1/functions/id/chat_files/valves/update', valves);
const defaults = { max_zip_mb: 300, include_images: 'auto', show_references: true, allow_rename: true, allow_delete: true, header_button: true, button_beside_menu: false, default_view: 'list', thumbnails: true, language: 'auto' };
const U = seed.user, F = U.files, E = U.expect;
const refs = E.chat_refs;
// Without Chat Gallery the chat's photo is listed too
const photo = seed.gallery ? 0 : 1;
const chatRows = E.chat_files + photo + refs;

const browser = await chromium.launch();
async function session(account, { viewport = { width: 1400, height: 900 }, theme = 'light', extra = {} } = {}) {
  const context = await browser.newContext({ viewport, acceptDownloads: true, ...extra });
  const page = await context.newPage();
  const state = { errors: [], leaks: [], uploads: 0 };
  page.on('pageerror', error => state.errors.push(String(error)));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin === new URL(seed.base).origin) { if (request.method() === 'POST' && /\/api\/v1\/files\/?$/.test(url.pathname)) state.uploads++; return; }
    if (url.protocol === 'data:' || url.protocol === 'blob:') return;
    const headers = request.headers();
    if (headers.authorization || headers.cookie) state.leaks.push(url.hostname);
  });
  const { version } = await (await fetch(seed.base + '/api/config')).json();
  await api(account, 'POST', '/api/v1/users/user/settings/update', { ui: { version } });
  await context.request.post(seed.base + '/api/v1/auths/signin', { data: { email: account.email, password: seed.password } });
  await context.addInitScript(([token, theme]) => { localStorage.token = token; localStorage.theme = theme; }, [account.token, theme]);
  const open = async path => {
    await page.goto(seed.base + path, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.documentElement.dataset.chatFiles === '1', null, { timeout: 30000 });
    await page.waitForSelector('#chat-input', { timeout: 30000 });
    await page.waitForSelector('#owui-chat-files-header', { timeout: 15000 }).catch(() => {});
  };
  return { context, page, state, open };
}
const shot = (page, name) => shots ? page.screenshot({ path: `${shots}/${name}.png` }) : null;
// Files is chosen from the tab row's + menu where there is one, and from its own button elsewhere
const plusOf = page => page.locator('#controls-container button[aria-label="New tab"]').first();
const openFiles = async page => {
  if (await page.locator('#owui-chat-files-header').isVisible().catch(() => false)) { await page.click('#owui-chat-files-header'); return; }
  await plusOf(page).click();
  await page.click('#owui-chat-files-button');
};
const html = (page, name) => page.evaluate(name => document.documentElement.dataset[name], name);
const rowsOf = page => page.locator('.cf-list .cf-row');
const foot = page => page.locator('.cf-foot').innerText().catch(() => 'closed');
const settle = (page, count, timeout = 20000) => page.waitForFunction(n => document.querySelectorAll('.cf-list .cf-row').length === n && !/Collecting|Loading/.test(document.querySelector('.cf-foot')?.textContent || ''), count, { timeout }).then(() => true, () => false);
const names = page => page.$$eval('.cf-list .cf-name', nodes => nodes.map(node => node.textContent));
const openRow = async (page, name) => {
  await page.locator('.cf-list .cf-row', { has: page.locator('.cf-name', { hasText: name }) }).first().click();
  await page.waitForSelector('.cf-viewer.cf-open');
  await page.waitForFunction(() => !document.querySelector('.cf-stage.cf-loading'), null, { timeout: 30000 }).catch(() => {});
};
const back = page => page.click('.cf-vbar [aria-label="Back to files"]');
const stageText = page => page.locator('.cf-stage').innerText();
const facts = page => page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.cf-facts dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent])));
const visible = (page, label) => page.locator(`.cf-vbar [aria-label="${label}"]`).isVisible();

// Contrast of a text element against what is really painted under it, our own layers and the page's included.
// A canvas does the colour work: Open WebUI's colours are oklch(), and the canvas composites the layers exactly.
const contrastOf = (page, selector) => page.evaluate(selector => {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const paint = canvas.getContext('2d', { willReadFrequently: true });
  // color-mix() comes back as color(srgb r g b / a), which canvas fillStyle ignores, so it is turned into rgba() first
  const norm = value => { const m = /^color\(srgb\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)(?:\s*\/\s*([\d.e+-]+))?\)$/.exec(value || ''); return m ? `rgba(${m[1] * 255}, ${m[2] * 255}, ${m[3] * 255}, ${m[4] ?? 1})` : value; };
  const visible = value => { value = norm(value); if (!value || value === 'transparent') return false; paint.clearRect(0, 0, 1, 1); paint.fillStyle = '#000'; paint.fillStyle = value; paint.fillRect(0, 0, 1, 1); return paint.getImageData(0, 0, 1, 1).data[3] > 0; };
  const composite = colours => { paint.globalCompositeOperation = 'source-over'; paint.fillStyle = '#fff'; paint.fillRect(0, 0, 1, 1); for (const colour of colours) { paint.fillStyle = norm(colour); paint.fillRect(0, 0, 1, 1); } const [r, g, b] = paint.getImageData(0, 0, 1, 1).data; return { r, g, b }; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const out = [];
  for (const node of [...document.querySelectorAll(selector)].slice(0, 12)) {
    // Only what is on screen: off screen nothing is painted, so there is nothing to measure against
    const box = node.getBoundingClientRect(); if (!box.width || !box.height || box.bottom > innerHeight || box.top < 0 || box.right > innerWidth) continue;
    const stack = document.elementsFromPoint(box.left + Math.min(4, box.width / 2), box.top + box.height / 2);
    const layers = [];
    for (const each of stack) {
      if (each.closest('[data-cf-covered],[data-cg-covered]')) continue;
      const bg = getComputedStyle(each).backgroundColor;
      if (visible(bg)) { layers.push(bg); paint.clearRect(0, 0, 1, 1); paint.fillStyle = norm(bg); paint.fillRect(0, 0, 1, 1); if (paint.getImageData(0, 0, 1, 1).data[3] >= 253) break; }
    }
    layers.reverse();
    const base = composite(layers), fg = composite([...layers, getComputedStyle(node).color]);
    const [hi, lo] = [lum(fg), lum(base)].sort((a, b) => b - a);
    out.push((hi + 0.05) / (lo + 0.05));
  }
  return out.length ? Math.min(...out) : 0;
}, selector);

/* ---- a plain user on a wide screen, with Chat Gallery installed too ---- */
console.log('Plain user, wide screen' + (seed.gallery ? ', with Chat Gallery' : ''));
{
  const { context, page, state, open } = await session(U, { extra: { permissions: ['clipboard-read', 'clipboard-write'] } });
  // The linked web page's site doesn't exist; a stand-in answers so the new tab has something to show
  await context.route('https://linked.example.test/**', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<p>article</p>' }));
  await open('/c/' + U.chat);
  const plusThere = await plusOf(page).isVisible().catch(() => false);
  if (plusThere) check('with a + menu in the tab row, Files has no button of its own', !(await page.locator('#owui-chat-files-header').count()));
  else check('with no + menu in the tab row, Files has its own button', await page.locator('#owui-chat-files-header').isVisible());
  if (seed.gallery && !plusThere) {
    const order = await page.evaluate(() => { const f = document.getElementById('owui-chat-files-header'), g = document.getElementById('owui-chat-gallery-header'); return f && g && f.parentElement === g.parentElement ? (f.compareDocumentPosition(g) & Node.DOCUMENT_POSITION_FOLLOWING ? 'files-first' : 'gallery-first') : 'apart'; });
    const inBar = await page.evaluate(() => !!document.getElementById('owui-chat-files-header').closest('#controls-container'));
    check('Files and Gallery buttons sit side by side, Gallery nearest the edge', order === (inBar ? 'files-first' : 'gallery-first'), order);
    const moves = await page.evaluate(() => new Promise(done => { const host = document.getElementById('owui-chat-files-header').parentElement; let n = 0; const watch = new MutationObserver(list => { n += list.length; }); watch.observe(host, { childList: true }); setTimeout(() => { watch.disconnect(); done(n); }, 1500); }));
    check('the two buttons settle and stop moving', moves === 0, moves + ' moves');
  }
  await openFiles(page);
  check(`This chat lists ${E.chat_files + photo} files and ${refs} references`, await settle(page, chatRows), await foot(page));
  const tabRow = await page.evaluate(() => !!document.getElementById('chat-panel-main-chat-view'));
  console.log('  (layout: ' + (tabRow ? 'tab row' : 'classic top bar') + ')');
  if (tabRow) check('Files is a tab in the tab row', await page.locator('.panel-tab-strip ~ #owui-chat-files-tabs #owui-chat-files-tab[aria-selected="true"]').count() === 1);
  else check('Files lies over the chat, below the top bar', await page.evaluate(() => { const box = document.getElementById('owui-chat-files'); return box.classList.contains('cf-solo') && box.getBoundingClientRect().top >= document.querySelector('#chat-container nav').getBoundingClientRect().bottom - 1; }));
  check('the chat under it is hidden and unreachable', await page.evaluate(() => { const view = document.getElementById('chat-panel-main-chat-view') || document.getElementById('chat-pane'); return view.hasAttribute('data-cf-covered') && view.inert; }));
  const listed = await names(page);
  check('the footer counts files, total size and references', new RegExp(`^${E.chat_files + photo} files · [\\d.,]+ (KB|MB) · ${refs} references$`).test(await foot(page)), await foot(page));
  check(seed.gallery ? 'images are left to Chat Gallery' : 'images are listed when Chat Gallery is absent', listed.includes('photo.png') === !seed.gallery, listed.join(' | '));
  check('a file on the other branch is listed', listed.includes('branch-only.txt'));
  check('a file the answer links to is listed, an image it links to is not', listed.includes('summary.md') && listed.filter(name => name === 'photo.png').length === (seed.gallery ? 0 : 1));
  check('the shared knowledge base, the note and the web page are listed', listed.includes('Course reading list') && listed.includes('Meeting notes') && listed.includes('An article'));
  check('both files named report.pdf are listed', listed.filter(name => name === 'report.pdf').length === 2);
  const flags = await page.$$eval('.cf-row', rows => rows.map(row => [row.querySelector('.cf-name').textContent, row.querySelector('.cf-flag')?.textContent || '', row.classList.contains('cf-gone')]));
  check('a deleted file is marked deleted', flags.some(([name, flag, gone]) => name === 'deleted-later.txt' && flag === 'Deleted' && gone));
  check('a file Open WebUI failed to read is marked', flags.some(([name, flag]) => name === 'slides.pptx' && flag === 'Processing failed'));
  check('a file Open WebUI has finished reading is not marked as still processing', flags.some(([name, flag]) => name === 'notes.docx' && flag === ''), JSON.stringify(flags.find(([name]) => name === 'notes.docx')));
  await shot(page, 'chat-light');

  if (seed.gallery && tabRow) {
    await page.click('#owui-chat-gallery-header');
    await page.locator('#owui-chat-gallery').waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
    check('opening Gallery sets Files aside', await page.locator('#owui-chat-files').isHidden() && await page.locator('#owui-chat-gallery').isVisible());
    const strips = await page.evaluate(() => [...document.querySelector('.panel-tab-strip').parentElement.children].map(node => node.id || node.className.split(' ')[0]));
    check('the tabs run: chat, Gallery, Files', strips.indexOf('owui-chat-gallery-tabs') + 1 === strips.indexOf('owui-chat-files-tabs'), strips.join());
    await page.click('#owui-chat-files-tab');
    await page.locator('#owui-chat-files').waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
    check('choosing the Files tab brings it back and sets Gallery aside', await page.locator('#owui-chat-files').isVisible() && await page.locator('#owui-chat-gallery').isHidden() && await rowsOf(page).count() === chatRows);
    check('only Files hides the chat now', await page.evaluate(() => !document.querySelector('[data-cg-covered]') && !!document.querySelector('[data-cf-covered]')));
  }

  // Filters, sorting and search
  const kinds = { documents: 9, sheets: 2, slides: 1, code: 3, audio: 1, archives: 1, other: 1, references: refs };
  for (const [value, count] of Object.entries(kinds)) {
    await page.click(`.cf-kinds .cf-chip[data-kind="${value}"]`);
    check(`kind filter: ${count} ${value}`, await settle(page, count, 5000), await foot(page));
  }
  check('each kind shows how many files it has', /Documents\s*9/.test(await page.locator('.cf-kinds .cf-chip[data-kind="documents"]').innerText()) && await page.locator('.cf-kinds .cf-chip[data-kind="references"][aria-pressed="true"]').count() === 1);
  await page.click('.cf-kinds .cf-chip[data-kind="all"]');
  await settle(page, chatRows);
  await page.selectOption('.cf-tools .cf-select', 'largest');
  check('largest first puts the 3 MB log on top', (await names(page))[0] === 'big.log');
  await page.selectOption('.cf-tools .cf-select', 'name');
  const byName = await names(page);
  check('name order sorts as people expect', byName[0].startsWith('a-very-long') && byName[byName.length - 1] === 'تقرير المشروع.txt', byName.slice(0, 3).join(' | ') + ' … ' + byName.slice(-2).join(' | '));
  await page.selectOption('.cf-tools .cf-select', 'oldest');
  // The column titles order the list too
  await page.click('.cf-colhead .cf-colsize');
  const bySize = await names(page);
  await page.click('.cf-colhead .cf-coldate');
  const byDate = await names(page);
  check('the Size and Date column titles order the list', bySize[0] === 'big.log' && await page.locator('.cf-tools .cf-select').inputValue() === 'newest' && byDate[byDate.length - 1] === 'report.pdf', bySize[0] + ' / ' + byDate.slice(-1));
  await page.selectOption('.cf-tools .cf-select', 'oldest');
  await page.fill('.cf-search', 'تقرير');
  check('search finds an Arabic name', await settle(page, 1, 5000));
  await page.fill('.cf-search', 'csv');
  check('search by extension', await settle(page, 2, 5000));
  await page.fill('.cf-search', 'nothing like this');
  await page.waitForSelector('.cf-empty');
  check('an empty search says so', /No matching files/.test(await page.locator('.cf-empty').innerText()));
  await page.fill('.cf-search', '');
  await settle(page, chatRows);
  const longRow = await page.evaluate(() => { const name = [...document.querySelectorAll('.cf-name')].find(node => node.textContent.startsWith('a-very-long')); const list = document.querySelector('.cf-list').getBoundingClientRect(), row = name.closest('.cf-row').getBoundingClientRect(); return { cut: name.scrollWidth > name.clientWidth, fits: row.right <= list.right + 1 }; });
  check('a very long name is cut with an ellipsis and stays in its row', longRow.cut && longRow.fits, JSON.stringify(longRow));
  check('names are shown in their own direction', await page.evaluate(() => [...document.querySelectorAll('.cf-name')].every(node => node.dir === 'auto')));

  // Previews
  await openRow(page, 'report.pdf');
  check('a PDF opens in the browser’s PDF viewer', await page.locator('.cf-stage iframe.cf-pdf[src^="blob:"]').count() === 1 && await visible(page, 'Open in a new tab'));
  check('the preview’s state is ready', await html(page, 'cfPreview') === 'ready', await html(page, 'cfPreview'));
  const details = await facts(page);
  check('details show kind, media type, extension, size in bytes, date, status, owner and the file ID', details.Kind === 'Documents' && details['Media type'] === 'application/pdf' && details.Extension === '.pdf' && /KB \([\d,.\s ]+ bytes\)/.test(details.Size) && details.Uploaded && details.Status === 'Ready' && details.Owner === 'You' && /^[0-9a-f-]{36}$/.test(details['File ID']), JSON.stringify(details));
  await shot(page, 'viewer-pdf');
  await back(page);
  await openRow(page, 'data.csv');
  const csv = await page.$$eval('.cf-table tr', trs => trs.map(tr => [...tr.children].map(cell => cell.textContent)));
  check('CSV: quoted commas, doubled quotes and line breaks inside quotes', csv[1]?.[0] === 'Smith, Jane' && csv[1]?.[2] === 'said "hello"' && csv[2]?.[1] === 'القاهرة' && csv[2]?.[2] === 'two\nlines', JSON.stringify(csv));
  const csvFacts = await facts(page);
  check('details count a table’s rows and columns', csvFacts.Rows === '2' && csvFacts.Columns === '3', JSON.stringify(csvFacts));
  await shot(page, 'viewer-csv');
  await back(page);
  await openRow(page, 'ventes.csv');
  const semi = await page.$$eval('.cf-table tr', trs => trs.map(tr => [...tr.children].map(cell => cell.textContent)));
  check('a semicolon CSV is split on semicolons, decimal commas kept', semi[0]?.join('|') === 'produit|prix|quantité' && semi[1]?.[1] === '1,20', JSON.stringify(semi));
  await back(page);
  await openRow(page, 'config.json');
  check('JSON is pretty-printed', /\n {2}"items": \[\n {4}1,/.test(await page.locator('.cf-pre').innerText()));
  check('details count a text’s lines, words and characters', +(await facts(page)).Lines > 0 && +(await facts(page)).Words > 0 && +(await facts(page)).Characters > 0, JSON.stringify(await facts(page)));
  await back(page);
  await openRow(page, 'big.log');
  const log = await page.evaluate(() => ({ cut: document.querySelector('.cf-cut')?.textContent || '', length: document.querySelector('.cf-pre')?.textContent.length || 0 }));
  check('a 3 MB log shows only its first half megabyte, and says so', /Showing the first 512 KB/.test(log.cut) && log.length > 400000 && log.length <= 524288, JSON.stringify(log));
  await back(page);
  await openRow(page, 'fake.pdf');
  check('an HTML page named .pdf is not shown as a PDF and runs nothing', await page.locator('.cf-stage iframe').count() === 0 && /isn’t one/.test(await stageText(page)) && !(await page.evaluate(() => window.__pwned)), await stageText(page));
  await back(page);
  await openRow(page, 'data.bin');
  check('a binary file is not shown as text', await page.locator('.cf-stage .cf-pre').count() === 0 && /No preview|isn’t text/.test(await stageText(page)), await stageText(page));
  await back(page);
  await openRow(page, 'notes.docx');
  check('a Word file shows the text Open WebUI extracted', /Minutes of the planning meeting/.test(await stageText(page)) && /extracted/.test(await stageText(page)), await stageText(page));
  await back(page);
  await openRow(page, 'slides.pptx');
  const failedFacts = await facts(page);
  await page.waitForSelector('.cf-info .cf-error', { timeout: 10000 }).catch(() => {});
  check('a file Open WebUI couldn’t read says why', failedFacts.Status === 'Processing failed' && (await page.locator('.cf-info .cf-error').innerText().catch(() => '')).length > 3, JSON.stringify(failedFacts));
  await back(page);
  await openRow(page, 'tone.wav');
  const duration = await page.waitForFunction(() => { const audio = document.querySelector('.cf-stage audio'); return audio && audio.readyState >= 1 ? audio.duration : 0; }, null, { timeout: 15000 }).then(handle => handle.jsonValue(), () => 0);
  check('audio plays in the page, straight from Open WebUI', Math.abs(duration - 1) < 0.1, String(duration));
  check('details give a recording’s length', (await facts(page)).Duration === '0:01', JSON.stringify(await facts(page)));
  await back(page);
  await openRow(page, 'archive.zip');
  check('an archive offers a download instead of a preview', /No preview/.test(await stageText(page)) && await page.locator('.cf-stage .cf-btn', { hasText: 'Download' }).count() === 1);
  await back(page);
  await openRow(page, 'deleted-later.txt');
  check('a deleted file says so and offers nothing to download', /was deleted/.test(await stageText(page)) && !(await visible(page, 'Download')));
  await back(page);
  await openRow(page, 'Course reading list');
  check('a knowledge base reference can be opened', /Knowledge base/.test(await stageText(page)) && await page.locator('.cf-stage .cf-btn', { hasText: 'Open' }).count() === 1 && !(await visible(page, 'Download')));
  await back(page);
  await openRow(page, 'An article');
  const [popup] = await Promise.all([page.waitForEvent('popup', { timeout: 10000 }).catch(() => null), page.click('.cf-stage .cf-btn')]);
  check('a web reference opens in a new tab without sharing the session', popup && popup.url().startsWith('https://linked.example.test/article') && !(await popup.evaluate(() => window.opener).catch(() => null)), popup?.url());
  await popup?.close();
  await back(page);

  // Keyboard
  await page.locator('.cf-list .cf-main').first().focus();
  await page.keyboard.press('ArrowDown');
  const second = await page.evaluate(() => document.activeElement === document.querySelectorAll('.cf-main')[1]);
  await page.keyboard.press('End');
  const last = await page.evaluate(() => { const all = document.querySelectorAll('.cf-main'); return document.activeElement === all[all.length - 1]; });
  await page.keyboard.press('Home'); await page.keyboard.press('Enter');
  await page.waitForSelector('.cf-viewer.cf-open');
  await page.keyboard.press('ArrowRight');
  const stepped = await page.locator('.cf-count').innerText();
  await page.keyboard.press('Escape');
  const returned = await page.evaluate(() => document.activeElement?.classList.contains('cf-main') && !document.querySelector('.cf-viewer.cf-open'));
  check('arrow keys, Home, End, Enter and Escape work through the list and preview', second && last && stepped === `2 / ${chatRows}` && returned, JSON.stringify({ second, last, stepped, returned }));
  await page.keyboard.press('/');
  check('slash goes to the search box', await page.evaluate(() => document.activeElement?.classList.contains('cf-search')));
  await page.keyboard.press('Escape');

  // Each file's menu: the ⋯ button, a right click, or Shift+F10
  const csvRow = page.locator('.cf-row', { has: page.locator('.cf-name', { hasText: 'data.csv' }) }).first();
  await csvRow.hover(); await csvRow.locator('.cf-more').click();
  await page.waitForFunction(() => /Rename/.test(document.querySelector('.cf-menu')?.textContent || ''), null, { timeout: 8000 }).catch(() => {});
  const menuWords = await page.locator('.cf-menu').innerText().catch(() => '');
  check('a file’s ⋯ menu offers open, download, attach, show in chat, copy name, rename and delete', ['Open', 'Download', 'Attach to message', 'Show in chat', 'Copy name', 'Rename', 'Delete'].every(word => menuWords.includes(word)), menuWords.replace(/\n/g, ' | '));
  check('the menu takes the keyboard, and the ⋯ button says it is open', await page.evaluate(() => document.activeElement?.classList.contains('cf-menuitem')) && await csvRow.locator('.cf-more[aria-expanded="true"]').count() === 1);
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('Escape');
  check('Escape closes the menu and gives focus back to the file', !(await page.locator('.cf-menu').count()) && await page.evaluate(() => document.activeElement?.closest('.cf-row')?.querySelector('.cf-name')?.textContent === 'data.csv') && await page.locator('.cf-viewer.cf-open').count() === 0);
  await csvRow.click({ button: 'right' });
  check('a right click opens the same menu', await page.locator('.cf-menu .cf-menuitem').count() >= 5);
  await page.locator('.cf-menu .cf-menuitem', { hasText: 'Copy name' }).click();
  check('Copy name copies the file’s name', (await page.evaluate(() => navigator.clipboard.readText())) === 'data.csv');
  const kbRow = page.locator('.cf-row', { has: page.locator('.cf-name', { hasText: 'Course reading list' }) }).first();
  await kbRow.locator('.cf-main').focus(); await page.keyboard.press('Shift+F10');
  const refMenu = await page.locator('.cf-menu').innerText().catch(() => '');
  check('Shift+F10 opens it from the keyboard; a reference offers no download, rename or delete', /Open/.test(refMenu) && !/Download|Rename|Delete/.test(refMenu), refMenu.replace(/\n/g, ' | '));
  await page.keyboard.press('Escape');

  // Picking files: Space, Shift for a range, Ctrl+A, the footer adds them up
  await page.locator('.cf-list .cf-main').first().focus();
  await page.keyboard.press(' ');
  check('Space picks a file and starts Select mode', await html(page, 'cfSelect') === 'on' && /^1 selected/.test(await foot(page)), await foot(page));
  await page.locator('.cf-list .cf-row').nth(3).click({ modifiers: ['Shift'] });
  check('Shift-click picks everything in between, and the footer adds it up', /^4 selected · [\d.,]+ (KB|MB)$/.test(await foot(page)), await foot(page));
  await page.locator('.cf-list .cf-main').first().focus();
  await page.keyboard.press('Control+a');
  const allPicked = await page.locator('.cf-row.cf-picked').count();
  check('Ctrl+A picks every file, and no reference', allPicked === E.chat_files + photo - 1, `${allPicked}`);
  await page.keyboard.press('Escape');
  check('Escape leaves Select mode', await html(page, 'cfSelect') === 'off' && !(await page.locator('.cf-row.cf-picked').count()));
  const pyRow = page.locator('.cf-row', { has: page.locator('.cf-name', { hasText: 'script.py' }) }).first();
  await pyRow.locator('.cf-main').focus(); await page.keyboard.press('Delete');
  await page.waitForSelector('.cf-modal[open]');
  const box = await page.locator('.cf-modal[open]').boundingBox(), vp = page.viewportSize();
  check('Delete asks first, names the file, in a box in the middle of the screen', /script\.py/.test(await page.locator('.cf-modal[open] .cf-names').innerText()) && Math.abs(box.x + box.width / 2 - vp.width / 2) < 4 && Math.abs(box.y + box.height / 2 - vp.height / 2) < 4, JSON.stringify(box));
  await page.click('.cf-modal[open] .cf-btn:has-text("Cancel")');

  // The grid
  await page.click('.cf-head [aria-label="Grid"]');
  const grid = await page.evaluate(() => { const cards = [...document.querySelectorAll('.cf-list .cf-item')]; const top = cards[0].offsetTop; return { view: document.querySelector('.cf-list').dataset.view, perRow: cards.filter(card => card.offsetTop === top).length, cols: !document.querySelector('.cf-colhead') || document.querySelector('.cf-colhead').hidden }; });
  check('the grid shows files as cards, several to a row, without the column titles', grid.view === 'grid' && grid.perRow >= 3 && grid.cols && await html(page, 'cfView') === 'grid', JSON.stringify(grid));
  await page.locator('.cf-list .cf-main').first().focus();
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowDown');
  const gridAt = await page.evaluate(() => [...document.querySelectorAll('.cf-main')].indexOf(document.activeElement));
  check('in the grid, arrows move across and down a row', gridAt === 1 + grid.perRow, `${gridAt} of rows of ${grid.perRow}`);
  await shot(page, 'grid-light');
  await page.reload({ waitUntil: 'domcontentloaded' }); await page.waitForFunction(() => document.documentElement.dataset.chatFiles === '1');
  await page.waitForTimeout(1500);
  await openFiles(page);
  await settle(page, chatRows);
  check('the grid is remembered in this browser', await page.locator('.cf-list[data-view="grid"]').count() === 1);
  await page.click('.cf-head [aria-label="List"]');
  // The platform's language, switched while it is open
  await page.evaluate(() => document.documentElement.setAttribute('lang', 'fr-FR'));
  await page.waitForTimeout(400);
  const french = await page.locator('#cf-tab-chat').innerText().catch(() => '');
  await page.evaluate(() => document.documentElement.setAttribute('lang', 'en-US'));
  await page.waitForTimeout(400);
  check('switching Open WebUI to French redraws Files in French at once, and back, keeping the files', french === 'Cette conversation' && await page.locator('#cf-tab-chat').innerText() === 'This chat' && await settle(page, chatRows), french);

  // Copy, download, attach
  await openRow(page, 'script.py');
  await page.click('.cf-vbar [aria-label="Copy text"]');
  check('Copy text puts the file’s text on the clipboard', (await page.evaluate(() => navigator.clipboard.readText())) === "def hello():\n    print('hi')\n");
  const uploadsBefore = state.uploads;
  await page.click('.cf-vbar [aria-label="Attach to message"]');
  await page.waitForFunction(n => window.__cfUploads === undefined, null).catch(() => {});
  await page.waitForTimeout(2500);
  check('Attach to message uploads it through Open WebUI’s composer', state.uploads > uploadsBefore && await page.locator('#owui-chat-files').isHidden(), `uploads ${uploadsBefore} -> ${state.uploads}`);
  await page.click(tabRow ? '#owui-chat-files-tab' : '#owui-chat-files-header');
  await page.locator('#owui-chat-files').waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
  if (await page.locator('.cf-viewer.cf-open').count()) await back(page);
  await openRow(page, 'تقرير المشروع.txt');
  // Headless Chromium saves any name that isn't plain ASCII as "download", even with Open WebUI's own UTF-8 header,
  // so the check is on the name handed to the browser and on the bytes saved
  await page.evaluate(() => { const click = HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click = function () { if (this.download) window.__cfSaved = this.download; return click.call(this); }; });
  const [download] = await Promise.all([page.waitForEvent('download', { timeout: 30000 }), page.click('.cf-vbar [aria-label="Download"]')]);
  const saved = fs.readFileSync(await download.path(), 'utf8');
  check('download hands the browser the original name, Arabic included, and the right bytes', (await page.evaluate(() => window.__cfSaved)) === 'تقرير المشروع.txt' && saved === 'مرحبا بالعالم\nسطر ثان\n', await page.evaluate(() => window.__cfSaved));
  await back(page);

  // Rename
  await openRow(page, 'summary.md');
  await page.click('.cf-vbar [aria-label="Rename"]');
  const selection = await page.evaluate(() => { const input = document.querySelector('.cf-modal[open] input'); return input.value.slice(input.selectionStart, input.selectionEnd); });
  check('rename selects the name without its extension', selection === 'summary', selection);
  await page.fill('.cf-modal[open] input', '   ');
  await page.click('.cf-modal[open] .cf-primary');
  const emptyMessage = await page.locator('.cf-modal[open] .cf-hint').innerText();
  await page.fill('.cf-modal[open] input', 'bad/name.md');
  await page.click('.cf-modal[open] .cf-primary');
  check('an empty name or one with a slash is refused', /Enter a name/.test(emptyMessage) && /can’t contain/.test(await page.locator('.cf-modal[open] .cf-hint').innerText()));
  await page.fill('.cf-modal[open] input', 'résumé final.md');
  await page.click('.cf-modal[open] .cf-primary');
  await page.waitForSelector('.cf-modal[open]', { state: 'detached', timeout: 5000 }).catch(() => {});
  await page.waitForFunction(() => !document.querySelector('.cf-modal[open]'), null, { timeout: 5000 }).catch(() => {});
  const record = await api(U, 'GET', '/api/v1/files/' + F.md);
  check('rename saves the new name in Open WebUI and shows it', record.json?.meta?.name === 'résumé final.md' && /résumé final\.md/.test(await page.locator('.cf-info .cf-title').innerText()), record.json?.meta?.name);
  await page.click('.cf-vbar [aria-label="Rename"]');
  await page.keyboard.press('Escape');
  check('Escape closes the rename box and keeps the preview', !(await page.locator('.cf-modal[open]').count()) && await page.locator('.cf-viewer.cf-open').count() === 1);
  await back(page);

  // Delete
  await openRow(page, 'branch-only.txt');
  await page.click('.cf-vbar [aria-label="Delete"]');
  await page.click('.cf-modal[open] .cf-btn:has-text("Cancel")');
  const kept = (await api(U, 'GET', '/api/v1/files/' + F.branch)).status;
  await page.click('.cf-vbar [aria-label="Delete"]');
  await page.click('.cf-modal[open] .cf-danger');
  await page.waitForFunction(() => /was deleted|\d+ \/ \d+/.test(document.querySelector('.cf-stage')?.textContent + document.querySelector('.cf-count')?.textContent), null, { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(800);
  const gone = (await api(U, 'GET', '/api/v1/files/' + F.branch)).status;
  check('Cancel keeps the file and Delete removes it from Open WebUI', kept === 200 && gone === 404, `${kept} / ${gone}`);
  await back(page);
  check('in This chat a deleted file stays, marked deleted', await page.locator('.cf-row.cf-gone .cf-name', { hasText: 'branch-only.txt' }).count() === 1);

  // Select and zip
  await page.click('.cf-head .cf-btn[aria-pressed]:has-text("Select")');
  for (const name of ['report.pdf', 'data.csv']) for (const row of await page.locator('.cf-row', { has: page.locator('.cf-name', { hasText: name }) }).all()) await row.click();
  await page.locator('.cf-row', { has: page.locator('.cf-name', { hasText: 'Course reading list' }) }).click();
  check('Select mode picks files and ignores references', await page.locator('.cf-head .cf-btn:has-text("Download 3")').isVisible());
  const [zip] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.click('.cf-head .cf-btn:has-text("Download 3")')]);
  const bytes = fs.readFileSync(await zip.path());
  const entries = [];
  for (let at = bytes.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02])); at >= 0; at = bytes.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]), at + 46)) {
    const length = bytes.readUInt16LE(at + 28); entries.push({ name: bytes.slice(at + 46, at + 46 + length).toString('utf8'), size: bytes.readUInt32LE(at + 24) });
  }
  check('the zip holds the three files, the second report.pdf renamed', entries.map(e => e.name).sort().join('|') === 'data.csv|report (2).pdf|report.pdf' && entries.every(e => e.size > 0) && /^files-files-test-chat\.zip$/.test(zip.suggestedFilename()), JSON.stringify(entries) + ' ' + zip.suggestedFilename());

  // Show in chat
  await openRow(page, 'data.csv');
  await page.waitForSelector('.cf-vbar [aria-label="Show in chat"]:visible', { timeout: 10000 }).catch(() => {});
  await page.click('.cf-vbar [aria-label="Show in chat"]');
  await page.waitForTimeout(1500);
  check('Show in chat goes to the message and sets Files aside', page.url().includes('message=m1') && await page.locator('#owui-chat-files').isHidden(), page.url());

  // My files, with a second page
  await page.click(tabRow ? '#owui-chat-files-tab' : '#owui-chat-files-header');
  await page.locator('#owui-chat-files').waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
  check('coming back keeps the preview you left', await page.locator('.cf-viewer.cf-open').count() === 1);
  await back(page);
  await api(U, 'POST', '/api/v1/files/' + U.extras[5] + '/rename', { filename: 'invoice\u202Etxt.exe' });
  await page.click('#cf-tab-library');
  await page.waitForFunction(() => document.querySelectorAll('.cf-row').length >= 150, null, { timeout: 20000 }).catch(() => {});
  const firstPage = await rowsOf(page).count();
  for (let i = 0; i < 6; i++) { await page.evaluate(() => { const box = document.querySelector('.cf-scroll'); box.scrollTop = box.scrollHeight; }); await page.waitForTimeout(700); }
  // One file deleted above, one new copy uploaded by Attach to message, and the photo when Gallery is absent
  const mine = E.library_files - 1 + 1 + (seed.gallery ? 0 : 1);
  check(`My files pages through all ${mine} files`, await settle(page, mine, 20000) && firstPage < mine, `${firstPage} then ${await rowsOf(page).count()} · ${await foot(page)}`);
  const all = await names(page);
  check('My files has the extra files and no references', all.includes('extra-229.txt') && !all.includes('Course reading list') && all.includes('résumé final.md'));
  check('a name with a hidden direction control is shown as it really reads', all.includes('invoicetxt.exe') && !all.some(name => /[\u202A-\u202E\u2066-\u2069]/.test(name)), all.filter(name => /invoice/.test(name)).join());
  check('My files is grouped by date', /Today/.test(await page.locator('.cf-list .cf-group').first().innerText().catch(() => '')));
  check('the library’s state is shown, and tab and job are settled', await html(page, 'cfLibrary') === 'done' && await html(page, 'cfTab') === 'front' && await html(page, 'cfJob') === 'idle', [await html(page, 'cfLibrary'), await html(page, 'cfTab'), await html(page, 'cfJob')].join());
  await shot(page, 'library-light');

  // A server error, then a retry
  await page.route('**/api/v1/files/user/search**', route => route.fulfill({ status: 500, body: '{}' }));
  await page.click('.cf-head [aria-label="Refresh"]');
  await page.waitForSelector('.cf-empty .cf-btn', { timeout: 15000 }).catch(() => {});
  const errorShown = /Couldn’t load your files/.test(await page.locator('.cf-empty').innerText().catch(() => ''));
  await page.unroute('**/api/v1/files/user/search**');
  await page.click('.cf-empty .cf-btn');
  check('a server error shows Try again, which recovers', errorShown && await page.waitForFunction(() => document.querySelectorAll('.cf-row').length >= 150, null, { timeout: 15000 }).then(() => true, () => false));

  // Empty chat, and the home page
  await page.click(tabRow ? '#owui-chat-files-tabs [aria-label="Close Files"]' : '.cf-head [aria-label="Close Files"]');
  check('closing removes the tab and uncovers the chat', await page.locator('#owui-chat-files').isHidden() && !(await page.locator('[data-cf-covered]').count()));
  await open('/c/' + U.empty);
  await openFiles(page);
  await page.waitForSelector('.cf-empty');
  check('an empty chat says so', /No files in this chat yet/.test(await page.locator('.cf-empty').innerText()));
  await page.click(tabRow ? '#owui-chat-files-tabs [aria-label="Close Files"]' : '.cf-head [aria-label="Close Files"]');
  await open('/');
  if (await page.locator('#owui-chat-files-header').isVisible().catch(() => false)) {
    await page.click('#owui-chat-files-header');
    await page.waitForSelector('.cf-row', { timeout: 15000 }).catch(() => {});
    check('on the home page it opens on My files, without This chat', await page.locator('#cf-tab-chat').isHidden() && await page.locator('#cf-tab-library[aria-selected="true"]').count() === 1);
  } else console.log('  (no Files button on the home page in this layout)');

  check('the sign-in token and cookies never left the site', state.leaks.length === 0, state.leaks.join());
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- light, dark, OLED black and Her: is everything readable? ---- */
console.log('Themes');
for (const theme of ['light', 'dark', 'oled-dark', 'her']) {
  const { context, page, state, open } = await session(U, { theme });
  await open('/c/' + U.chat);
  await openFiles(page);
  await settle(page, chatRows);
  const scores = {};
  for (const [label, selector] of Object.entries({ names: '.cf-name', details: '.cf-cell', badges: '.cf-badge', flags: '.cf-flag', tabs: '.cf-tab', chips: '.cf-chip', columns: '.cf-colbtn', footer: '.cf-foot span', buttons: '.cf-head .cf-btn' })) scores[label] = +(await contrastOf(page, selector)).toFixed(2);
  const low = Object.entries(scores).filter(([, score]) => score < 4.5);
  check(`${theme}: every text reaches 4.5:1 contrast`, low.length === 0, JSON.stringify(scores));
  await shot(page, `chat-${theme}`);
  await openRow(page, 'data.csv');
  const table = +(await contrastOf(page, '.cf-table td')).toFixed(2), dt = +(await contrastOf(page, '.cf-facts dt')).toFixed(2);
  check(`${theme}: the preview reaches 4.5:1 too`, table >= 4.5 && dt >= 4.5, JSON.stringify({ table, dt }));
  await shot(page, `viewer-${theme}`);
  // The delete box, and the red button white text sits on
  await page.click('.cf-vbar [aria-label="Delete"]');
  await page.waitForSelector('.cf-modal[open]');
  const modal = { text: +(await contrastOf(page, '.cf-modal[open] p')).toFixed(2), danger: +(await contrastOf(page, '.cf-modal[open] .cf-danger')).toFixed(2), cancel: +(await contrastOf(page, '.cf-modal[open] .cf-btn:not(.cf-danger)')).toFixed(2) };
  await shot(page, `delete-${theme}`);
  await page.click('.cf-modal[open] .cf-btn:has-text("Cancel")');
  check(`${theme}: the delete box reaches 4.5:1`, Object.values(modal).every(score => score >= 4.5), JSON.stringify(modal));
  if (theme === 'light') {
    // Switching theme while the tab is open repaints it at once
    await page.evaluate(() => { document.documentElement.classList.remove('light'); document.documentElement.classList.add('dark'); });
    await back(page);
    const live = +(await contrastOf(page, '.cf-name')).toFixed(2), dark = await page.evaluate(() => getComputedStyle(document.querySelector('.cf-name')).color);
    check('switching to dark while open repaints it, still readable', live >= 4.5 && dark !== 'rgb(38, 38, 38)', `${live} ${dark}`);
  }
  check(`${theme}: no page errors`, state.errors.length === 0, state.errors.slice(0, 2).join(' | '));
  await context.close();
}

/* ---- a phone ---- */
console.log('Plain user, phone, dark');
{
  const { context, page, state, open } = await session(U, { viewport: { width: 390, height: 844 }, theme: 'dark', extra: { hasTouch: true, isMobile: true } });
  await open('/c/' + U.chat);
  check('the Files button is in the top bar', await page.locator('nav #owui-chat-files-header').isVisible());
  await page.tap('#owui-chat-files-header');
  check('Files opens over the chat with its files', await settle(page, chatRows));
  const layout = await page.evaluate(() => { const box = document.getElementById('owui-chat-files'); const r = box.getBoundingClientRect(); return { width: r.width, overflow: box.scrollWidth > box.clientWidth + 1 || document.querySelector('.cf-scroll').scrollWidth > document.querySelector('.cf-scroll').clientWidth + 1, below: r.top >= document.querySelector('#chat-container nav').getBoundingClientRect().bottom - 1, bg: getComputedStyle(box).backgroundColor }; });
  check('it fills the width below the top bar, with no sideways scrolling', layout.width >= 380 && !layout.overflow && layout.below && layout.bg !== 'rgba(0, 0, 0, 0)', JSON.stringify(layout));
  await shot(page, 'chat-phone-dark');
  await page.locator('.cf-row', { has: page.locator('.cf-name', { hasText: 'data.csv' }) }).tap();
  await page.waitForSelector('.cf-viewer.cf-open');
  await page.waitForSelector('.cf-table', { timeout: 15000 }).catch(() => {});
  const viewerLayout = await page.evaluate(() => { const stage = document.querySelector('.cf-stage').getBoundingClientRect(), info = document.querySelector('.cf-info').getBoundingClientRect(); return { stacked: info.top >= stage.bottom - 1, pageOverflow: document.documentElement.scrollWidth > innerWidth + 1 }; });
  check('the preview puts details under the file and the table scrolls inside its box', viewerLayout.stacked && !viewerLayout.pageOverflow, JSON.stringify(viewerLayout));
  const infoBox = await page.evaluate(() => { const info = document.querySelector('.cf-info'), r = info.getBoundingClientRect(), box = document.getElementById('owui-chat-files').getBoundingClientRect(); return { height: Math.round(r.height), inside: r.bottom <= box.bottom + 1, fits: info.scrollHeight <= info.clientHeight + 1 }; });
  check('the details under the preview get room, and scroll only when they must', infoBox.inside && (infoBox.fits || infoBox.height >= 150), JSON.stringify(infoBox));
  await shot(page, 'viewer-phone-dark');
  await page.locator('.cf-vbar [aria-label="Back to files"]').tap();
  await page.locator('.cf-head [aria-label="Close Files"]').tap();
  check('its close button closes it', await page.locator('#owui-chat-files').isHidden());
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- an administrator: the + menu, and only their own files ---- */
console.log('Administrator');
{
  const { context, page, state, open } = await session(seed.admin);
  await open('/c/' + seed.admin.chat);
  const plus = page.locator('#controls-container button[aria-label="New tab"]').first();
  await plus.waitFor({ state: 'visible', timeout: 8000 }).catch(() => {});
  if (!(await plus.isVisible().catch(() => false))) { await page.click('nav button[aria-label="Tabs"]').catch(() => {}); await plus.waitFor({ state: 'visible', timeout: 8000 }).catch(() => {}); }
  if (await plus.isVisible().catch(() => false)) {
    await plus.click();
    await page.waitForSelector('#owui-chat-files-button', { timeout: 10000 }).catch(() => {});
    check('Files is in the + menu' + (seed.gallery ? ', beside Gallery' : ''), await page.locator('#owui-chat-files-button').count() === 1 && (!seed.gallery || await page.locator('#owui-chat-gallery-button').count() === 1));
    check('and has no button of its own beside the + menu', !(await page.locator('#owui-chat-files-header').count()));
    await page.click('#owui-chat-files-button');
    check('choosing it opens a Files tab in the panel', await settle(page, 1) && await page.locator('#controls-container #owui-chat-files-tab[aria-selected="true"]').count() === 1);
    await page.click('#cf-tab-library');
    check('an administrator’s My files holds only their own files', await settle(page, 1) && (await names(page))[0] === 'admin-only.pdf', (await names(page)).slice(0, 5).join(' | '));
  } else check('the + menu is there for an administrator', false, 'no + button');
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- valves ---- */
console.log('Valves');
{
  await setValves({ ...defaults, include_images: 'yes', show_references: false, allow_rename: false, allow_delete: false, language: 'fr' });
  const { context, page, open } = await session(U);
  await open('/c/' + U.chat);
  await openFiles(page);
  const expected = E.chat_files + 1;
  check('images can be listed and references left out', await settle(page, expected) && (await names(page)).includes('photo.png') && !(await names(page)).includes('An article'), `${await rowsOf(page).count()} · ${await foot(page)}`);
  check('the Files tab speaks French', await page.locator('#cf-tab-chat').innerText() === 'Cette conversation' && await page.locator('#cf-tab-library').innerText() === 'Mes fichiers' && /fichiers/.test(await foot(page)));
  await openRow(page, 'photo.png');
  check('an image previews when images are listed', await page.locator('.cf-stage img.cf-media').count() === 1);
  await page.waitForFunction(() => /\d+ × \d+/.test(document.querySelector('.cf-info')?.textContent || ''), null, { timeout: 8000 }).catch(() => {});
  check('details give a picture’s size', /\d+ × \d+/.test((await facts(page)).Dimensions || ''), JSON.stringify(await facts(page)));
  await page.click('.cf-vbar [aria-label="Retour aux fichiers"]');
  const thumb = await page.waitForFunction(() => { const img = [...document.querySelectorAll('.cf-thumbimg')].find(node => node.closest('.cf-row').querySelector('.cf-name').textContent === 'photo.png'); return img && img.complete && img.naturalWidth > 0; }, null, { timeout: 8000 }).then(() => true, () => false);
  check('an image shows as a small picture in the list', thumb);
  await openRow(page, 'photo.png');
  check('no rename or delete when they are turned off', !(await visible(page, 'Renommer')) && !(await visible(page, 'Supprimer')) && await visible(page, 'Télécharger'));
  await context.close();

  await setValves({ ...defaults, header_button: false });
  const second = await session(U);
  await second.open('/c/' + U.chat);
  await second.page.waitForTimeout(1500);
  check('the Files button can be turned off', !(await second.page.locator('#owui-chat-files-header').count()));
  await second.context.close();
  await setValves(defaults);

  if (seed.gallery) {
    await api(seed.admin, 'POST', '/api/v1/functions/id/chat_gallery/toggle');
    const third = await session(U);
    await third.open('/c/' + U.chat);
    await openFiles(third.page);
    await settle(third.page, chatRows + 1);
    check('with Chat Gallery turned off, images are listed automatically', (await names(third.page)).includes('photo.png'));
    await third.context.close();
    await api(seed.admin, 'POST', '/api/v1/functions/id/chat_gallery/toggle');
  }
}

await browser.close();
console.log(`\n${passed} passed, ${failed.length} failed`);
if (failed.length) { for (const name of failed) console.log('  - ' + name); process.exit(1); }
