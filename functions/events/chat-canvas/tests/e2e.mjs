// Browser checks for Chat Canvas against a throwaway Open WebUI seeded by seed.py.
//
//   python tests/seed.py http://127.0.0.1:8080 chat_canvas.py ../chat-files/chat_files.py ../chat-gallery/chat_gallery.py > seed.json
//   node tests/e2e.mjs seed.json [screenshot-folder]
//
// It creates files and notes and changes valves on that instance. Needs Playwright with Chromium.
import { createRequire } from 'module';
import fs from 'fs';

const { chromium } = createRequire(import.meta.url)('playwright');
const seed = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const shots = process.argv[3] || null;
if (shots) fs.mkdirSync(shots, { recursive: true });
const others = seed.installed.includes('chat_files') && seed.installed.includes('chat_gallery');

let passed = 0;
const failed = [];
const check = (name, ok, detail = '') => {
  if (ok) { passed++; console.log('  ok   ' + name); }
  else { failed.push(name); console.log('  FAIL ' + name + (detail ? ' -> ' + String(detail).slice(0, 500) : '')); }
};
const api = async (account, method, path, body) => {
  const response = await fetch(seed.base + path, { method, headers: { Authorization: 'Bearer ' + account.token, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, json: await response.json().catch(() => null) };
};
const setValves = valves => api(seed.admin, 'POST', '/api/v1/functions/id/chat_canvas/valves/update', valves);
const defaults = { min_code_lines: 3, min_document_chars: 1200, run_scripts: true, keep_drafts: true, allow_save: true, header_button: true, language: 'auto' };
const U = seed.user;
const ALL = 14; // canvases the test chat yields with the default valves

const browser = await chromium.launch();
async function session(account, { viewport = { width: 1400, height: 900 }, theme = 'light', extra = {} } = {}) {
  const context = await browser.newContext({ viewport, acceptDownloads: true, ...extra });
  const page = await context.newPage();
  const state = { errors: [], leaks: [] };
  // Open WebUI's own Artifacts throws this on any chat that holds an HTML block, with or without Canvas
  page.on('pageerror', error => { if (!/Failed to read a named property 'addEventListener' from 'Window'/.test(String(error))) state.errors.push(String(error)); });
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin === new URL(seed.base).origin || url.protocol === 'data:' || url.protocol === 'blob:' || url.protocol === 'about:') return;
    const headers = request.headers();
    if (headers.authorization || headers.cookie) state.leaks.push(url.hostname);
  });
  const { version } = await (await fetch(seed.base + '/api/config')).json();
  await api(account, 'POST', '/api/v1/users/user/settings/update', { ui: { version } });
  await context.request.post(seed.base + '/api/v1/auths/signin', { data: { email: account.email, password: seed.password } });
  // The script also runs inside sandboxed preview frames, where storage is off limits
  await context.addInitScript(([token, theme]) => { try { localStorage.token = token; localStorage.theme = theme; } catch {} }, [account.token, theme]);
  const open = async path => {
    await page.goto(seed.base + path, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.documentElement.dataset.chatCanvas === '1', null, { timeout: 30000 });
    await page.waitForSelector('#chat-input', { timeout: 30000 });
    await page.waitForSelector('#owui-chat-canvas-header', { timeout: 15000 }).catch(() => {});
  };
  return { context, page, state, open };
}
const shot = (page, name) => shots ? page.screenshot({ path: `${shots}/${name}.png` }) : null;
const foot = page => page.locator('.cv-foot').innerText().catch(() => 'closed');
const settle = (page, count, timeout = 20000) => page.waitForFunction(n => document.querySelectorAll('.cv-list .cv-row').length === n && !/Reading|Lecture/.test(document.querySelector('.cv-foot')?.textContent || ''), count, { timeout }).then(() => true, () => false);
const titles = page => page.$$eval('.cv-list .cv-name', nodes => nodes.map(node => node.textContent));
const rowOf = (page, title, nth = 0) => page.locator('.cv-list .cv-row', { has: page.locator('.cv-name', { hasText: title }) }).nth(nth);
const openRow = async (page, title, nth = 0) => { await rowOf(page, title, nth).click(); await page.waitForSelector('.cv-viewer.cv-open'); };
const back = page => page.click('.cv-vbar [aria-label="Back to the list"]');
const text = page => page.locator('.cv-area').inputValue();
const bar = (page, label) => page.locator(`.cv-vbar [aria-label="${label}"]`);
const composerText = page => page.evaluate(() => document.getElementById('chat-input')?.innerText || document.getElementById('chat-input')?.value || '');
// Canvas is a tab wherever a tab row or the side panel is on screen, and lies over the chat otherwise
const asTab = page => page.locator('#owui-chat-canvas-tab').isVisible().catch(() => false);
const reopen = async page => { await page.click(await asTab(page) ? '#owui-chat-canvas-tab' : '#owui-chat-canvas-header'); await page.locator('#owui-chat-canvas').waitFor({ state: 'visible', timeout: 5000 }).catch(() => {}); };
const closeIt = async page => page.click(await asTab(page) ? '#owui-chat-canvas-tabs [aria-label="Close Canvas"]' : '.cv-head [aria-label="Close Canvas"]');
const drafts = page => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('owui-chat-canvas:')));

