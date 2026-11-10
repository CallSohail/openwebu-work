// Browser checks for Chat Gallery against a throwaway Open WebUI seeded by seed.py.
//
//   python tests/seed.py http://127.0.0.1:8080 chat_gallery.py > seed.json
//   node tests/e2e.mjs seed.json [screenshot-folder]
//
// It deletes files and changes the function's valves on that instance. Needs Playwright with Chromium.
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
  else { failed.push(name); console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')); }
};
const api = async (account, method, path, body) => {
  const response = await fetch(seed.base + path, {
    method, headers: { Authorization: 'Bearer ' + account.token, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, json: await response.json().catch(() => null) };
};
const setValves = valves => api(seed.admin, 'POST', '/api/v1/functions/id/chat_gallery/valves/update', valves);
const defaults = { max_zip_mb: 300, library: 'all', allow_delete: true, load_linked_images: true, header_button: true, thumbnail_px: 320, language: 'auto' };

const browser = await chromium.launch();
async function session(account, viewport = { width: 1400, height: 900 }, extra = {}) {
  const context = await browser.newContext({ viewport, acceptDownloads: true, ...extra });
  const page = await context.newPage();
  const state = { errors: [], leaks: [], foreign: new Set() };
  page.on('pageerror', error => state.errors.push(String(error)));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin === new URL(seed.base).origin || url.protocol === 'data:' || url.protocol === 'blob:') return;
    state.foreign.add(url.hostname);
    const headers = request.headers();
    if (headers.authorization || headers.cookie) state.leaks.push(url.hostname);
  });
  await context.request.post(seed.base + '/api/v1/auths/signin', { data: { email: account.email, password: seed.password } });
  // An administrator is shown the release notes once per version; mark them read so they don't cover the page
  const { version } = await (await fetch(seed.base + '/api/config')).json();
  await api(account, 'POST', '/api/v1/users/user/settings/update', { ui: { version } });
  await context.addInitScript(token => { localStorage.token = token; }, account.token);
  const open = async chat => {
    await page.goto(seed.base + (chat ? '/c/' + chat : '/'), { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.documentElement.dataset.chatGallery === '1', null, { timeout: 30000 });
    await page.waitForSelector('#chat-input', { timeout: 30000 });
  };
  return { context, page, state, open };
}
const shot = (page, name) => shots ? page.screenshot({ path: `${shots}/${name}.png` }) : null;
const tiles = page => page.locator('.cg-grid .cg-tile');
const foot = page => page.locator('.cg-foot').innerText();
const settle = (page, count) => page.waitForFunction(n => document.querySelectorAll('.cg-grid .cg-tile').length === n, count, { timeout: 20000 }).then(() => true, () => false);
const facts = page => page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.cg-facts dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent])));

