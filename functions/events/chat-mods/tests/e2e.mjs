// Browser tests for Mods against a real, seeded Open WebUI.
//
//   python tests/fake_model.py 9996 &
//   python tests/seed.py http://127.0.0.1:8080 chat_mods.py > seed.json
//   node tests/e2e.mjs seed.json [screenshot-dir]
//
// Never point it at an instance people use: it installs mods and changes settings.
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const seed = JSON.parse(fs.readFileSync(process.argv[2] || 'seed.json', 'utf8'));
const shots = process.argv[3] || '';
if (shots) fs.mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
let failures = 0, passes = 0;
const check = (ok, label) => { if (ok) passes++; else failures++; console.log((ok ? 'ok   ' : 'FAIL ') + label); };
const shot = async (page, name) => { if (shots) await page.screenshot({ path: path.join(shots, name + '.png') }); };
const SHORTCUT = 'Control+Shift+Period';

async function api(token, method, url, body) {
  const response = await fetch(seed.base + url, { method, headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const text = await response.text();
  let data = null; try { data = JSON.parse(text); } catch {}
  return { status: response.status, data };
}

async function session(account, { theme = 'light', locale = 'en-US', viewport = { width: 1360, height: 860 } } = {}) {
  const context = await browser.newContext({ viewport, acceptDownloads: true });
  // Sandboxed mod frames can't use storage at all, so this only runs where it can
  await context.addInitScript(([token, theme, locale]) => { try { localStorage.token = token; localStorage.theme = theme; localStorage.locale = locale; } catch {} }, [account.token, theme, locale]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const open = async (where = '/c/' + seed.chats.user) => {
    await page.goto(seed.base + where, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.documentElement.dataset.mods === '1', null, { timeout: 30000 });
    await page.waitForSelector('#chat-input', { timeout: 30000 });
    await page.waitForFunction(() => ['ready', 'failed'].includes(document.documentElement.dataset.mdStore) || document.documentElement.dataset.modsSafe, null, { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(800);
  };
  return { context, page, errors, open };
}

const dialogOpen = page => page.waitForSelector('.md-dialog[open]', { timeout: 5000 });
async function openMods(page) { await page.keyboard.press(SHORTCUT); await dialogOpen(page); await page.waitForFunction(() => !document.querySelector('.md-dialog .md-spin') || document.querySelector('[data-focus="generate"]'), null, { timeout: 8000 }).catch(() => {}); }
async function tab(page, name) { await page.click('#md-tab-' + name); await page.waitForSelector(`#md-tab-${name}[aria-selected="true"]`); }
async function allow(page) { await page.waitForSelector('.md-ask[open]'); await page.click('.md-ask[open] .md-btn.md-primary'); await page.waitForSelector('.md-ask[open]', { state: 'detached' }); }
async function installStarter(page, id) {
  await tab(page, 'discover');
  await page.click(`.md-card[data-mod="${id}"] .md-btn.md-primary`);
  await allow(page);
  await page.waitForFunction(id => !!document.querySelector(`iframe.md-frame[data-mod="${id}"]`), id, { timeout: 5000 });
}
const chipText = page => page.locator('#owui-mods-bar').innerText().catch(() => '');
async function typeInComposer(page, text) { await page.click('#chat-input'); await page.keyboard.press('Control+A'); await page.keyboard.press('Backspace'); if (text) await page.keyboard.type(text); await page.waitForTimeout(450); }
async function userMods(token) { const { data } = await api(token, 'GET', '/api/v1/functions/id/chat_mods/valves/user'); return data?.mods ? JSON.parse(data.mods) : { mods: [], dismissed: [] }; }

/* ---------- a person installs ready-made mods and uses them ---------- */
{
  const { context, page, errors, open } = await session(seed.user);
  await open();
  await openMods(page);
  const box = await page.locator('.md-dialog').boundingBox();
  check(Math.abs(box.x + box.width / 2 - 680) < 4 && Math.abs(box.y + box.height / 2 - 430) < 4, 'the Mods window is centred');
  check(await page.locator('.md-empty').isVisible(), 'a new person sees the empty state');
  check(await page.locator('#md-tab-create').count() === 1, 'Create is offered when people may make mods');
  // Arrow keys move between tabs
  await page.focus('#md-tab-installed'); await page.keyboard.press('ArrowRight');
  check(await page.evaluate(() => document.activeElement.id === 'md-tab-discover' && document.activeElement.getAttribute('aria-selected') === 'true'), 'arrow keys move between tabs');
  check(await page.locator('text=From your organisation').count() === 0, 'an empty organisation catalog is not shown to people');
  await shot(page, 'discover-light');
  // Cancel installs nothing
  await page.click('.md-card[data-mod="word-counter"] .md-btn.md-primary');
  await page.waitForSelector('.md-ask[open]');
  check(await page.evaluate(() => document.activeElement.textContent === 'Cancel'), 'the permission dialog focuses Cancel');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  check((await userMods(seed.user.token)).mods.length === 0 && await page.locator('.md-dialog[open]').count() === 1, 'Escape on the permission dialog installs nothing and keeps the window open');
  await page.click('.md-card[data-mod="bookmarks"] .md-btn.md-primary');
  await page.waitForSelector('.md-ask[open]');
  check(await page.locator('.md-ask[open] [data-risk="high"]').count() === 1 && /trust/.test(await page.locator('.md-ask[open]').innerText()), 'reading the chat is flagged as high risk, with a trust warning');
  await shot(page, 'ask-light');
  await allow(page);
  for (const id of ['word-counter', 'prompt-shortcuts', 'pomodoro', 'readability', 'export-markdown', 'focus-mode']) await installStarter(page, id);
  check(await page.locator('.md-card[data-mod="word-counter"] .md-btn:disabled').count() === 1, 'an installed mod shows as installed in Discover');
  await tab(page, 'installed');
  check(await page.locator('.md-list .md-card').count() === 7, 'Installed lists the 7 mods');
  await shot(page, 'installed-light');
  const stored = await userMods(seed.user.token);
  check(stored.mods.length === 7 && stored.mods.every(m => m.enabled && m.source === 'starter' && m.granted.length === m.permissions.length), 'the mods are kept on the server, granted and on');
  await page.click('.md-dialog .md-dhead .md-icon');
  await page.waitForSelector('.md-dialog', { state: 'detached' });

  // Word Counter reads the message box
  await typeInComposer(page, 'one two three');
  check(/3 words · 13 chars/.test(await chipText(page)), 'Word Counter counts while you type');
  // / commands: a hint, then Enter fills in the prompt instead of sending
  await typeInComposer(page, '/sum');
  check(/\/summarize/.test(await chipText(page)), 'typing / shows matching commands');
  const before = await page.locator('#messages-container [id^="message-"]').count();
  await typeInComposer(page, '/summarize The moon');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(700);
  const composer = await page.locator('#chat-input').innerText();
  check(/^Summarize this text in 5 key points/.test(composer) && /The moon/.test(composer), 'a / command fills in its prompt');
  check(await page.locator('#messages-container [id^="message-"]').count() === before, 'a / command does not send anything');
  await typeInComposer(page, '');
  check(!/words/.test(await chipText(page)), 'Word Counter clears when the box is empty');

  // Pomodoro band
  await page.click('#owui-mods-bar .md-band .md-btn:has-text("Start")');
  await page.waitForTimeout(2300);
  check(/24:5[78]/.test(await chipText(page)), 'the Pomodoro bar counts down');
  check(await page.locator('#owui-mods-bar .md-bandname').count() === 0, 'a bar whose text starts with the mod icon shows no extra icon');

  // Readability scored the last answer
  check(/📖 \S+/.test(await chipText(page)), 'Readability scores the last answer');

  // Bookmarks: button under answers, label, panel
  check(await page.locator('.md-msgrow .md-msgbtn').count() === 2, 'Bookmarks adds a button under each answer');
  await page.locator('.md-msgrow .md-msgbtn').first().click();
  await page.waitForFunction(() => /🔖 1/.test(document.getElementById('owui-mods-bar')?.innerText || ''), null, { timeout: 5000 }).catch(() => {});
  check(/🔖 1/.test(await chipText(page)), 'bookmarking an answer counts it');
  await page.click('#owui-mods-bar button.md-chip:has-text("🔖")');
  await page.waitForSelector('#owui-mods-pane:not([hidden])');
  const frame = page.frameLocator('iframe.md-frame[data-mod="bookmarks"]');
  check(/library opens at eight/.test(await frame.locator('body').innerText()), 'the Bookmarks panel lists the saved answer');
  await shot(page, 'pane-light');
  await page.click('#owui-mods-pane .md-icon');
  check(await page.locator('#owui-mods-pane').isHidden(), 'the panel closes');

  // Export downloads the chat as Markdown
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('#owui-mods-bar button.md-chip:has-text("Export")')]);
  const markdown = fs.readFileSync(await download.path(), 'utf8');
  check(download.suggestedFilename() === 'Library questions.md' && /^# Library questions/.test(markdown) && /## You\n\nWhen does the library open\?/.test(markdown), 'Export saves the chat as Markdown');

  // Focus mode changes the page's style and can be turned off
  await page.click('#owui-mods-bar button.md-chip:has-text("Focus")');
  await page.waitForTimeout(400);
  check(await page.evaluate(() => getComputedStyle(document.getElementById('sidebar')).display === 'none'), 'Focus Mode hides the sidebar');
  await shot(page, 'chat-light');
  await page.click('#owui-mods-bar button.md-chip:has-text("Focus")');
  await page.waitForTimeout(400);
  check(await page.evaluate(() => getComputedStyle(document.getElementById('sidebar')).display !== 'none'), 'Focus Mode turns off again');

  // A new answer reaches mods that listen for messages
  const scoreBefore = (await chipText(page)).match(/📖[^\n]*/)?.[0];
  await typeInComposer(page, 'Tell me about the library');
  await page.keyboard.press('Enter');
  await page.waitForFunction(before => document.querySelectorAll('#messages-container [id^="message-"]').length >= before + 2, before, { timeout: 15000 });
  await page.waitForFunction(old => { const m = (document.getElementById('owui-mods-bar')?.innerText || '').match(/📖[^\n]*/); return m && m[0] !== old; }, scoreBefore, { timeout: 15000 }).catch(() => {});
  check((await chipText(page)).match(/📖[^\n]*/)?.[0] !== scoreBefore, 'a finished answer is passed to mods (Readability updates)');

  // Turning a mod off stops it, and that is kept
  await openMods(page);
  await page.click('.md-card[data-mod="word-counter"] .md-switch');
  await page.waitForTimeout(500);
  check(await page.locator('iframe.md-frame[data-mod="word-counter"]').count() === 0 && (await userMods(seed.user.token)).mods.find(m => m.id === 'word-counter').enabled === false, 'switching a mod off stops it and is saved');
  // Remove asks first
  await page.click('.md-card[data-mod="word-counter"] .md-icon[aria-haspopup="menu"]');
  await page.click('.md-ask[open] [role="menuitem"]:has-text("Remove")');
  await page.waitForSelector('.md-ask[open] .md-danger');
  await page.click('.md-ask[open] .md-danger');
  await page.waitForTimeout(500);
  check((await userMods(seed.user.token)).mods.every(m => m.id !== 'word-counter'), 'Remove deletes a mod after asking');
  await page.keyboard.press('Escape');

  // After a reload everything comes back on its own
  await open();
  check(await page.locator('iframe.md-frame').count() === 6 && /🔖 1/.test(await chipText(page)), 'mods start again after a reload, with their saved data');
  check(errors.length === 0, 'no page errors (' + errors.slice(0, 2).join(' | ') + ')');
  await context.close();
}

/* ---------- Create: a model writes a mod, you try it, change it, install it ---------- */
{
  const { context, page, open } = await session(seed.user);
  await open();
  await openMods(page);
  await tab(page, 'create');
  await page.click('[data-focus="generate"]');
  check(await page.evaluate(() => document.activeElement.dataset.focus === 'describe'), 'Create with nothing described focuses the description');
  await page.fill('[data-focus="describe"]', 'NOMOD please');
  await page.click('[data-focus="generate"]');
  await page.waitForSelector('.md-dbody .md-error', { timeout: 15000 });
  check(/didn’t return a mod/.test(await page.locator('.md-dbody .md-error').innerText()), 'an answer without a mod says so and how to go on');
  await page.fill('[data-focus="describe"]', 'count the characters I type');
  await page.click('[data-focus="generate"]');
  await page.waitForSelector('.md-card[data-mod="character-counter"]', { timeout: 15000 });
  check(/Character Counter/.test(await page.locator('.md-dbody').innerText()), 'the model’s mod is previewed');
  await shot(page, 'create-light');
  await page.click('[data-focus="try"]');
  await allow(page);
  check(await page.locator('iframe.md-frame[data-mod="preview:character-counter"]').count() === 1, 'Try It runs the mod without installing it');
  check((await userMods(seed.user.token)).mods.every(m => m.id !== 'character-counter'), 'trying saves nothing');
  await page.fill('[data-focus="change"]', 'say Characters instead');
  await page.click('[data-focus="apply-change"]');
  await page.waitForFunction(() => /version|Characters: /.test(document.querySelector('.md-dbody pre')?.textContent || ''), null, { timeout: 15000 });
  check(/Characters: /.test(await page.locator('.md-dbody pre').textContent()) && await page.locator('iframe.md-frame[data-mod^="preview:"]').count() === 0, 'a change rewrites the mod and stops the old try');
  await page.click('[data-focus="keep"]');
  await allow(page);
  await page.waitForSelector('#md-tab-installed[aria-selected="true"]');
  const mine = (await userMods(seed.user.token)).mods.find(m => m.id === 'character-counter');
  check(mine && mine.version === 2 && mine.source === 'user', 'the changed mod is installed as yours');
  await page.keyboard.press('Escape');
  await typeInComposer(page, 'abcde');
  check(/Characters: 5/.test(await chipText(page)), 'the installed mod works');
  // Export, then paste it back: the text round-trips
  await openMods(page);
  await page.click('.md-card[data-mod="character-counter"] .md-icon[aria-haspopup="menu"]');
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('.md-ask[open] [role="menuitem"]:has-text("Export")')]);
  const text = fs.readFileSync(await download.path(), 'utf8');
  check(download.suggestedFilename() === 'character-counter.mod' && /^name: Character Counter\n/.test(text) && /\n---\n/.test(text), 'Export saves the mod as text');
  await context.close();
}

/* ---------- what a mod can't do ---------- */
{
  const { context, page, open } = await session(seed.user);
  await open();
  const paste = async modText => {
    await openMods(page);
    await tab(page, 'create');
    await page.click('.md-details > summary:has-text("Paste")');
    await page.fill('[data-focus="paste"]', modText);
    await page.click('.md-btn:has-text("Read Mod")');
    await page.click('[data-focus="keep"]');
    await allow(page);
    await page.waitForSelector('#md-tab-installed[aria-selected="true"]');
    await page.keyboard.press('Escape');
  };
  // Every way out it might try, reported back through its own storage
  const probe = `name: Probe
permissions: ui.status
---
const out = {};
const attempt = async (name, fn) => { try { out[name] = 'ALLOWED:' + String(await fn()).slice(0, 40); } catch (e) { out[name] = 'blocked'; } };
await attempt('parent', () => window.parent.document.title);
await attempt('top', () => window.top.location.href);
await attempt('storage', () => localStorage.getItem('token'));
await attempt('cookie', () => document.cookie || (() => { throw 1; })());
await attempt('fetch', () => fetch('/api/v1/auths/').then(r => r.status));
await attempt('remote', () => fetch('https://example.com/').then(r => r.status));
await attempt('image', () => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok('loaded'); i.onerror = no; i.src = 'https://example.com/x.png'; }));
await attempt('popup', () => { const w = window.open('https://example.com'); if (!w) throw 1; return 'opened'; });
await attempt('composer', () => mod.composer.get());
await attempt('chat', () => mod.chat.current());
await attempt('send', () => mod.chat.send('hi'));
await attempt('style', () => mod.style('body{display:none}'));
await attempt('ai', () => mod.ai.complete('x'));
await attempt('redefine', () => { window.mod = 1; return window.mod === 1 ? 'replaced' : (() => { throw 1; })(); });
await mod.storage.set('result', out);
mod.status.set('probe done');`;
  await paste(probe);
  await page.waitForFunction(() => /probe done/.test(document.getElementById('owui-mods-bar')?.innerText || ''), null, { timeout: 10000 });
  const result = await page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.endsWith(':probe')) return JSON.parse(localStorage.getItem(k)).result; });
  const leaks = Object.entries(result || {}).filter(([, v]) => v !== 'blocked');
  check(result && leaks.length === 0, 'a mod can’t reach the page, the sign-in, the network or anything it wasn’t granted (' + leaks.map(l => l.join('=')).join(', ') + ')');
  check(await page.evaluate(() => getComputedStyle(document.body).display !== 'none'), 'a mod without ui.style can’t restyle the page');

  // Styles that could carry data away, or hide Mods, are refused
  await paste(`name: Styler
permissions: ui.style, ui.status
---
const out = [];
for (const css of ['body{background:url(https://example.com/a)}', 'body{background:image-set("https://example.com/a" 1x)}', '@import "https://example.com/a.css";', 'body{background:\\\\75rl(https://example.com/a)}', '#owui-mods-bar{display:none}', '[class*=md-ui]{display:none}', 'body{background:u/**/rl(https://example.com/a)}']) {
  try { await mod.style(css); out.push('kept'); } catch (e) { out.push('refused'); }
}
mod.status.set('styles ' + out.join(','));`);
  await page.waitForFunction(() => /styles /.test(document.getElementById('owui-mods-bar')?.innerText || ''), null, { timeout: 10000 });
  const styled = (await chipText(page)).match(/styles ([a-z,]+)/)[1].split(',');
  const sheet = await page.evaluate(() => document.querySelector('style[data-mod="styler"]')?.textContent || '');
  check(styled.slice(1, 6).every(s => s === 'refused') && !/example\.com/.test(sheet), 'styles that load from other sites or touch Mods are refused (' + styled.join(',') + ')');

  // A mod that navigates its frame away is stopped and switched off
  await paste(`name: Runaway
---
setTimeout(() => { location.href = 'data:text/html,bye'; }, 300);`);
  await page.waitForTimeout(1500);
  check(await page.locator('iframe.md-frame[data-mod="runaway"]').count() === 0 && (await userMods(seed.user.token)).mods.find(m => m.id === 'runaway')?.enabled === false, 'a mod that navigates its frame is stopped and switched off');

  // A mod that keeps failing is switched off, and shows why
  await paste(`name: Crasher
---
setInterval(() => { throw new Error('boom'); }, 100);`);
  await page.waitForTimeout(1800);
  const crasher = (await userMods(seed.user.token)).mods.find(m => m.id === 'crasher');
  check(await page.locator('iframe.md-frame[data-mod="crasher"]').count() === 0 && crasher?.enabled === false && crasher?.failed === true, 'a mod that keeps failing is switched off');
  await openMods(page);
  check(/Stopped/.test(await page.locator('.md-card[data-mod="crasher"]').innerText()), 'a stopped mod is marked in Installed');
  await page.keyboard.press('Escape');

  // The server checks what is stored
  const bad = await api(seed.user.token, 'POST', '/api/v1/functions/id/chat_mods/valves/user/update', { mods: JSON.stringify({ mods: [{ id: 'x', name: 'X', code: 'mod.toast(1)', permissions: ['root'], granted: ['root'] }] }) });
  const bad2 = await api(seed.user.token, 'POST', '/api/v1/functions/id/chat_mods/valves/user/update', { mods: JSON.stringify({ mods: [{ id: 'x', name: 'X', code: 'mod.toast(1)', permissions: [], granted: ['chat.send'] }] }) });
  const bad3 = await api(seed.user.token, 'POST', '/api/v1/functions/id/chat_mods/valves/update', { catalog: '[]' });
  check(bad.status === 400 && bad2.status === 400, 'the server refuses unknown permissions and grants a mod didn’t ask for');
  check(bad3.status === 401 || bad3.status === 403, 'people can’t change the organisation catalog');

  // Safe mode: nothing runs, and the window says how to come back
  await open('/c/' + seed.chats.user + '?mods=off');
  check(await page.locator('iframe.md-frame').count() === 0 && await page.locator('#owui-mods-bar').count() === 0, 'safe mode (?mods=off) runs no mods');
  await openMods(page);
  check(/Safe mode/.test(await page.locator('.md-dialog').innerText()), 'safe mode is explained in the Mods window');
  await context.close();
}