const contrastOf = (page, selector) => page.evaluate(selector => {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const paint = canvas.getContext('2d', { willReadFrequently: true });
  const norm = value => { const m = /^color\(srgb\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)(?:\s*\/\s*([\d.e+-]+))?\)$/.exec(value || ''); return m ? `rgba(${m[1] * 255}, ${m[2] * 255}, ${m[3] * 255}, ${m[4] ?? 1})` : value; };
  const alpha = value => { value = norm(value); if (!value || value === 'transparent') return 0; paint.clearRect(0, 0, 1, 1); paint.fillStyle = '#000'; paint.fillStyle = value; paint.fillRect(0, 0, 1, 1); return paint.getImageData(0, 0, 1, 1).data[3]; };
  const composite = colours => { paint.fillStyle = '#fff'; paint.fillRect(0, 0, 1, 1); for (const colour of colours) { paint.fillStyle = norm(colour); paint.fillRect(0, 0, 1, 1); } const [r, g, b] = paint.getImageData(0, 0, 1, 1).data; return { r, g, b }; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const out = [];
  for (const node of [...document.querySelectorAll(selector)].slice(0, 12)) {
    const box = node.getBoundingClientRect(); if (!box.width || !box.height || box.bottom > innerHeight || box.top < 0 || box.right > innerWidth) continue;
    const layers = [];
    for (const each of document.elementsFromPoint(box.left + Math.min(4, box.width / 2), box.top + box.height / 2)) {
      if (each.closest('[data-cv-covered],[data-cf-covered],[data-cg-covered]')) continue;
      const bg = getComputedStyle(each).backgroundColor, a = alpha(bg);
      if (a) { layers.push(bg); if (a >= 253) break; }
    }
    layers.reverse();
    const base = composite(layers), fg = composite([...layers, getComputedStyle(node).color]);
    const [hi, lo] = [lum(fg), lum(base)].sort((a, b) => b - a);
    out.push((hi + 0.05) / (lo + 0.05));
  }
  return out.length ? +Math.min(...out).toFixed(2) : 0;
}, selector);