/* ---- a plain user on a wide screen: no + menu, the button in the tab row is the way in ---- */
console.log('Plain user, wide screen');
{
  const { context, page, state, open } = await session(seed.user);
  await open(seed.user.chat);
  await page.waitForSelector('#owui-chat-gallery-header', { timeout: 15000 }).catch(() => {});
  check('entry button is on the page', await page.locator('#owui-chat-gallery-header').isVisible());
  await page.click('#owui-chat-gallery-header');
  check('This chat lists 6 images, the document left out and the repeat listed once', await settle(page, 6), await foot(page));
  // Open WebUI 0.13.1 and later show a tab row above the chat on a wide screen; 0.13.0 shows the classic top bar
  const tabRow = await page.evaluate(() => !!document.getElementById('chat-panel-main-chat-view'));
  console.log('  (layout: ' + (tabRow ? 'tab row' : 'classic top bar') + ')');
  if (tabRow) check('the gallery is a tab in the tab row', await page.locator('.panel-tab-strip + #owui-chat-gallery-tabs #owui-chat-gallery-tab[aria-selected="true"]').count() === 1);
  else check('the gallery lies over the chat, below the top bar', await page.evaluate(() => { const box = document.getElementById('owui-chat-gallery'), bar = document.querySelector('#chat-container nav'); return box.classList.contains('cg-solo') && box.getBoundingClientRect().top >= bar.getBoundingClientRect().bottom - 1; }));
  check('the chat under it is hidden and unreachable', await page.evaluate(() => { const view = document.getElementById('chat-panel-main-chat-view') || document.getElementById('chat-pane'); return view.hasAttribute('data-cg-covered') && view.inert; }));

  await page.waitForFunction(() => [...document.querySelectorAll('.cg-tile img')].filter(img => img.complete && img.naturalWidth).length >= 4, null, { timeout: 30000 }).catch(() => {});
  const first = await page.evaluate(() => { const img = document.querySelector('.cg-tile img'); return { src: img.src.slice(0, 5), width: img.naturalWidth }; });
  check('a 4000 px photo is shown as a small picture', first.src === 'blob:' && first.width > 0 && first.width <= 700, JSON.stringify(first));
  await shot(page, 'chat-wide');

  await page.selectOption('.cg-select', 'uploaded');
  check('kind filter: 2 uploaded', await settle(page, 2), await foot(page));
  await page.selectOption('.cg-select', 'generated');
  check('kind filter: 2 generated', await settle(page, 2));
  await page.selectOption('.cg-select', 'other');
  check('kind filter: 2 linked, the look-alike local address among them', await settle(page, 2));
  await page.selectOption('.cg-select', 'all');
  await settle(page, 6);

  const before = await tiles(page).first().getAttribute('aria-label');
  await page.click('.cg-head .cg-icon >> nth=0');
  const after = await tiles(page).first().getAttribute('aria-label');
  check('sort button reverses the order', before !== after && /trap|again|linked/i.test(after), `${before} / ${after}`);
  await page.click('.cg-head .cg-icon >> nth=0');

  await page.fill('.cg-search', 'dunes');
  check('search finds the prompt', await settle(page, 1) && /1 of 6 images/.test(await foot(page)), await foot(page));
  await page.fill('.cg-search', 'no such picture');
  await page.waitForSelector('.cg-empty');
  check('an empty search says so', /No matching images/.test(await page.locator('.cg-empty').innerText()));
  await page.fill('.cg-search', '');
  await settle(page, 6);

  // The viewer, and what it reads from the record and from the file itself
  await tiles(page).nth(2).click();
  await page.waitForFunction(() => /Dimensions[^]*424242/.test(document.querySelector('.cg-info')?.textContent || ''), null, { timeout: 20000 }).catch(() => {});
  let details = await facts(page);
  check('AUTOMATIC1111 settings are read from the PNG', details.Seed === '424242' && details.Model === 'sdxl_base_1.0' && details.Sampler === 'DPM++ 2M', JSON.stringify(details));
  check('prompt and negative prompt are shown', /desert dunes at golden hour/.test(await page.locator('.cg-info').innerText()) && /blurry, text/.test(await page.locator('.cg-negative').innerText()));
  check('dimensions come from the loaded image', details.Dimensions === '768 × 512', details.Dimensions);
  check('Show in chat is offered', await page.locator('.cg-vbar [aria-label="Show in chat"]').isVisible());
  await shot(page, 'viewer-wide');
  await page.keyboard.press('ArrowRight');
  await page.waitForFunction(() => /mountain lake/.test(document.querySelector('.cg-info')?.textContent || ''), null, { timeout: 20000 }).catch(() => {});
  details = await facts(page);
  check('ComfyUI graph is read from the PNG', details.Seed === '77' && details.Model === 'dreamshaper_8' && /a mountain lake at dawn/.test(await page.locator('.cg-info').innerText()), JSON.stringify(details));
  await page.keyboard.press('ArrowLeft'); await page.keyboard.press('ArrowLeft'); await page.keyboard.press('ArrowLeft');
  await page.waitForFunction(() => /holiday-photo[^]*4000/.test(document.querySelector('.cg-info')?.textContent || ''), null, { timeout: 30000 }).catch(() => {});
  details = await facts(page);
  check('an uploaded photo shows its name and source', details['File name'] === 'holiday-photo.jpg' && details.Source === 'Uploaded' && details.Dimensions === '4000 × 3000', JSON.stringify(details));
  check('your own upload can be deleted', await page.locator('.cg-vbar [aria-label="Delete image"]').isVisible());
  await page.keyboard.press('Escape');
  check('Escape closes the viewer and keeps the gallery', !(await page.locator('.cg-viewer.cg-open').count()) && await page.locator('#owui-chat-gallery').isVisible());

  // Zip
  const [download] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.click('.cg-head .cg-btn:has-text("Download all")')]);
  const bytes = fs.readFileSync(await download.path());
  const end = bytes.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  const names = bytes.toString('latin1').match(/\d{3}-[a-z0-9-]+\.(?:png|jpg)|prompts\.txt/g) || [];
  check('Download all saves a zip with the 4 reachable images and prompts.txt', end > 0 && bytes.readUInt16LE(end + 10) === 5 && names.includes('prompts.txt') && /^gallery-gallery-test-user\.zip$/.test(download.suggestedFilename()), `${download.suggestedFilename()} entries=${end > 0 ? bytes.readUInt16LE(end + 10) : -1}`);

  // My images: uploads and generated images from every chat
  await page.click('#cg-tab-library');
  check('My images lists 12 images and not the text file', await settle(page, 12), await foot(page));
  await page.selectOption('.cg-select', 'uploaded');
  check('My images, uploaded only: 2', await settle(page, 2));
  await page.selectOption('.cg-select', 'all');
  await settle(page, 12);
  await shot(page, 'library-wide');

  // Delete: cancel keeps, confirm removes the file from Open WebUI
  await tiles(page).first().click();
  await page.waitForSelector('.cg-vbar [aria-label="Delete image"]:visible');
  await page.click('.cg-vbar [aria-label="Delete image"]');
  await page.click('.cg-confirm .cg-btn:has-text("Cancel")');
  check('Cancel keeps the image', await page.locator('.cg-count').innerText() === '1 / 12');
  await page.click('.cg-vbar [aria-label="Delete image"]');
  await page.click('.cg-confirm .cg-danger');
  await page.waitForFunction(() => document.querySelector('.cg-count')?.textContent === '1 / 11', null, { timeout: 15000 }).catch(() => {});
  const left = []; for (const id of seed.user.extras) if ((await api(seed.user, 'GET', '/api/v1/files/' + id)).status === 200) left.push(id);
  const gone = seed.user.extras.length - left.length;
  check('Delete removes one file from Open WebUI and moves on', await page.locator('.cg-count').innerText() === '1 / 11' && gone === 1, `gone=${gone}`);
  await page.keyboard.press('Escape');

  // Select mode
  await page.click('.cg-head .cg-btn[aria-pressed]');
  await tiles(page).nth(0).click(); await tiles(page).nth(1).click();
  check('Select mode offers Download 2 and Delete 2', await page.locator('.cg-head .cg-btn:has-text("Download 2")').isVisible() && await page.locator('.cg-head .cg-btn:has-text("Delete 2")').isVisible());
  await page.keyboard.press('Escape');
  check('Escape leaves Select mode', await page.locator('.cg-head .cg-btn[aria-pressed]').getAttribute('aria-pressed') === 'false');

  // Refresh picks up an image the chat gained
  await page.click('#cg-tab-chat');
  await settle(page, 6);
  const chat = (await api(seed.user, 'GET', '/api/v1/chats/' + seed.user.chat)).json.chat;
  chat.history.messages.m5 = { id: 'm5', parentId: 'm4', childrenIds: [], role: 'assistant', timestamp: Math.floor(Date.now() / 1000), content: 'One more.', files: [{ type: 'image', url: `/api/v1/files/${left[0]}/content` }] };
  chat.history.messages.m4.childrenIds = ['m5']; chat.history.currentId = 'm5';
  await api(seed.user, 'POST', '/api/v1/chats/' + seed.user.chat, { chat });
  await page.click('.cg-head [aria-label="Refresh"]');
  check('Refresh shows the image the chat gained', await settle(page, 7), await foot(page));

  // Tabs take turns, and closing leaves nothing behind
  await page.click(tabRow ? '#chat-panel-main-chat' : '#owui-chat-gallery-header');
  await page.locator('#owui-chat-gallery').waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
  check('going back to the chat sets the gallery aside', await page.locator('#owui-chat-gallery').isHidden() && !(await page.locator('[data-cg-covered]').count()));
  await page.click(tabRow ? '#owui-chat-gallery-tab' : '#owui-chat-gallery-header');
  await page.locator('#owui-chat-gallery').waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
  check('coming back shows it as it was', await page.locator('#owui-chat-gallery').isVisible() && await tiles(page).count() === 7);
  await page.click(tabRow ? '#owui-chat-gallery-tabs [aria-label="Close Gallery"]' : '.cg-head [aria-label="Close Gallery"]');
  check('closing removes the tab and uncovers the chat', await page.locator('#owui-chat-gallery').isHidden() && !(await page.locator('#owui-chat-gallery-tabs').count()) && !(await page.locator('[data-cg-covered]').count()));

  // An empty chat
  await open(seed.user.empty);
  await page.click('#owui-chat-gallery-header');
  await page.waitForSelector('.cg-empty');
  check('an empty chat says so', /No images in this chat yet/.test(await page.locator('.cg-empty').innerText()));

  check('the sign-in token and cookies never left the site', state.leaks.length === 0, state.leaks.join());
  check('linked images were asked for from their own sites, without credentials', state.foreign.has('linked.example.test') && state.foreign.has('evil.example.test'), [...state.foreign].join());
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- an administrator: Gallery is also in the + menu ---- */
console.log('Administrator, wide screen');
{
  const { context, page, state, open } = await session(seed.admin);
  await open(seed.admin.chat);
  const plus = page.locator('#controls-container button[aria-label="New tab"]').first();
  await plus.waitFor({ state: 'visible', timeout: 8000 }).catch(() => {});
  // On 0.13.0 the Tabs panel starts closed; its button in the top bar opens it
  if (!(await plus.isVisible().catch(() => false))) { await page.click('nav button[aria-label="Tabs"]').catch(() => {}); await plus.waitFor({ state: 'visible', timeout: 8000 }).catch(() => {}); }
  if (await plus.isVisible().catch(() => false)) {
    await plus.click();
    await page.waitForSelector('#owui-chat-gallery-button', { timeout: 10000 }).catch(() => {});
    check('Gallery is the last item of the + menu', await page.evaluate(() => { const item = document.getElementById('owui-chat-gallery-button'); return !!item && item === item.parentElement.lastElementChild; }));
    await shot(page, 'plus-menu');
    await page.click('#owui-chat-gallery-button');
    check('choosing it opens the gallery with the chat\'s images', await settle(page, 6), await foot(page).catch(() => 'closed'));
    check('there it is a tab of the panel', await page.locator('#controls-container #owui-chat-gallery-tab[aria-selected="true"]').count() === 1 && !(await page.locator('#owui-chat-gallery.cg-solo').count()));
    await shot(page, 'admin-tab');
  } else check('the + menu is there for an administrator', false, 'no + button found');
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- a phone: the classic top bar, and the gallery lying over the chat ---- */
console.log('Plain user, phone');
{
  const { context, page, state, open } = await session(seed.user, { width: 390, height: 844 }, { hasTouch: true, isMobile: true });
  await open(seed.user.chat);
  await page.waitForSelector('#owui-chat-gallery-header', { timeout: 15000 }).catch(() => {});
  check('entry button is in the top bar', await page.locator('nav #owui-chat-gallery-header').isVisible());
  await page.tap('#owui-chat-gallery-header');
  check('the gallery opens over the chat with its images', await settle(page, 7), await foot(page).catch(() => 'closed'));
  const layout = await page.evaluate(() => { const box = document.getElementById('owui-chat-gallery').getBoundingClientRect(); const tops = [...document.querySelectorAll('.cg-grid li')].map(li => li.getBoundingClientRect().top); return { width: box.width, solid: getComputedStyle(document.getElementById('owui-chat-gallery')).backgroundColor, columns: tops.filter(top => top === tops[0]).length }; });
  check('it fills the width, has its own background and shows 3 columns', layout.width >= 380 && layout.solid !== 'rgba(0, 0, 0, 0)' && layout.columns === 3, JSON.stringify(layout));
  check('the top bar stays in reach above it', await page.evaluate(() => document.getElementById('owui-chat-gallery').getBoundingClientRect().top >= document.querySelector('#chat-container nav').getBoundingClientRect().bottom - 1));
  await page.waitForFunction(() => [...document.querySelectorAll('.cg-tile img')].filter(img => img.complete && img.naturalWidth).length >= 5, null, { timeout: 30000 }).catch(() => {});
  await shot(page, 'chat-phone');
  await page.locator('.cg-grid .cg-tile').nth(2).tap();
  await page.waitForSelector('.cg-viewer.cg-open');
  const stacked = await page.evaluate(() => { const stage = document.querySelector('.cg-stage').getBoundingClientRect(), info = document.querySelector('.cg-info').getBoundingClientRect(); return info.top >= stage.bottom - 1; });
  check('the viewer puts the details under the image', stacked);
  await shot(page, 'viewer-phone');
  await page.locator('.cg-vbar [aria-label="Back to gallery"]').tap();
  await page.locator('.cg-head [aria-label="Close Gallery"]').tap();
  check('its close button closes it', await page.locator('#owui-chat-gallery').isHidden());
  check('no page errors', state.errors.length === 0, state.errors.slice(0, 3).join(' | '));
  await context.close();
}

/* ---- valves: French, generated only, no delete, no linked images, no entry button ---- */
console.log('Valves');
{
  await setValves({ ...defaults, library: 'generated', allow_delete: false, load_linked_images: false, language: 'fr', thumbnail_px: 0 });
  const { context, page, state, open } = await session(seed.user);
  await open(seed.user.chat);
  await page.waitForSelector('#owui-chat-gallery-header', { timeout: 15000 }).catch(() => {});
  await page.click('#owui-chat-gallery-header');
  check('linked images are left out and counted', await settle(page, 5) && /2 images liées masquées/.test(await foot(page)), await foot(page).catch(() => 'closed'));
  check('the gallery speaks French', await page.locator('#cg-tab-chat').innerText() === 'Cette conversation' && await page.locator('#cg-tab-library').innerText() === 'Images générées');
  check('with thumbnails off the tiles show the files themselves', await page.evaluate(() => [...document.querySelectorAll('.cg-tile img')].every(img => img.getAttribute('src').startsWith('/api/v1/files/'))));
  await tiles(page).first().click();
  await page.waitForFunction(() => /holiday-photo/.test(document.querySelector('.cg-info')?.textContent || ''), null, { timeout: 20000 }).catch(() => {});
  check('no delete button when deleting is turned off', await page.locator('.cg-vbar [aria-label="Supprimer l’image"]').isHidden());
  await page.keyboard.press('Escape');
  await page.click('#cg-tab-library');
  check('the second view lists generated images only', await settle(page, 9), await foot(page));
  // The chat page itself still shows linked images in its messages; the check is that the gallery adds none of its own
  check('the gallery itself shows nothing from other sites', await page.evaluate(() => [...document.querySelectorAll('#owui-chat-gallery img[src]')].every(img => new URL(img.src, location.href).origin === location.origin || img.src.startsWith('blob:'))));
  await shot(page, 'french-generated');
  await context.close();

  await setValves({ ...defaults, header_button: false });
  const second = await session(seed.user);
  await second.open(seed.user.chat);
  await second.page.waitForTimeout(1500);
  check('the entry button can be turned off', !(await second.page.locator('#owui-chat-gallery-header').count()));
  await second.context.close();
  await setValves(defaults);
}

await browser.close();
console.log(`\n${passed} passed, ${failed.length} failed`);
if (failed.length) { for (const name of failed) console.log('  - ' + name); process.exit(1); }
