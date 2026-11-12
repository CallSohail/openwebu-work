// Browser checks for Chat Inspector against a throwaway Open WebUI wired to tests/fake_model.py and seeded by seed.py.
//
//   node tests/e2e.mjs seed.json [screenshot-folder]
//
// It sends messages and changes valves on that instance. Needs Playwright with Chromium.
import { createRequire } from 'module';
import fs from 'fs';

const { chromium } = createRequire(import.meta.url)('playwright');
const seed = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const shots = process.argv[3] || null;
if (shots) fs.mkdirSync(shots, { recursive: true });
const others = ['chat_canvas', 'chat_files', 'chat_gallery'].every(id => seed.installed.includes(id));

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
const defaults = { context_windows: 'fake-mini = 4000', warn_percent: 80, show_capabilities: true, header_button: false, language: 'auto', platform_name: '', open_as: 'popup', show_in_chat_menu: true, show_in_plus_menu: false };
const setValves = valves => api(seed.admin, 'POST', '/api/v1/functions/id/chat_inspector/valves/update', { ...defaults, ...valves });
const A = seed.admin, U = seed.user;
const SYSTEM = 'You are a helpful assistant for students at a French university. Answer clearly and cite sources when you can.';

const browser = await chromium.launch();
async function session(account, { viewport = { width: 1400, height: 900 }, theme = 'light', extra = {} } = {}) {
  const context = await browser.newContext({ viewport, ...extra });
  const page = await context.newPage();
  const state = { errors: [], leaks: [], chatReads: 0 };
  page.on('pageerror', error => { if (!/Failed to read a named property 'addEventListener' from 'Window'/.test(String(error))) state.errors.push(String(error)); });
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin === new URL(seed.base).origin) { if (/^\/api\/v1\/chats\/[0-9a-f-]{36}$/.test(url.pathname) && request.method() === 'GET') state.chatReads++; return; }
    if (['data:', 'blob:', 'about:'].includes(url.protocol)) return;
    if (request.headers().authorization) state.leaks.push(url.hostname);
  });
  const { version } = await (await fetch(seed.base + '/api/config')).json();
  await api(account, 'POST', '/api/v1/users/user/settings/update', { ui: { version, ...(account === U ? { system: SYSTEM } : {}) } });
  await context.request.post(seed.base + '/api/v1/auths/signin', { data: { email: account.email, password: seed.password } });
  await context.addInitScript(([token, theme]) => { try { localStorage.token = token; localStorage.theme = theme; } catch {} }, [account.token, theme]);
  const open = async path => {
    await page.goto(seed.base + path, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.documentElement.dataset.chatInspector === '1', null, { timeout: 30000 });
    await page.waitForSelector('#chat-input', { timeout: 30000 });
  };
  // The way in: the + menu where there is one, otherwise the optional button
  const openContext = async () => {
    const plus = page.locator('#controls-container button[aria-label="New tab"]').first();
    await plus.waitFor({ state: 'visible', timeout: 6000 }).catch(() => {});
    if (!(await plus.isVisible().catch(() => false))) { await page.click('nav button[aria-label="Tabs"]', { timeout: 3000 }).catch(() => {}); await plus.waitFor({ state: 'visible', timeout: 6000 }).catch(() => {}); }
    if (await plus.isVisible().catch(() => false)) {
      await plus.click();
      await page.waitForSelector('#owui-chat-inspector-button', { timeout: 8000 });
      await page.click('#owui-chat-inspector-button');
    } else await page.click('#owui-chat-inspector-header', { timeout: 8000 });
    await ready();
  };
  // Ready when the panel shows the chat in the address bar and has finished reading it
  // The way in every account has: the chat's ⋯ menu
  const openFromMenu = async () => {
    await page.locator('#chat-context-menu-button').filter({ visible: true }).first().click();
    await page.waitForSelector('#owui-chat-inspector-menu-item', { timeout: 8000 });
    await page.click('#owui-chat-inspector-menu-item');
    await ready();
  };
  const ready = () => page.waitForFunction(() => { const d = document.getElementById('owui-chat-inspector'), id = (/^\/c\/([^/?#]+)/.exec(location.pathname) || [])[1] || ''; return d && !d.hidden && d.dataset.state === 'ready' && (d.dataset.chat === id || !d.dataset.chat && d.querySelector('.ci-notice')); }, null, { timeout: 20000 }).then(() => true, () => false);
  return { context, page, state, open, openContext, openFromMenu, ready };
}
const shot = (page, name) => shots ? page.screenshot({ path: `${shots}/${name}.png` }) : null;
const panelText = page => page.locator('#owui-chat-inspector .ci-body').innerText().catch(() => '');
const legend = page => page.$$eval('#owui-chat-inspector .ci-legend li', rows => Object.fromEntries(rows.map(row => [row.querySelector('.ci-swatch').dataset.c, { tokens: +row.querySelector('.ci-lvalue').textContent.replace(/[^\d]/g, ''), kind: row.querySelector('.ci-tag')?.dataset.kind || '' }])));
const facts = page => page.$$eval('#owui-chat-inspector .ci-facts dt', dts => Object.fromEntries(dts.map(dt => [dt.textContent, dt.nextElementSibling.textContent])));
const cells = page => page.$$eval('#owui-chat-inspector .ci-cell', nodes => nodes.reduce((all, node) => { all[node.dataset.c] = (all[node.dataset.c] || 0) + 1; return all; }, {}));
const big = page => page.locator('#owui-chat-inspector .ci-big').innerText().catch(() => '');
// Moves to another chat inside the app, as a click in the sidebar does, so the open tab follows
const goChat = async (page, ready, id) => { await page.evaluate(href => { const a = document.createElement('a'); a.href = href; document.body.append(a); a.click(); a.remove(); }, '/c/' + id); await page.waitForURL('**/c/' + id, { timeout: 10000 }); };

// An element scrolled out of view has nothing painted under it to measure, so it is brought into view first
const contrastOf = async (page, selector) => {
  await page.evaluate(selector => { const nodes = [...document.querySelectorAll(selector)]; const seen = nodes.some(node => { const box = node.getBoundingClientRect(); return box.height && box.top >= 0 && box.bottom <= innerHeight; }); if (!seen && nodes[0]) nodes[0].scrollIntoView({ block: 'center' }); }, selector);
  return measure(page, selector);
};
const measure = (page, selector) => page.evaluate(selector => {
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
      if (each.closest('[data-ci-covered],[data-cv-covered],[data-cf-covered],[data-cg-covered]')) continue;
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

await setValves({});

/* ---- the + menu, for an account that has one ---- */
console.log('Administrator, wide screen' + (others ? ', with Canvas, Files and Gallery' : ''));
{
  const { context, page, state, open, openContext, ready } = await session(A, { extra: { permissions: ['clipboard-read', 'clipboard-write'] } });
  await open('/c/' + A.usage);
  await page.waitForTimeout(1500);
  check('by default there is no button beside the tabs', await page.locator('#owui-chat-inspector-header').count() === 0);
  const plusButton = page.locator('#controls-container button[aria-label="New tab"]').first();
  if (!(await plusButton.isVisible().catch(() => false))) await page.click('nav button[aria-label="Tabs"]', { timeout: 3000 }).catch(() => {});
  await plusButton.click(); await page.waitForTimeout(600);
  check('by default Context is not in the + menu', await page.locator('#owui-chat-inspector-button').count() === 0);
  await page.keyboard.press('Escape');
  await setValves({ show_in_plus_menu: true });
  await open('/c/' + A.usage);
  await openContext();
  check('Context opens from the + menu as a tab in the panel', await page.locator('#controls-container #owui-chat-inspector-tab[aria-selected="true"]').count() === 1 && await page.locator('#owui-chat-inspector').isVisible());
  check('the chat under it is hidden and unreachable', await page.evaluate(() => [...document.querySelectorAll('[data-ci-covered]')].length > 0 && [...document.querySelectorAll('[data-ci-covered]')].every(node => node.inert)));
  const parts = await legend(page);
  check('the breakdown is fitted to the model’s own count: 1,180 read, then the answer and what came after', /1,371 of 8,192 tokens/.test(await big(page)) && parts.extras?.kind === 'inferred' && parts.system.tokens + parts.extras.tokens + parts.messages.tokens === 1180 && parts.latest.tokens > 100 && parts.newer.tokens > 0, JSON.stringify(parts) + ' ' + await big(page));
  check('the window comes from the provider, and free space adds up to it', /reported by the provider/.test(await panelText(page)) && parts.free.tokens === 8192 - 1371);
  const grid = await cells(page);
  check('the grid has 100 squares, coloured by part, 17 used', Object.values(grid).reduce((a, b) => a + b, 0) === 100 && grid.free === 83 && grid.system === 1 && grid.newer === 1, JSON.stringify(grid));
  const last = await facts(page);
  check('the last answer shows read, written, reasoning and cached tokens', last['Read by the model'] === '1,180 tokens' && last.Written === '210 tokens' && last['of which reasoning'] === '40 tokens' && last['Read from cache'] === '512 tokens' && last.Total === '1,390 tokens', JSON.stringify(last));
  check('the conversation totals add up the model’s counts', last['Answers the model counted'] === '2 / 2' && last['Tokens read in all'] === '1,820 tokens' && last['Tokens written in all'] === '470 tokens', JSON.stringify(last));
  check('notes say the numbers are fitted to the model’s count, tuned for GPT-4o, and mention sources and reasoning', /counted its own prompt for the last answer \(1,180 tokens\)/.test(await panelText(page)) && /tuned for GPT-4o/.test(await panelText(page)) && /2 retrieved sources|1 retrieved sources/.test(await panelText(page)) && /Reasoning is usually not sent back/.test(await panelText(page)));
  check('the model’s set-up is listed', /Reads images/.test(await panelText(page)) && /Searches the web/.test(await panelText(page)));
  check('it says how many more exchanges fit', /About \d+ more exchanges/.test(await panelText(page)));
  await shot(page, 'counted-light');
  const reads = state.chatReads;
  await page.waitForTimeout(8000);
  check('it doesn’t poll while nothing changes', state.chatReads === reads, `${reads} -> ${state.chatReads}`);

  // Estimated only
  await goChat(page, ready, A.estimated); await ready();
  const est = await legend(page);
  check('without the model’s count everything is estimated, and the administrator’s window applies', Object.values(est).filter(p => p.kind).every(p => p.kind === 'estimated') && !est.extras && /of 4,000 tokens/.test(await big(page)) && /set by your administrator/.test(await panelText(page)), JSON.stringify(est) + ' ' + await big(page));
  check('the note says tools and documents aren’t included, and counts the image', /hasn’t reported its own count/.test(await panelText(page)) && /1 images in this chat aren’t counted|1 image/.test(await panelText(page)) && /general tuning/.test(await panelText(page)));
  const estimate = est.messages.tokens + est.latest.tokens;
  check('French, Arabic and Chinese text is counted far above characters ÷ 4 would give for Chinese', estimate > 300, String(estimate));

  // Nearly full, then over
  await goChat(page, ready, A.full); await ready();
  check('a nearly full chat says it is getting full', /Getting full/.test(await panelText(page)) && (await page.locator('.ci-state').getAttribute('data-state')) === 'warn', await big(page));
  await shot(page, 'full-light');
  await goChat(page, ready, A.over); await ready();
  check('a chat past the window says so and how far over', /Over the window/.test(await panelText(page)) && /over the window/.test(await panelText(page)) && (await page.locator('.ci-state').getAttribute('data-state')) === 'full' && /Not even one more exchange/.test(await panelText(page)));
  const overGrid = await cells(page);
  check('the grid never shows more than 100 squares', Object.values(overGrid).reduce((a, b) => a + b, 0) === 100 && !overGrid.free, JSON.stringify(overGrid));

  // Two models
  await goChat(page, ready, A.multi); await ready();
  check('a chat with two models offers a choice, starting with the shown answer', await page.locator('.ci-head .ci-select').isVisible() && (await page.locator('.ci-head .ci-select').inputValue()) === 'fake-gpt-4o' && /several models at once/.test(await panelText(page)));
  const firstModel = (await facts(page))['Read by the model'];
  await page.selectOption('.ci-head .ci-select', 'llama3.1:8b');
  await ready(); await page.waitForTimeout(500);
  const second = await facts(page);
  check('choosing the other model shows its own answer, window and tuning', firstModel === '500 tokens' && second['Read by the model'] === '520 tokens' && second.Model === 'llama3.1:8b' && /of 131,072 tokens/.test(await big(page)) && /tuned for Llama/.test(await panelText(page)), firstModel + ' ' + JSON.stringify(second) + ' ' + await big(page));

  // Summarised history, unknown model
  await goChat(page, ready, A.summary); await ready();
  const summary = await legend(page);
  check('a summarised chat counts the summary, not the old messages', summary.summary?.tokens > 5 && summary.messages.tokens < 200 && /Summarised by EveGPT/.test(await panelText(page)), JSON.stringify(summary));
  await goChat(page, ready, A.unknown); await ready();
  check('a model that is gone: no window, no %, and it says so', !/ of [\d,]+ tokens/.test(await big(page)) && /tokens in use/.test(await big(page)) && /isn’t known/.test(await panelText(page)) && /isn’t in the list of models/.test(await panelText(page)) && (await cells(page)).free === undefined);

  // A real answer, streamed by the fake model, and Context updating by itself
  await goChat(page, ready, A.streaming); await ready();
  check('an empty chat says there is nothing yet', /Nothing in this chat yet/.test(await panelText(page)));
  // Where Context covers the chat, go back to the chat to type; in a side panel the chat is already beside it.
  // An empty chat sometimes has no chat tab yet, so then Context is closed and opened again after sending.
  let reopenAfter = false;
  if (await page.locator('#chat-input').isVisible().catch(() => false) && !(await page.locator('#chat-panel-main-chat-view[data-ci-covered], #chat-pane[data-ci-covered]').count())) {}
  else if (await page.locator('#chat-panel-main-chat').isVisible({ timeout: 3000 }).catch(() => false)) await page.click('#chat-panel-main-chat');
  else { await page.click('#owui-chat-inspector-tabs [aria-label="Close Context"]'); reopenAfter = true; }
  await page.locator('#chat-input').click();
  await page.keyboard.type('Explain enrolment please SLOW');
  await page.keyboard.press('Enter');
  await page.waitForSelector('#messages-container [id^="message-"]', { timeout: 15000 });
  await page.waitForTimeout(1200);
  if (reopenAfter) await openContext(); else await page.click('#owui-chat-inspector-tab').catch(() => {});
  await page.waitForFunction(() => /being written/.test(document.querySelector('.ci-updated')?.textContent || ''), null, { timeout: 8000 }).catch(() => {});
  const during = await page.locator('.ci-updated').innerText().catch(() => '');
  await page.waitForFunction(() => /counted its own prompt/.test(document.querySelector('#owui-chat-inspector .ci-body')?.textContent || ''), null, { timeout: 30000 }).catch(() => {});
  const after = await facts(page);
  check('while the answer streams it says so, then updates by itself with the model’s real count', /being written/.test(during) && /\d+ tokens/.test(after['Read by the model'] || '') && after['Answers the model counted'] === '1 / 1', during + ' ' + JSON.stringify(after));
  const live = await legend(page);
  check('the fake model added 300 tokens of its own; they show up as the worked-out share', live.extras && live.extras.tokens >= 250 && live.extras.tokens <= 340, JSON.stringify(live));

  // Taking turns with the others
  if (others) {
    await goChat(page, ready, A.usage);
    await ready();
    const plus = page.locator('#controls-container button[aria-label="New tab"]').first();
    await plus.click();
    await page.waitForSelector('#owui-chat-canvas-button');
    const menu = await page.$$eval('.app-dropdown-menu > button', buttons => buttons.map(b => b.id).filter(Boolean));
    check('the + menu lists Canvas, Files, Gallery and Context', ['owui-chat-canvas-button', 'owui-chat-files-button', 'owui-chat-gallery-button', 'owui-chat-inspector-button'].every(id => menu.includes(id)), menu.join());
    await page.click('#owui-chat-canvas-button');
    await page.locator('#owui-chat-canvas').waitFor({ state: 'visible', timeout: 8000 }).catch(() => {});
    check('opening Canvas sets Context aside', await page.locator('#owui-chat-inspector').isHidden() && await page.locator('#owui-chat-canvas').isVisible());
    const strips = await page.evaluate(() => [...document.querySelector('.panel-tab-strip').parentElement.children].map(node => node.id).filter(id => id.endsWith('-tabs')).join(' '));
    check('the tabs keep their order, Context last', /owui-chat-canvas-tabs owui-chat-inspector-tabs$/.test(strips), strips);
    await page.click('#owui-chat-inspector-tab');
    await page.locator('#owui-chat-inspector').waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
    check('choosing Context brings it back alone', await page.locator('#owui-chat-inspector').isVisible() && await page.locator('#owui-chat-canvas').isHidden() && await page.evaluate(() => !document.querySelector('[data-cv-covered],[data-cf-covered],[data-cg-covered]')));
  }
  await page.click('#owui-chat-inspector-tabs [aria-label="Close Context"]');
  check('closing removes the tab and uncovers the chat', await page.locator('#owui-chat-inspector').isHidden() && !(await page.locator('[data-ci-covered]').count()));
  check('the sign-in token never left the site', state.leaks.length === 0, state.leaks.join());
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- a plain user: the chat's ⋯ menu and the pop-up ---- */
console.log('Plain user, the ⋯ menu and the pop-up');
{
  await setValves({});
  const first = await session(U);
  const { page } = first;
  await first.open('/c/' + U.usage);
  await page.waitForTimeout(2000);
  const hasPlus = await page.locator('#controls-container button[aria-label="New tab"]').count();
  check('a plain user has no + menu and, by default, no Context button beside the tabs', !hasPlus && !(await page.locator('#owui-chat-inspector-header').count()));
  await page.locator('#chat-context-menu-button').filter({ visible: true }).first().click();
  await page.waitForSelector('#owui-chat-inspector-menu-item', { timeout: 8000 }).catch(() => {});
  const items = await page.$$eval('body > [role="menu"] .app-dropdown-menu > button', buttons => buttons.map(b => b.id === 'owui-chat-inspector-menu-item' ? '[Context]' : b.textContent.trim()));
  check('Context is in the chat’s ⋯ menu, right after Overview', items[items.indexOf('Overview') + 1] === '[Context]', items.join(' | '));
  check('it looks like the menu’s own items', await page.evaluate(() => { const ours = document.getElementById('owui-chat-inspector-menu-item'), theirs = ours.previousElementSibling; const a = getComputedStyle(ours), b = getComputedStyle(theirs); return a.height === b.height && a.fontSize === b.fontSize && a.paddingLeft === b.paddingLeft && a.gap === b.gap; }));
  await page.click('#owui-chat-inspector-menu-item');
  await first.ready();
  // Open WebUI's menu fades out after a choice
  await page.waitForFunction(() => !document.querySelector('body > [role="menu"]'), null, { timeout: 3000 }).catch(() => {});
  const pop = await page.evaluate(() => { const d = document.getElementById('owui-chat-inspector'), r = d.getBoundingClientRect(), form = document.getElementById('chat-input')?.closest('form')?.getBoundingClientRect(); return { role: d.getAttribute('role'), modal: d.getAttribute('aria-modal'), pop: d.classList.contains('ci-pop'), menuClosed: !document.querySelector('body > [role="menu"]'), covered: document.querySelectorAll('[data-ci-covered]').length, onScreen: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight, clearOfInput: !form || r.bottom <= form.top || r.right <= form.left || r.left >= form.right, width: Math.round(r.width), bg: getComputedStyle(d).backgroundColor, focus: document.activeElement?.getAttribute('aria-label') }; });
  check('choosing it opens a pop-up, not a tab, and the menu closes', pop.role === 'dialog' && pop.modal === 'false' && pop.pop && pop.menuClosed && pop.covered === 0 && await page.locator('#owui-chat-inspector-tab').count() === 0, JSON.stringify(pop));
  check('the pop-up is on screen, clear of the message box, solid, with focus on its close button', pop.onScreen && pop.clearOfInput && pop.width > 300 && pop.bg !== 'rgba(0, 0, 0, 0)' && pop.focus === 'Close Context', JSON.stringify(pop));
  check('it shows this chat’s numbers', /1,371 of 8,192 tokens/.test(await big(page)));
  const text = await panelText(page);
  check('the wording uses the platform’s own name, EveGPT, not Open WebUI', /EveGPT/.test(text) && !/Open WebUI/.test(text) && /instructions EveGPT added/.test(text), text.match(/[^\n]*(EveGPT|Open WebUI)[^\n]*/g)?.join(' | '));
  await shot(page, 'popup-light');
  // The chat stays usable with the pop-up open, and the pop-up follows the answer
  await page.locator('#chat-input').click();
  await page.keyboard.type('Tell me more SLOW');
  check('the message box can be typed in with the pop-up open', /Tell me more SLOW/.test(await page.locator('#chat-input').innerText().catch(() => '')) && await page.locator('#owui-chat-inspector').isVisible());
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => /being written/.test(document.querySelector('.ci-updated')?.textContent || ''), null, { timeout: 10000 }).catch(() => {});
  const during = await page.locator('.ci-updated').innerText().catch(() => '');
  await page.waitForFunction(() => /3 \/ 3/.test(document.querySelector('#owui-chat-inspector .ci-body')?.textContent || ''), null, { timeout: 30000 }).catch(() => {});
  check('while the answer streams the pop-up says so, then updates by itself', /being written/.test(during) && (await facts(page))['Answers the model counted'] === '3 / 3', during + ' ' + JSON.stringify(await facts(page)));
  await page.keyboard.press('Escape');
  await page.locator('#owui-chat-inspector').waitFor({ state: 'hidden', timeout: 3000 }).catch(() => {});
  check('Escape closes it and gives focus back to the ⋯ button', await page.locator('#owui-chat-inspector').isHidden() && await page.evaluate(() => document.activeElement?.id === 'chat-context-menu-button'));
  await first.openFromMenu();
  await page.click('#owui-chat-inspector .ci-head [aria-label="Close Context"]');
  check('its close button closes it too', await page.locator('#owui-chat-inspector').isHidden());
  await first.openFromMenu();
  await goChat(page, first.ready, U.over); await first.ready();
  check('moving to another chat keeps the pop-up open and shows that chat', await page.locator('#owui-chat-inspector').isVisible() && /9,902 of 8,192|of 8,192 tokens/.test(await big(page)) && /Over the window/.test(await panelText(page)), await big(page));
  check('no page errors', first.state.errors.length === 0, first.state.errors.slice(0, 3).join(' | '));
  await first.context.close();
}

/* ---- the optional button beside the tabs ---- */
console.log('Plain user, with the button beside the tabs');
{
  await setValves({ header_button: true });
  const { context, page, state, open, openContext } = await session(U);
  await open('/c/' + U.usage);
  await page.waitForSelector('#owui-chat-inspector-header', { timeout: 10000 }).catch(() => {});
  if (others) {
    const order = await page.evaluate(() => [...document.getElementById('owui-chat-inspector-header').parentElement.children].map(node => node.id).filter(id => id.endsWith('-header')).join(' '));
    const inBar = await page.evaluate(() => !!document.getElementById('owui-chat-inspector-header').closest('#controls-container'));
    check('with the button turned on, the four buttons keep a fixed order', order === (inBar ? 'owui-chat-inspector-header owui-chat-canvas-header owui-chat-files-header owui-chat-gallery-header' : 'owui-chat-gallery-header owui-chat-files-header owui-chat-canvas-header owui-chat-inspector-header'), order);
    const moves = await page.evaluate(() => new Promise(done => { const host = document.getElementById('owui-chat-inspector-header').parentElement; let n = 0; const w = new MutationObserver(l => { n += l.length; }); w.observe(host, { childList: true }); setTimeout(() => { w.disconnect(); done(n); }, 1500); }));
    check('and stop moving', moves === 0, moves + ' moves');
  } else check('with the button turned on it appears', await page.locator('#owui-chat-inspector-header').isVisible());
  await openContext();
  const parts = await legend(page);
  check('the person’s own system prompt is counted when the chat has none of its own', parts.system.tokens >= 5, JSON.stringify(parts));
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- light, dark, OLED black and Her ---- */
console.log('Themes');
for (const theme of ['light', 'dark', 'oled-dark', 'her']) {
  const { context, page, state, open, openFromMenu, ready } = await session(A, { theme });
  await open('/c/' + A.usage);
  await openFromMenu();
  const scores = {};
  for (const [label, selector] of Object.entries({ close: '.ci-head [aria-label="Close Context"]', heading: '.ci-heading', big: '.ci-big strong', badge: '.ci-state', sub: '.ci-summary .ci-sub', updated: '.ci-updated', legend: '.ci-lname', values: '.ci-lvalue', shares: '.ci-lshare', tags: '.ci-tag', titles: '.ci-section h3', facts: '.ci-facts dt' })) scores[label] = await contrastOf(page, selector);
  check(`${theme}: every text reaches 4.5:1 contrast`, Object.values(scores).every(score => score >= 4.5), JSON.stringify(scores));
  await shot(page, `counted-${theme}`);
  await goChat(page, ready, A.full); await ready();
  const warn = { alert: await contrastOf(page, '.ci-alert'), badge: await contrastOf(page, '.ci-state') };
  check(`${theme}: the warning reaches 4.5:1`, Object.values(warn).every(score => score >= 4.5), JSON.stringify(warn));
  await goChat(page, ready, A.over); await ready();
  const full = { alert: await contrastOf(page, '.ci-alert'), badge: await contrastOf(page, '.ci-state') };
  check(`${theme}: the over-the-window warning reaches 4.5:1`, Object.values(full).every(score => score >= 4.5), JSON.stringify(full));
  if (theme === 'dark') await shot(page, 'over-dark');
  check(`${theme}: no page errors`, state.errors.length === 0, state.errors.slice(0, 2).join(' | '));
  await context.close();
}

/* ---- a phone: a sheet from the bottom ---- */
console.log('Phone, dark');
{
  await setValves({});
  const { context, page, state, open, openFromMenu } = await session(U, { viewport: { width: 390, height: 844 }, theme: 'dark', extra: { hasTouch: true, isMobile: true } });
  await open('/c/' + U.usage);
  await openFromMenu();
  const sheet = await page.evaluate(() => { const d = document.getElementById('owui-chat-inspector'), r = d.getBoundingClientRect(), grid = document.querySelector('.ci-grid'); return { sheet: d.classList.contains('ci-sheet'), backdrop: !document.querySelector('.ci-backdrop').hidden, width: Math.round(r.width), bottom: Math.round(innerHeight - r.bottom), top: Math.round(r.top), overflow: document.documentElement.scrollWidth > innerWidth + 1 || document.querySelector('.ci-body').scrollWidth > document.querySelector('.ci-body').clientWidth + 1, columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length }; });
  check('on a phone it opens as a sheet from the bottom, full width, with a 10-column grid', sheet.sheet && sheet.backdrop && sheet.width >= 385 && sheet.bottom <= 1 && sheet.top > 80 && !sheet.overflow && sheet.columns === 10, JSON.stringify(sheet));
  await page.waitForTimeout(500);
  await shot(page, 'phone-dark');
  await page.mouse.click(195, 40);
  await page.locator('#owui-chat-inspector').waitFor({ state: 'hidden', timeout: 3000 }).catch(() => {});
  check('a tap outside the sheet closes it', await page.locator('#owui-chat-inspector').isHidden() && await page.locator('.ci-backdrop').isHidden());
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- valves ---- */
console.log('Valves');
{
  await setValves({ language: 'fr', context_windows: 'fake-gpt-4o = 2k, fake-mini = 4000', warn_percent: 50, show_capabilities: false });
  const { context, page, open, openFromMenu } = await session(A);
  await open('/c/' + A.usage);
  await openFromMenu();
  const text = await panelText(page);
  check('an administrator’s window overrides the provider’s, with k for thousands', /sur 2\s000 jetons/.test(await big(page)) && /définie par votre administrateur/.test(text), await big(page));
  check('Context speaks French', /Ce qui occupe le contexte/i.test(text) && /Dernière réponse/i.test(text) && (await page.locator('#owui-chat-inspector .ci-heading').innerText()).trim() === 'Contexte' && (await page.locator('#owui-chat-inspector-menu-item').count()) === 0);
  check('the warning level follows the valve', (await page.locator('.ci-state').getAttribute('data-state')) !== 'ok');
  check('the model’s set-up can be hidden', !/Lit les images/.test(text));
  await context.close();

  await setValves({ platform_name: 'Campus AI' });
  const named = await session(A);
  await named.open('/c/' + A.usage);
  await named.openFromMenu();
  check('platform_name overrides the server’s name', /instructions Campus AI added/.test(await panelText(named.page)) && !/EveGPT/.test(await panelText(named.page)));
  await named.context.close();

  await setValves({ open_as: 'tab' });
  const tabbed = await session(A);
  await tabbed.open('/c/' + A.usage);
  await tabbed.openFromMenu();
  // A tab in the panel, or, where no panel is open (as on 0.13.0 at first), the full view over the chat
  check('open_as = tab makes the ⋯ menu open the full view instead', !(await tabbed.page.locator('#owui-chat-inspector.ci-pop').count()) && (await tabbed.page.locator('#owui-chat-inspector-tab[aria-selected="true"]').count() === 1 || await tabbed.page.locator('#owui-chat-inspector.ci-solo').isVisible()));
  await tabbed.context.close();

  await setValves({ show_in_chat_menu: false });
  const hidden = await session(U);
  await hidden.open('/c/' + U.usage);
  await hidden.page.locator('#chat-context-menu-button').filter({ visible: true }).first().click();
  await hidden.page.waitForTimeout(800);
  check('show_in_chat_menu = off keeps it out of the ⋯ menu', !(await hidden.page.locator('#owui-chat-inspector-menu-item').count()) && await hidden.page.locator('body > [role="menu"]').count() > 0);
  await hidden.context.close();
  await setValves({});
}

await browser.close();
console.log(`\n${passed} passed, ${failed.length} failed`);
if (failed.length) { for (const name of failed) console.log('  - ' + name); process.exit(1); }