/* ---------- dark mode, French and phones ---------- */
{
  const { context, page, open } = await session(seed.user, { theme: 'dark' });
  await open();
  check(await page.evaluate(() => document.documentElement.classList.contains('dark')), 'dark mode is on');
  await openMods(page);
  const colours = await page.evaluate(() => { const d = getComputedStyle(document.querySelector('.md-dialog')); return [d.backgroundColor, d.color]; });
  check(colours[0] !== 'rgb(255, 255, 255)' && colours[1] !== 'rgb(38, 38, 38)', 'the Mods window is dark in dark mode (' + colours.join(' / ') + ')');
  await shot(page, 'installed-dark');
  await tab(page, 'discover');
  await shot(page, 'discover-dark');
  await page.keyboard.press('Escape');
  await page.click('#owui-mods-bar button.md-chip:has-text("🔖")');
  await page.waitForSelector('#owui-mods-pane:not([hidden])');
  const frameColours = await page.frameLocator('iframe.md-frame[data-mod="bookmarks"]').locator('body').evaluate(b => [getComputedStyle(b).color, document.documentElement.dataset.theme]);
  check(frameColours[1] === 'dark' && frameColours[0] !== 'rgb(38, 38, 38)', 'a mod’s panel follows dark mode (' + frameColours.join(' / ') + ')');
  await shot(page, 'pane-dark');
  await page.click('#owui-mods-pane .md-icon');
  await typeInComposer(page, '/');
  await shot(page, 'chat-dark');
  await context.close();
}
{
  const { context, page, open } = await session(seed.user, { locale: 'fr-FR' });
  await open();
  await openMods(page);
  check(await page.locator('#md-tab-installed').innerText() === 'Installés', 'Mods follows the French interface');
  await page.keyboard.press('Escape');
  check(/Concentration/.test(await chipText(page)) || /Travail/.test(await chipText(page)), 'ready-made mods speak French too');
  await context.close();
}
{
  const { context, page, open } = await session(seed.user, { viewport: { width: 390, height: 780 } });
  await open();
  await openMods(page);
  const box = await page.locator('.md-dialog').boundingBox();
  const overflow = await page.evaluate(() => document.querySelector('.md-dbody').scrollWidth - document.querySelector('.md-dbody').clientWidth);
  check(box.width === 390 && overflow <= 0, 'on a phone the window fills the screen without sideways scrolling');
  await shot(page, 'phone-light');
  await context.close();
}