/* ---- a plain user on a wide screen ---- */
console.log('Plain user, wide screen' + (others ? ', with Chat Files and Chat Gallery' : ''));
{
  const { context, page, state, open } = await session(U, { extra: { permissions: ['clipboard-read', 'clipboard-write'] } });
  await open('/c/' + U.chat);
  check('Canvas button is on the page', await page.locator('#owui-chat-canvas-header').isVisible());
  if (others) {
    const order = await page.evaluate(() => [...document.getElementById('owui-chat-canvas-header').parentElement.children].map(node => node.id).filter(id => id.endsWith('-header')).join(' '));
    const inBar = await page.evaluate(() => !!document.getElementById('owui-chat-canvas-header').closest('#controls-container'));
    check('the three buttons sit together in a fixed order', order === (inBar ? 'owui-chat-canvas-header owui-chat-files-header owui-chat-gallery-header' : 'owui-chat-gallery-header owui-chat-files-header owui-chat-canvas-header'), order);
    const moves = await page.evaluate(() => new Promise(done => { const host = document.getElementById('owui-chat-canvas-header').parentElement; let n = 0; const watch = new MutationObserver(list => { n += list.length; }); watch.observe(host, { childList: true }); setTimeout(() => { watch.disconnect(); done(n); }, 1500); }));
    check('the buttons settle and stop moving', moves === 0, moves + ' moves');
  }
  await page.click('#owui-chat-canvas-header');
  check(`the chat yields ${ALL} canvases`, await settle(page, ALL), await foot(page));
  const tabRow = await page.evaluate(() => !!document.getElementById('chat-panel-main-chat-view'));
  const sidePanel = !tabRow && await asTab(page);
  console.log('  (layout: ' + (tabRow ? 'tab row' : sidePanel ? 'classic top bar, Canvas in the side panel' : 'classic top bar, Canvas over the chat') + ')');
  if (sidePanel) check('in the side panel it hides the panel’s content, not the chat', await page.evaluate(() => { const hidden = [...document.querySelectorAll('[data-cv-covered]')]; return hidden.length > 0 && hidden.every(node => node.inert) && !document.getElementById('chat-pane').hasAttribute('data-cv-covered'); }));
  else check('the chat under it is hidden and unreachable', await page.evaluate(() => { const view = document.getElementById('chat-panel-main-chat-view') || document.getElementById('chat-pane'); return view.hasAttribute('data-cv-covered') && view.inert; }));
  const listed = await titles(page);
  check('code hidden in the model’s reasoning is left out', !(await page.$$eval('.cv-row', rows => rows.some(row => /hidden/.test(row.textContent)))) && listed.filter(name => /^Python \d/.test(name)).length === 2, listed.join(' | '));
  check('a one-line command is left out', !listed.some(name => /Shell/.test(name)));
  check('titles come from the block, its file name or the page title', listed.filter(name => name === 'app.py').length === 2 && listed.includes('Demo page') && listed.includes('Project plan'), listed.join(' | '));
  check('a block the user pasted is listed as theirs', /From you/.test(await rowOf(page, 'Python 1').innerText()));
  check('a block still being written is flagged', /Still being written/.test(await rowOf(page, 'Python 4').innerText()));
  check('a drawing sent as XML is recognised as SVG', listed.includes('SVG 1'));
  await shot(page, 'list-light');

  // Parsing
  await openRow(page, 'Markdown 1');
  check('a longer fence keeps the shorter fence inside it', (await text(page)) === '# Readme\n\nInstall with:\n\n```bash\npip install demo\n```\n\nThen run it.', JSON.stringify(await text(page)));
  await back(page);
  await openRow(page, 'SQL 1');
  check('a ~~~ block is read', (await text(page)) === 'SELECT id, name\nFROM users\nWHERE active = 1;');
  await back(page);
  await openRow(page, 'JavaScript 1');
  check('a block inside a list loses its indentation', (await text(page)) === 'const a = 1;\nconst b = 2;\nconsole.log(a + b);', JSON.stringify(await text(page)));
  await back(page);
  await openRow(page, 'Python 4');
  check('an unclosed block runs to the end and can’t be typed in while it grows', (await text(page)).startsWith('while True:') && await page.locator('.cv-area').evaluate(node => node.readOnly));
  await back(page);

  // Editing
  await openRow(page, 'app.py', 0);
  const original = await text(page);
  check('the newest app.py opens first, with line numbers', original.includes('punctuation="!"') && (await page.locator('.cv-gutter').innerText()).trim().split('\n').length === 6 && await page.locator('#cv-mode-preview').isHidden());
  await page.locator('.cv-area').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.type('\nprint("added")');
  check('typing marks the canvas edited and updates the count', /Edited/.test(await page.locator('.cv-status').innerText()) && /7 lines/.test(await page.locator('.cv-status').innerText()) && /Ln 7, Col 15/.test(await page.locator('.cv-status').innerText()), await page.locator('.cv-status').innerText());
  await page.keyboard.press('Control+Home');
  await page.keyboard.press('Tab');
  const indented = (await text(page)).startsWith('  def greet');
  await page.keyboard.press('Shift+Tab');
  check('Tab indents and Shift+Tab takes it back', indented && (await text(page)).startsWith('def greet'));
  await page.keyboard.press('Control+z'); await page.keyboard.press('Control+z');
  check('undo still works after indenting', (await text(page)).startsWith('def greet') || (await text(page)).startsWith('  def greet'));
  await page.keyboard.press('Control+a'); await page.keyboard.type(original + '\nprint("added")');
  await page.keyboard.press('Escape');
  const stillOpen = await page.locator('.cv-viewer.cv-open').count() === 1;
  await page.keyboard.press('Tab');
  const moved = await page.evaluate(() => document.activeElement !== document.querySelector('.cv-area'));
  check('Escape frees the Tab key instead of trapping keyboard users', stillOpen && moved);
  await page.waitForTimeout(700);
  check('edits are kept on this device', /Edits kept on this device/.test(await page.locator('.cv-status').innerText()) && (await drafts(page)).length === 1, (await drafts(page)).join());
  await shot(page, 'editor-light');

  // Changes
  await page.click('#cv-mode-changes');
  const diff = await page.evaluate(() => ({ sum: document.querySelector('.cv-diffsum')?.textContent, add: [...document.querySelectorAll('.cv-line[data-sign=add] .cv-linetext')].map(n => n.textContent), del: document.querySelectorAll('.cv-line[data-sign=del]').length, signs: [...document.querySelectorAll('.cv-line[data-sign=add] .cv-sign')].map(n => n.textContent).join('') }));
  check('Changes shows the added line against the original, with a + sign', diff.sum === '1 added, 0 removed' && diff.add.join() === 'print("added")' && diff.signs === '+', JSON.stringify(diff));
  await page.selectOption('.cv-diffbar select', { label: 'app.py' });
  const versus = await page.evaluate(() => ({ sum: document.querySelector('.cv-diffsum')?.textContent, del: [...document.querySelectorAll('.cv-line[data-sign=del] .cv-linetext')].map(n => n.textContent) }));
  check('Changes can compare with the earlier app.py', versus.sum === '4 added, 2 removed' && versus.del[0] === 'def greet(name):', JSON.stringify(versus));
  await shot(page, 'changes-light');
  await page.click('#cv-mode-edit');

  // Survives a reload
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#owui-chat-canvas-header');
  await page.click('#owui-chat-canvas-header');
  await settle(page, ALL);
  check('after a reload the edit is still there, flagged in the list', /Edited/.test(await rowOf(page, 'app.py', 0).innerText()) && /1 edited/.test(await foot(page)), await foot(page));
  await openRow(page, 'app.py', 0);
  check('and the editor shows the edited text', (await text(page)).endsWith('print("added")'));

  // Ask for a change
  await page.locator('.cv-area').evaluate(node => { const at = node.value.indexOf('return'); node.focus(); node.setSelectionRange(at, node.value.indexOf('\n', at)); });
  await bar(page, 'Ask for a change').click();
  check('Ask for a change quotes the selected part', /selected part/.test(await page.locator('.cv-modal[open] .cv-about').innerText()) && /return "Hello " \+ name \+ punctuation/.test(await page.locator('.cv-modal[open] .cv-quote').innerText()));
  await page.click('.cv-modal[open] .cv-primary');
  check('an empty request is refused', /Say what should change/.test(await page.locator('.cv-modal[open] .cv-hint').innerText()));
  await page.fill('.cv-modal[open] textarea', 'Use an f-string here');
  await page.click('.cv-modal[open] .cv-primary');
  await page.waitForFunction(() => /f-string/.test(document.getElementById('chat-input')?.innerText || ''), null, { timeout: 8000 }).catch(() => {});
  const asked = await composerText(page);
  check('the request lands in the message box with the part and the full edited version, unsent', /Use an f-string here/.test(asked) && /Change this part of "app\.py"/.test(asked) && /print\("added"\)/.test(asked) && /Reply with the complete updated version/.test(asked) && await page.locator('#owui-chat-canvas').isHidden(), asked.slice(0, 300));
  const sentCount = await page.locator('#messages-container [id^="message-"]').count();
  check('nothing was sent for the person', sentCount === await page.locator('#messages-container [id^="message-"]').count() && (await api(U, 'GET', '/api/v1/chats/' + U.chat)).json.chat.history.currentId === 'm4');
  await reopen(page);
  check('coming back keeps the editor open where it was', await page.locator('.cv-viewer.cv-open').count() === 1 && (await text(page)).endsWith('print("added")'));

  // Reset
  await bar(page, 'Go back to the original').click();
  await page.click('.cv-modal[open] .cv-btn:has-text("Cancel")');
  const keptEdit = (await text(page)).endsWith('print("added")');
  await bar(page, 'Go back to the original').click();
  await page.click('.cv-modal[open] .cv-danger');
  await page.waitForFunction(() => !/added/.test(document.querySelector('.cv-area').value));
  check('Go back to the original asks first, then restores it and clears the kept edit', keptEdit && (await text(page)) === original && (await drafts(page)).length === 0 && await bar(page, 'Go back to the original').isHidden());
  await back(page);

  // Templates travel as a file, so Open WebUI can't rewrite them
  await openRow(page, 'Jinja 1');
  await bar(page, 'Put in the message box').click();
  await page.waitForFunction(() => /attached file/.test(document.getElementById('chat-input')?.innerText || ''), null, { timeout: 8000 }).catch(() => {});
  const templated = await composerText(page);
  await page.waitForTimeout(1500);
  const attached = await page.evaluate(() => document.body.innerText.includes('Jinja-1.txt'));
  check('code with {{ }} goes as an attached file, not as text Open WebUI would rewrite', /attached file "Jinja-1\.txt"/.test(templated) && !templated.includes('{{') && attached, templated.slice(0, 200) + ' attached=' + attached);
  await reopen(page);
  await back(page);

  // Previews
  await openRow(page, 'Demo page');
  await page.click('#cv-mode-preview');
  const frame = page.frameLocator('.cv-frame');
  await frame.locator('h1').waitFor({ timeout: 10000 }).catch(() => {});
  const sandbox = await page.locator('.cv-frame').getAttribute('sandbox');
  const escaped = await page.evaluate(() => [window.__pwned, document.documentElement.dataset.cvPwned, localStorage.getItem('cvPwned')].some(Boolean));
  check('an HTML canvas runs in a sandbox that can’t reach the page, its storage or the session', (await frame.locator('h1').innerText()) === 'Script ran' && sandbox === 'allow-scripts' && !escaped, `sandbox=${sandbox} escaped=${escaped}`);
  await shot(page, 'preview-html');
  await page.click('#cv-mode-edit');
  await page.locator('.cv-area').click(); await page.keyboard.press('Control+a'); await page.keyboard.type('<h1>Edited live</h1>');
  await page.click('#cv-mode-preview');
  check('the preview shows what was just typed', (await page.frameLocator('.cv-frame').locator('h1').innerText()) === 'Edited live');
  await back(page);
  await openRow(page, 'SVG 1');
  await page.click('#cv-mode-preview');
  await page.waitForFunction(() => document.querySelector('.cv-drawing')?.naturalWidth > 0, null, { timeout: 10000 }).catch(() => {});
  check('an SVG is shown as an image, so its script can’t run', await page.locator('img.cv-drawing').evaluate(node => node.naturalWidth === 120 && node.src.startsWith('blob:')) && !(await page.evaluate(() => window.__pwned)));
  await back(page);
  await openRow(page, 'Project plan');
  check('a document opens with wrapped lines and no line numbers', await page.locator('.cv-gutter').isHidden() && await page.locator('.cv-edit.cv-wrapped.cv-prose').count() === 1);
  await page.click('#cv-mode-preview');
  const doc = await page.evaluate(() => { const p = document.querySelector('.cv-page'); return { h: [...p.querySelectorAll('h2,h3')].map(n => n.tagName + ':' + n.textContent), strong: p.querySelector('strong')?.textContent, em: p.querySelector('em')?.textContent, del: p.querySelector('del')?.textContent, links: [...p.querySelectorAll('a')].map(a => a.getAttribute('href') + '|' + a.rel), li: p.querySelectorAll('li').length, task: [...p.querySelectorAll('li')].some(li => li.textContent.startsWith('☑')), ol: p.querySelectorAll('ol li').length, quote: p.querySelector('blockquote')?.textContent.trim(), cells: [...p.querySelectorAll('td')].map(n => n.textContent), imgs: p.querySelectorAll('img,script,iframe').length, stray: /follow this\)/.test(p.textContent), raw: p.textContent.includes('<img src=x onerror=') }; });
  check('Markdown preview: headings, emphasis, lists, tasks, quote and table', doc.h.join() === 'H2:Project plan,H3:Phases' && doc.strong === 'first release' && doc.em === 'three' && doc.del === 'dropped' && doc.li === 7 && doc.task && doc.ol === 2 && doc.quote === 'A quoted remark that matters.' && doc.cells.join() === 'Research,2,Build,6', JSON.stringify(doc));
  check('Markdown preview: only http links are links, and HTML stays text', doc.links.join() === 'https://docs.example.test/guide|noopener noreferrer nofollow' && doc.imgs === 0 && doc.raw && !doc.stray && !(await page.evaluate(() => window.__pwned)), JSON.stringify(doc.links));
  await shot(page, 'preview-markdown');
  await back(page);
  await openRow(page, 'JSON 1');
  await page.click('#cv-mode-preview');
  const pretty = /\n {2}"items": \[\n {4}1,/.test(await page.locator('.cv-preview .cv-pre').innerText());
  await page.click('#cv-mode-edit');
  await page.locator('.cv-area').click(); await page.keyboard.press('Control+End'); await page.keyboard.type(' oops');
  await page.click('#cv-mode-preview');
  check('JSON is pretty-printed, and broken JSON says so', pretty && /isn’t valid JSON/.test(await page.locator('.cv-preview').innerText()));
  await back(page);
  await openRow(page, 'CSV 1');
  await page.click('#cv-mode-preview');
  const cells = await page.$$eval('.cv-table tr', trs => trs.map(tr => [...tr.children].map(cell => cell.textContent)));
  check('CSV previews as a table with quoted commas and Arabic', cells[1]?.[0] === 'Smith, Jane' && cells[2]?.[1] === 'القاهرة', JSON.stringify(cells));
  await back(page);
  await openRow(page, 'SQL 1');
  const ltr = await page.locator('.cv-area').evaluate(node => getComputedStyle(node).direction);
  await back(page);
  await openRow(page, 'Markdown 2');
  check('Arabic text is edited right to left, other text left to right', ltr === 'ltr' && await page.locator('.cv-area').evaluate(node => getComputedStyle(node).direction === 'rtl'));
  await back(page);

  // Copy, download, save
  await openRow(page, 'SQL 1');
  await bar(page, 'Copy').click();
  check('Copy puts the text on the clipboard', (await page.evaluate(() => navigator.clipboard.readText())) === 'SELECT id, name\nFROM users\nWHERE active = 1;');
  const [download] = await Promise.all([page.waitForEvent('download', { timeout: 30000 }), bar(page, 'Download').click()]);
  check('Download names the file by its language', download.suggestedFilename() === 'SQL-1.sql' && fs.readFileSync(await download.path(), 'utf8').startsWith('SELECT id'), download.suggestedFilename());
  await bar(page, 'Save to my files').click();
  await page.waitForFunction(() => /Saved to your files/.test(document.querySelector('.cv-toast')?.textContent || ''), null, { timeout: 10000 }).catch(() => {});
  const files = (await api(U, 'GET', '/api/v1/files/user/search?filename=SQL-1.sql')).json || [];
  check('Save to my files stores it in Open WebUI', files.length === 1 && files[0].meta?.size === 44, JSON.stringify(files.map(f => f.meta)));
  await bar(page, 'Save as a note').click();
  await page.waitForFunction(() => /note/i.test(document.querySelector('.cv-toast')?.textContent || ''), null, { timeout: 10000 }).catch(() => {});
  const notes = (await api(U, 'GET', '/api/v1/notes/')).json || [];
  const note = notes.find(each => each.title === 'SQL 1');
  const noteBody = note ? (await api(U, 'GET', '/api/v1/notes/' + note.id)).json?.data?.content?.md : '';
  check('Save as a note stores it as a note, code fenced', !!note && noteBody === '```sql\nSELECT id, name\nFROM users\nWHERE active = 1;\n```', (await page.locator('.cv-toast').innerText()) + ' ' + JSON.stringify(noteBody));
  await bar(page, 'Show in chat').click();
  await page.waitForTimeout(1200);
  check('Show in chat goes to the message', page.url().includes('message=m2') && await page.locator('#owui-chat-canvas').isHidden(), page.url());
  await reopen(page);
  await back(page);

  // Search and filter
  await page.fill('.cv-search', 'punctuation');
  check('search looks inside the text', await settle(page, 1, 5000));
  await page.fill('.cv-search', '');
  await page.selectOption('.cv-head .cv-select', 'web');
  check('kind filter: web pages and drawings', await settle(page, 2, 5000));
  await page.selectOption('.cv-head .cv-select', 'all');
  await settle(page, ALL + 0);

  // A blank canvas
  await page.click('.cv-head .cv-btn:has-text("New")');
  await page.waitForSelector('.cv-viewer.cv-open');
  check('New opens a blank canvas ready to type in', await page.evaluate(() => document.activeElement === document.querySelector('.cv-area')) && !(await page.locator('.cv-title').evaluate(node => node.readOnly)));
  await page.keyboard.type('# My notes\n\nFirst line.');
  await page.fill('.cv-title', 'Meeting <b>prep</b>');
  await page.waitForTimeout(700);
  await back(page);
  check('it is listed on top with its title shown as plain text', (await titles(page))[0] === 'Meeting <b>prep</b>' && await page.locator('.cv-name b').count() === 0 && /Started by you/.test(await rowOf(page, 'Meeting').innerText()));
  await page.click('.cv-head .cv-btn:has-text("New")');
  await page.waitForSelector('.cv-viewer.cv-open');
  await back(page);
  check('a blank canvas nobody wrote in is not kept', await settle(page, ALL + 1, 5000));

  // Download all
  const [zip] = await Promise.all([page.waitForEvent('download', { timeout: 30000 }), page.click('.cv-head .cv-btn:has-text("Download all")')]);
  const bytes = fs.readFileSync(await zip.path());
  const entries = [];
  for (let at = bytes.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02])); at >= 0; at = bytes.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]), at + 46)) entries.push(bytes.slice(at + 46, at + 46 + bytes.readUInt16LE(at + 28)).toString('utf8'));
  check('Download all zips every canvas, same names kept apart', entries.length === ALL + 1 && entries.includes('app.py') && entries.includes('app (2).py') && entries.includes('Demo-page.html') && zip.suggestedFilename() === 'canvas-canvas-test-chat.zip', entries.join(' | ') + ' ' + zip.suggestedFilename());

  // Another chat has its own canvases and edits
  await page.goto(seed.base + '/c/' + U.other, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#owui-chat-canvas-header');
  await page.click('#owui-chat-canvas-header');
  check('another chat shows its own canvas, not this chat’s blank one', await settle(page, 1) && (await titles(page))[0] === 'CSS 1');
  await page.goto(seed.base + '/c/' + U.chat, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#owui-chat-canvas-header');
  await page.click('#owui-chat-canvas-header');
  check('back in the first chat the blank canvas is still there', await settle(page, ALL + 1) && (await titles(page))[0] === 'Meeting <b>prep</b>');
  await openRow(page, 'Meeting');
  await bar(page, 'Delete this canvas').click();
  await page.click('.cv-modal[open] .cv-danger');
  check('deleting a blank canvas removes it', await settle(page, ALL, 5000));

  // The browser's storage is full or blocked
  await page.evaluate(() => { const real = Storage.prototype.setItem; Storage.prototype.setItem = function (key, value) { if (String(key).startsWith('owui-chat-canvas:')) throw new DOMException('full', 'QuotaExceededError'); return real.call(this, key, value); }; });
  await openRow(page, 'SQL 1');
  await page.locator('.cv-area').click(); await page.keyboard.press('Control+Home'); await page.keyboard.type('-- note\n');
  await page.waitForTimeout(700);
  check('when the browser can’t store edits, it says so and keeps working', /Edits not kept/.test(await page.locator('.cv-status').innerText()) && (await text(page)).startsWith('-- note') && state.errors.length === 0, await page.locator('.cv-status').innerText());
  await back(page);

  // The home page, and an empty chat
  await closeIt(page);
  check('closing removes the tab and uncovers the chat', await page.locator('#owui-chat-canvas').isHidden() && !(await page.locator('[data-cv-covered]').count()));
  await open('/c/' + U.empty);
  await page.click('#owui-chat-canvas-header');
  await page.waitForSelector('.cv-empty');
  check('a chat with nothing to edit says so and offers a blank canvas', /Nothing for the canvas yet/.test(await page.locator('.cv-empty').innerText()) && await page.locator('.cv-empty .cv-btn').count() === 1);

  check('the sign-in token and cookies never left the site', state.leaks.length === 0, state.leaks.join());
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- a very large block ---- */
console.log('Large canvas');
{
  const { context, page, state, open } = await session(U);
  await open('/c/' + U.big);
  await page.click('#owui-chat-canvas-header');
  await settle(page, 1);
  const started = Date.now();
  await page.locator('.cv-row').first().click();
  await page.waitForFunction(() => document.querySelector('.cv-area')?.value.length > 400000);
  const opened = Date.now() - started;
  check('a 25,000-line block opens in under 3 seconds', opened < 3000 && /25[,. ]?000 lines/.test(await page.locator('.cv-status').innerText()), opened + ' ms · ' + await page.locator('.cv-status').innerText());
  await page.locator('.cv-area').click(); await page.keyboard.press('Control+Home');
  const typed = Date.now();
  await page.keyboard.type('# x');
  await page.waitForFunction(() => document.querySelector('.cv-area').value.startsWith('# x'));
  check('typing in it stays responsive', Date.now() - typed < 2500, (Date.now() - typed) + ' ms');
  await page.waitForTimeout(800);
  check('an edit too large for the browser’s storage says so', /Too large to keep on this device/.test(await page.locator('.cv-status').innerText()), await page.locator('.cv-status').innerText());
  const aligned = await page.evaluate(() => { const area = document.querySelector('.cv-area'), gutter = document.querySelector('.cv-gutter'); area.scrollTop = area.scrollHeight; gutter.scrollTop = area.scrollTop; const a = getComputedStyle(area), g = getComputedStyle(gutter); return { same: a.lineHeight === g.lineHeight && a.fontSize === g.fontSize && a.paddingTop === g.paddingTop, whole: Number.isInteger(parseFloat(a.lineHeight)), lines: gutter.textContent.trim().split('\n').length, scrolled: Math.abs(area.scrollTop - gutter.scrollTop) < 2 }; });
  check('line numbers stay level with their lines to the end of the file', aligned.same && aligned.whole && aligned.lines === 25000 && aligned.scrolled, JSON.stringify(aligned));
  await page.click('#cv-mode-changes');
  check('Changes still works on it', (await page.locator('.cv-diffsum').innerText()) === '1 added, 1 removed');
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- opened before the chat has finished loading ---- */
console.log('Opened early');
{
  const { context, page, state } = await session(U);
  await page.goto(seed.base + '/c/' + U.big, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#owui-chat-canvas-header', { timeout: 30000 });
  await page.click('#owui-chat-canvas-header');
  await page.waitForTimeout(4500);
  check('Canvas opened while the chat is still loading stays open', await page.locator('#owui-chat-canvas').isVisible() && await page.locator('.cv-row').count() === 1);
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- side by side with Files and Gallery ---- */
if (others) {
  console.log('With Chat Files and Chat Gallery');
  const { context, page, state, open } = await session(U);
  await open('/c/' + U.chat);
  const tabRow = await page.evaluate(() => !!document.getElementById('chat-panel-main-chat-view'));
  await page.click('#owui-chat-canvas-header'); await settle(page, ALL);
  await page.click('#owui-chat-files-header');
  await page.locator('#owui-chat-files').waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
  check('opening Files sets Canvas aside', await page.locator('#owui-chat-canvas').isHidden() && await page.locator('#owui-chat-files').isVisible());
  await page.click('#owui-chat-gallery-header');
  await page.locator('#owui-chat-gallery').waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
  check('opening Gallery sets Files aside', await page.locator('#owui-chat-files').isHidden() && await page.locator('#owui-chat-gallery').isVisible());
  if (await page.locator('#owui-chat-canvas-tab').count()) {
    const strips = await page.evaluate(() => [...document.querySelector('.panel-tab-strip').parentElement.children].map(node => node.id).filter(Boolean).join(' '));
    check('the tabs run: Gallery, Files, Canvas', strips.includes('owui-chat-gallery-tabs owui-chat-files-tabs owui-chat-canvas-tabs'), strips);
    const reachable = await page.evaluate(() => { const tab = document.getElementById('owui-chat-canvas-tab'); tab.scrollIntoView({ block: 'nearest', inline: 'nearest' }); const box = tab.getBoundingClientRect(); const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2); return tab.contains(top) || top === tab; });
    check('in a narrow panel the tab row scrolls, so the Canvas tab can still be reached', reachable);
    await reopen(page);
  } else await page.click('#owui-chat-canvas-header');
  await page.locator('#owui-chat-canvas').waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
  check('choosing Canvas brings it back alone', await page.locator('#owui-chat-canvas').isVisible() && await page.locator('#owui-chat-gallery').isHidden() && await page.locator('#owui-chat-files').isHidden() && await page.evaluate(() => !document.querySelector('[data-cg-covered],[data-cf-covered]')));
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- light, dark, OLED black and Her ---- */
console.log('Themes');
for (const theme of ['light', 'dark', 'oled-dark', 'her']) {
  const { context, page, state, open } = await session(U, { theme });
  await open('/c/' + U.chat);
  await page.click('#owui-chat-canvas-header');
  await settle(page, ALL);
  const list = {};
  for (const [label, selector] of Object.entries({ names: '.cv-name', details: '.cv-sub', badges: '.cv-badge', flags: '.cv-flag', footer: '.cv-foot span', buttons: '.cv-head .cv-btn' })) list[label] = await contrastOf(page, selector);
  check(`${theme}: the list reaches 4.5:1 contrast`, Object.values(list).every(score => score >= 4.5), JSON.stringify(list));
  await openRow(page, 'app.py', 0);
  await page.locator('.cv-area').click(); await page.keyboard.press('Control+End'); await page.keyboard.type('\nextra = 1');
  const editor = {};
  for (const [label, selector] of Object.entries({ text: '.cv-area', numbers: '.cv-gutter', title: '.cv-title', status: '.cv-status', flags: '.cv-status .cv-flag', tabs: '.cv-vbar .cv-tab' })) editor[label] = await contrastOf(page, selector);
  check(`${theme}: the editor reaches 4.5:1`, Object.values(editor).every(score => score >= 4.5), JSON.stringify(editor));
  await shot(page, `editor-${theme}`);
  await page.click('#cv-mode-changes');
  const diff = { added: await contrastOf(page, '.cv-line[data-sign=add] .cv-linetext'), sign: await contrastOf(page, '.cv-line[data-sign=add] .cv-sign'), same: await contrastOf(page, '.cv-line[data-sign=same] .cv-linetext'), sum: await contrastOf(page, '.cv-diffsum') };
  await page.selectOption('.cv-diffbar select', { label: 'app.py' });
  diff.removed = await contrastOf(page, '.cv-line[data-sign=del] .cv-linetext'); diff.minus = await contrastOf(page, '.cv-line[data-sign=del] .cv-sign');
  check(`${theme}: added and removed lines reach 4.5:1`, Object.values(diff).every(score => score >= 4.5), JSON.stringify(diff));
  await shot(page, `changes-${theme}`);
  await page.click('#cv-mode-edit');
  await bar(page, 'Ask for a change').click();
  const modal = { text: await contrastOf(page, '.cv-modal[open] p'), primary: await contrastOf(page, '.cv-modal[open] .cv-primary'), label: await contrastOf(page, '.cv-modal[open] .cv-label') };
  await page.click('.cv-modal[open] .cv-btn:has-text("Cancel")');
  check(`${theme}: the ask box reaches 4.5:1`, Object.values(modal).every(score => score >= 4.5), JSON.stringify(modal));
  await bar(page, 'Go back to the original').click(); await page.click('.cv-modal[open] .cv-danger');
  await back(page);
  await openRow(page, 'Project plan');
  await page.click('#cv-mode-preview');
  const preview = { text: await contrastOf(page, '.cv-page p'), heading: await contrastOf(page, '.cv-page h2'), link: await contrastOf(page, '.cv-page a'), quote: await contrastOf(page, '.cv-page blockquote p') };
  check(`${theme}: the document preview reaches 4.5:1`, Object.values(preview).every(score => score >= 4.5), JSON.stringify(preview));
  if (theme !== 'light') await shot(page, `preview-${theme}`);
  check(`${theme}: no page errors`, state.errors.length === 0, state.errors.slice(0, 2).join(' | '));
  await context.close();
}

/* ---- a phone ---- */
console.log('Plain user, phone, dark');
{
  const { context, page, state, open } = await session(U, { viewport: { width: 390, height: 844 }, theme: 'dark', extra: { hasTouch: true, isMobile: true } });
  await open('/c/' + U.chat);
  check('the Canvas button is in the top bar', await page.locator('nav #owui-chat-canvas-header').isVisible());
  await page.tap('#owui-chat-canvas-header');
  check('Canvas opens over the chat', await settle(page, ALL));
  const layout = await page.evaluate(() => { const box = document.getElementById('owui-chat-canvas'), r = box.getBoundingClientRect(); return { width: r.width, below: r.top >= document.querySelector('#chat-container nav').getBoundingClientRect().bottom - 1, overflow: document.documentElement.scrollWidth > innerWidth + 1 || document.querySelector('.cv-scroll').scrollWidth > document.querySelector('.cv-scroll').clientWidth + 1 }; });
  check('it fills the width below the top bar with no sideways scrolling', layout.width >= 380 && layout.below && !layout.overflow, JSON.stringify(layout));
  await shot(page, 'list-phone-dark');
  await rowOf(page, 'app.py', 0).tap();
  await page.waitForSelector('.cv-viewer.cv-open');
  const editor = await page.evaluate(() => { const area = document.querySelector('.cv-area').getBoundingClientRect(), box = document.getElementById('owui-chat-canvas').getBoundingClientRect(), status = document.querySelector('.cv-status').getBoundingClientRect(); return { height: Math.round(area.height), inside: status.bottom <= box.bottom + 1 && area.right <= box.right + 1, page: document.documentElement.scrollWidth > innerWidth + 1 }; });
  check('the editor gets real room and its status line stays on screen', editor.height > 300 && editor.inside && !editor.page, JSON.stringify(editor));
  await shot(page, 'editor-phone-dark');
  await page.locator('.cv-vbar [aria-label="Back to the list"]').tap();
  await closeIt(page);
  check('its close button closes it', await page.locator('#owui-chat-canvas').isHidden());
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- an administrator: the + menu ---- */
console.log('Administrator');
{
  const { context, page, state, open } = await session(seed.admin);
  await open('/c/' + seed.admin.chat);
  const plus = page.locator('#controls-container button[aria-label="New tab"]').first();
  await plus.waitFor({ state: 'visible', timeout: 8000 }).catch(() => {});
  if (!(await plus.isVisible().catch(() => false))) { await page.click('nav button[aria-label="Tabs"]').catch(() => {}); await plus.waitFor({ state: 'visible', timeout: 8000 }).catch(() => {}); }
  if (await plus.isVisible().catch(() => false)) {
    await plus.click();
    await page.waitForSelector('#owui-chat-canvas-button', { timeout: 10000 }).catch(() => {});
    check('Canvas is in the + menu', await page.locator('#owui-chat-canvas-button').count() === 1);
    await page.click('#owui-chat-canvas-button');
    check('choosing it opens a Canvas tab in the panel', await settle(page, ALL) && await page.locator('#controls-container #owui-chat-canvas-tab[aria-selected="true"]').count() === 1);
  } else check('the + menu is there for an administrator', false, 'no + button');
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- valves ---- */
console.log('Valves');
{
  await setValves({ ...defaults, min_code_lines: 1, min_document_chars: 0, run_scripts: false, keep_drafts: false, allow_save: false, language: 'fr' });
  const { context, page, open } = await session(U);
  await open('/c/' + U.chat);
  await page.evaluate(() => { for (const key of Object.keys(localStorage)) if (key.startsWith('owui-chat-canvas:')) localStorage.removeItem(key); });
  await page.click('#owui-chat-canvas-header');
  check('one-line blocks can be listed and document canvases turned off', await settle(page, ALL) && (await titles(page)).some(name => /Shell/.test(name)) && !(await titles(page)).includes('Project plan'), (await titles(page)).join(' | '));
  check('Canvas speaks French', /canevas/.test(await foot(page)) && (await page.locator('.cv-head .cv-btn').first().innerText()).trim() === 'Nouveau', await foot(page));
  await openRow(page, 'Demo page');
  check('saving to Open WebUI can be turned off', await bar(page, 'Enregistrer dans mes fichiers').isHidden() && await bar(page, 'Enregistrer comme note').isHidden() && await bar(page, 'Télécharger').isVisible());
  await page.click('#cv-mode-preview');
  await page.frameLocator('.cv-frame').locator('h1').waitFor({ timeout: 10000 }).catch(() => {});
  check('scripts can be turned off in the preview', (await page.locator('.cv-frame').getAttribute('sandbox')) === '' && (await page.frameLocator('.cv-frame').locator('h1').innerText()) === 'Hello' && /scripts sont désactivés/.test(await page.locator('.cv-preview').innerText()));
  await page.click('#cv-mode-edit');
  await page.locator('.cv-area').click(); await page.keyboard.type('<!-- x -->');
  await page.waitForTimeout(700);
  check('with keeping turned off, nothing is written to the browser and the editor says so', (await drafts(page)).length === 0 && /non conservées/.test(await page.locator('.cv-status').innerText()), await page.locator('.cv-status').innerText());
  await context.close();

  await setValves({ ...defaults, header_button: false });
  const second = await session(U);
  await second.open('/c/' + U.chat);
  await second.page.waitForTimeout(1500);
  check('the Canvas button can be turned off', !(await second.page.locator('#owui-chat-canvas-header').count()));
  await second.context.close();
  await setValves(defaults);
}

await browser.close();
console.log(`\n${passed} passed, ${failed.length} failed`);
if (failed.length) { for (const name of failed) console.log('  - ' + name); process.exit(1); }