/* ---------- ways in: the chat ⋯ menu ---------- */
{
  const { context, page, open } = await session(seed.user);
  await open();
  await page.click('#chat-context-menu-button');
  await page.waitForSelector('#owui-mods-menu-item', { timeout: 5000 }).catch(() => {});
  check(await page.locator('#owui-mods-menu-item').count() === 1, 'Mods is in the chat ⋯ menu');
  await page.click('#owui-mods-menu-item');
  await dialogOpen(page);
  check(true, 'the ⋯ menu item opens Mods');
  await context.close();
}

/* ---------- administrators publish mods to everyone ---------- */
{
  const { context, page, open } = await session(seed.admin);
  await open('/c/' + seed.chats.admin);
  // Open WebUI shows administrators what's new first
  if (await page.locator('[role="dialog"][aria-modal="true"]').count()) { await page.keyboard.press('Escape'); await page.waitForTimeout(500); }
  const plus = page.locator('.panel-tab-strip').locator('xpath=..').locator(':scope > :not([role=tablist]) > button[aria-haspopup="true"]');
  if (await plus.count() && await plus.first().isVisible()) {
    await plus.first().click();
    await page.waitForSelector('#owui-mods-plus', { timeout: 5000 }).catch(() => {});
    check(await page.locator('#owui-mods-plus').count() === 1, 'Mods is in the tab row’s + menu');
    await page.click('#owui-mods-plus');
    await dialogOpen(page);
    await page.keyboard.press('Escape');
    await page.waitForSelector('.md-dialog', { state: 'detached' });
  }
  await openMods(page);
  await tab(page, 'discover');
  check(await page.locator('text=From your organisation').count() === 1, 'administrators see the organisation catalog, even empty');
  await installStarter(page, 'pomodoro');
  await tab(page, 'installed');
  await page.click('.md-card[data-mod="pomodoro"] .md-icon[aria-haspopup="menu"]');
  await page.click('.md-ask[open] [role="menuitem"]:has-text("Publish")');
  await page.waitForTimeout(800);
  await tab(page, 'discover');
  await page.click('.md-list').catch(() => {});
  await page.locator('.md-card[data-mod="pomodoro"]').first().locator('.md-icon').click();
  await page.click('.md-ask[open] [role="menuitem"]:has-text("Turn On for Everyone")');
  await page.waitForTimeout(800);
  const valves = (await api(seed.admin.token, 'GET', '/api/v1/functions/id/chat_mods/valves')).data;
  const catalog = JSON.parse(valves.catalog);
  check(catalog.length === 1 && catalog[0].id === 'pomodoro' && catalog[0].pinned === true, 'an administrator publishes a mod and turns it on for everyone');
  const badCatalog = await api(seed.admin.token, 'POST', '/api/v1/functions/id/chat_mods/valves/update', { ...valves, catalog: JSON.stringify([{ id: 'Bad Id', name: 'x', code: 'x' }]) });
  check(badCatalog.status === 400, 'the server refuses a malformed catalog');
  await shot(page, 'discover-admin');
  await context.close();
}
{
  // Another person: the mod turned on for everyone arrives once; removing it is respected
  const add = await api(seed.admin.token, 'POST', '/api/v1/auths/add', { name: 'Second User', email: 'second@example.test', password: seed.password, role: 'user' });
  const second = { token: add.data.token };
  const { context, page, open } = await session(second);
  await open('/');
  await page.waitForTimeout(800);
  check(await page.locator('iframe.md-frame[data-mod="pomodoro"]').count() === 1 && /Focus · 25:00/.test(await chipText(page)), 'a mod turned on for everyone runs for a new person');
  await openMods(page);
  await page.click('.md-card[data-mod="pomodoro"] .md-icon[aria-haspopup="menu"]');
  await page.click('.md-ask[open] [role="menuitem"]:has-text("Remove")');
  check(/won’t come back/.test(await page.locator('.md-ask[open]').innerText()), 'removing an organisation mod says it won’t come back');
  await page.click('.md-ask[open] .md-danger');
  await page.waitForTimeout(500);
  await open('/');
  check(await page.locator('iframe.md-frame[data-mod="pomodoro"]').count() === 0, 'a removed organisation mod stays removed');
  await context.close();
}

/* ---------- where people may not write mods, only known code runs ---------- */
{
  const valves = (await api(seed.admin.token, 'GET', '/api/v1/functions/id/chat_mods/valves')).data;
  await api(seed.admin.token, 'POST', '/api/v1/functions/id/chat_mods/valves/update', { ...valves, allow_user_mods: false });
  const add = await api(seed.admin.token, 'POST', '/api/v1/auths/add', { name: 'Third User', email: 'third@example.test', password: seed.password, role: 'user' });
  const third = { token: add.data.token };
  const pomodoro = (await userMods(seed.user.token)).mods.find(m => m.id === 'pomodoro');
  const disguised = { ...pomodoro, id: 'disguised', name: 'Disguised', code: "mod.band.set({text: 'not a starter'});" };
  await api(third.token, 'POST', '/api/v1/functions/id/chat_mods/valves/user/update', { mods: JSON.stringify({ mods: [pomodoro, disguised], dismissed: ['pomodoro'] }) });
  await new Promise(r => setTimeout(r, 1500));
  const { context, page, open } = await session(third);
  await open('/');
  check(await page.locator('iframe.md-frame[data-mod="pomodoro"]').count() === 1 && await page.locator('iframe.md-frame[data-mod="disguised"]').count() === 0, 'with people’s own mods off, only code matching a ready-made or published mod runs');
  await openMods(page);
  check(await page.locator('#md-tab-create').count() === 0, 'Create is hidden when people may not make mods');
  await api(seed.admin.token, 'POST', '/api/v1/functions/id/chat_mods/valves/update', { ...valves, allow_user_mods: true });
  await context.close();
}

/* ---------- the administrator switches mods off ---------- */
{
  const valves = (await api(seed.admin.token, 'GET', '/api/v1/functions/id/chat_mods/valves')).data;
  await api(seed.admin.token, 'POST', '/api/v1/functions/id/chat_mods/valves/update', { ...valves, enabled: false });
  await new Promise(r => setTimeout(r, 1500));
  const { context, page, open } = await session(seed.user);
  await open();
  check(await page.locator('iframe.md-frame').count() === 0, 'switching Mods off stops every mod');
  await openMods(page);
  check(/administrator has switched mods off/.test(await page.locator('.md-dialog').innerText()), 'the Mods window says an administrator switched them off');
  await api(seed.admin.token, 'POST', '/api/v1/functions/id/chat_mods/valves/update', { ...valves, enabled: true });
  await context.close();
}

await browser.close();
console.log(`\n${passes} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
