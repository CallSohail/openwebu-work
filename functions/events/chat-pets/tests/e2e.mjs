// Browser checks for Chat Pets against a throwaway Open WebUI wired to the fake model and seeded by seed.py.
//
//   node tests/e2e.mjs seed.json [screenshot-folder]
//
// It changes valves, user settings and chats on that instance. Needs Playwright with Chromium.
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
  else { failed.push(name); console.log('  FAIL ' + name + (detail !== '' ? ' -> ' + String(typeof detail === 'string' ? detail : JSON.stringify(detail)).slice(0, 400) : '')); }
};
const api = async (account, method, path, body) => {
  const response = await fetch(seed.base + path, { method, headers: { Authorization: 'Bearer ' + account.token, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, json: await response.json().catch(() => null) };
};
const DEFAULTS = { available_pets: 'hibou,ecureuil,berger,renard,chat,tortue', allow_custom_pets: true, allow_photo_pets: true, default_pet: '', invite_new_users: true, show_paw_when_off: true,
  speech_bubbles: true, bubble_interval_minutes: 20, sleep_after_minutes: 3, quick_actions: true, quick_actions_json: '', default_position: 'composer', hide_on_mobile: false, language: 'auto' };
const setValves = async valves => {
  const r = await api(seed.admin, 'POST', '/api/v1/functions/id/chat_pets/valves/update', { ...DEFAULTS, ...valves });
  if (r.status !== 200) throw new Error('valves ' + r.status);
};
const resetPet = account => api(account, 'POST', '/api/v1/users/user/settings/update', { owui_pets: null });
const serverPet = async account => ((await api(account, 'GET', '/api/v1/users/user/settings')).json || {}).owui_pets || null;
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACgAAAAeCAIAAADRv8uKAAAAlUlEQVR4nO2WwQ2AMAwDC2IbNoApmIehmIIR2IcHH6RW9BwJEqHmSe0cDlRtN61H8qjehdrAX9Zg8OzLmD+cN+0n1cBF5H2J44VRP1AljQbmHaESgTmV6+tglQpdUfexLS7xRk3cwA38Glg9c7g3auJkDV11ocQqm+jpqDkbKoVvTDry99OuPldfhzuXjVGswNvpb+ATXSUtjT3SU+AAAAAASUVORK5CYII=', 'base64');

const browser = await chromium.launch();
const contexts = [];
async function session(account, { viewport = { width: 1280, height: 820 }, locale = 'en-US', extra = {}, storage = null } = {}) {
  const context = await browser.newContext({ viewport, ...extra });
  contexts.push(context);
  await context.addInitScript(([token, loc, store]) => {
    if (sessionStorage.getItem('owp-test-init')) return;
    sessionStorage.setItem('owp-test-init', '1');
    localStorage.setItem('token', token); localStorage.setItem('locale', loc);
    if (store) for (const k in store) localStorage.setItem(k, store[k]);
  }, [account.token, locale, storage]);
  const page = await context.newPage();
  const state = { errors: [], dialogs: [] };
  page.on('pageerror', e => state.errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::ERR|WebSocket|socket\.io/i.test(m.text())) state.errors.push(m.text()); });
  page.on('dialog', d => { state.dialogs.push(d.message()); d.dismiss().catch(() => {}); });
  return { context, page, state };
}
async function ready(page, path = '/') {
  await page.goto(seed.base + path, { waitUntil: 'load' });
  if (path === '/' || path.startsWith('/c/')) await page.waitForSelector('#chat-input', { timeout: 30000 });
  await page.waitForFunction(() => window.__owuiChatPetsDebug, null, { timeout: 15000 });
  await page.waitForTimeout(1800);
}
const mode = page => page.evaluate(() => window.__owuiChatPetsDebug.mode);
const debugState = page => page.evaluate(() => window.__owuiChatPetsDebug.state);
const rect = (page, sel) => page.$eval(sel, e => e.getBoundingClientRect().toJSON()).catch(() => null);
const visible = (page, sel) => page.$eval(sel, e => !e.hidden && e.getBoundingClientRect().width > 0).catch(() => false);
const shot = async (page, name) => { if (shots) await page.screenshot({ path: `${shots}/${name}.png` }); };
async function adopt(page, id) {
  await page.evaluate(() => window.__owuiChatPetsDebug.openPicker());
  await page.click(`.owp-card[data-id=${id}]`);
  await page.click('.owp-dfoot .owp-primary');
  await page.waitForTimeout(700);
}
async function waitMode(page, want, ms = 6000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (want.includes(await mode(page))) return true; await page.waitForTimeout(150); }
  return false;
}

try {
  await setValves({});
  await resetPet(seed.user); await resetPet(seed.other);

  console.log('\nFirst visit');
  {
    const { page, state } = await session(seed.user);
    await ready(page);
    check('no pet before choosing', !(await visible(page, '#owp-pet')));
    check('paw invites to choose a pet', await visible(page, '#owp-paw') && await page.$eval('#owp-paw', e => e.classList.contains('owp-invite')));
    await shot(page, '01-paw');
    await page.click('#owp-paw');
    await page.waitForSelector('.owp-modal');
    check('picker shows six pets and "add your own"', await page.$$eval('.owp-card', c => c.map(x => x.dataset.id).join(',')) === 'hibou,ecureuil,berger,renard,chat,tortue,custom');
    check('picker is a modal dialog with a title', await page.$eval('.owp-dialog', d => d.getAttribute('role') === 'dialog' && d.getAttribute('aria-modal') === 'true' && !!d.querySelector('h2').textContent));
    check('choose button waits for a pick', await page.$eval('.owp-dfoot .owp-primary', b => b.disabled));
    for (let i = 0; i < 14; i++) await page.keyboard.press('Tab');
    check('focus stays inside the picker', await page.evaluate(() => !!document.activeElement.closest('.owp-dialog')));
    await page.keyboard.press('Escape');
    check('Escape closes the picker and returns focus', !(await page.$('.owp-modal')) && await page.evaluate(() => document.activeElement && document.activeElement.id === 'owp-paw'));
    await page.click('#owp-paw');
    await shot(page, '02-picker');
    await page.click('.owp-card[data-id=renard]');
    check('choose button names the pet', (await page.textContent('.owp-dfoot .owp-primary')).includes('Roux'));
    await page.click('.owp-dfoot .owp-primary');
    await page.waitForTimeout(900);
    check('pet appears after choosing', await visible(page, '#owp-pet'));
    const pet = await rect(page, '#owp-pet'), box = await rect(page, '#message-input-container');
    check('pet sits on the message box', Math.abs(pet.bottom - box.top) <= 8 && pet.left >= box.left && pet.right <= box.right, { pet, box });
    const mid = box.left + box.width / 2;
    check('pet keeps clear of the middle of the box', Math.abs(pet.left + pet.width / 2 - mid) >= 60);
    check('greeting bubble names the pet', (await page.textContent('#owp-bubble')).includes('Roux'));
    const bub = await rect(page, '#owp-bubble');
    check('bubble sits just above the pet', bub.bottom <= pet.top && pet.top - bub.bottom < 30 && bub.right > pet.left && bub.left < pet.right, { bub, pet });
    await shot(page, '03-perched');
    await page.waitForTimeout(900);
    const saved = await serverPet(seed.user);
    check('choice is saved in the account', saved && saved.chosen === true && saved.pet && saved.pet.id === 'renard', saved);
    check('no script errors or alerts', state.errors.length === 0 && state.dialogs.length === 0, state.errors);
  }

  console.log('\nAnother device');
  {
    const { page } = await session(seed.user);
    await ready(page);
    check('same pet on a fresh browser', await visible(page, '#owp-pet') && (await debugState(page)).pet.id === 'renard');
    check('daily greeting is not repeated', !(await visible(page, '#owp-bubble')));
  }

  console.log('\nMenu and shortcuts');
  {
    const { page, state } = await session(seed.user);
    await ready(page);
    await page.click('#owp-pet .owp-hit');
    check('menu opens', await visible(page, '#owp-menu') && await page.$eval('#owp-pet .owp-hit', b => b.getAttribute('aria-expanded') === 'true'));
    check('menu lists four shortcuts', await page.$$eval('#owp-menu .owp-mi', b => b.length) >= 4 + 5);
    const menuBox = await rect(page, '#owp-menu'), petBox = await rect(page, '#owp-pet');
    check('menu opens next to the pet', Math.abs((menuBox.left + menuBox.width / 2) - (petBox.left + petBox.width / 2)) < menuBox.width && (menuBox.bottom <= petBox.top + 2 || menuBox.top >= petBox.bottom - 2), { menuBox, petBox });
    await page.waitForTimeout(250);
    await shot(page, '04-menu');
    await page.keyboard.press('Escape');
    check('Escape closes the menu', !(await visible(page, '#owp-menu')));
    await page.focus('#owp-pet .owp-hit');
    await page.keyboard.press('Enter');
    check('keyboard opens the menu on its first item', await page.evaluate(() => document.activeElement.classList.contains('owp-mi')));
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Escape');
    check('focus goes back to the pet', await page.evaluate(() => document.activeElement.classList.contains('owp-hit')));
    const before = await page.$$eval('[id^="message-"]', n => n.length);
    const url = page.url();
    await page.click('#owp-pet .owp-hit');
    await page.click('#owp-menu .owp-mi >> nth=0');
    await page.waitForTimeout(500);
    const text = await page.textContent('#chat-input');
    check('shortcut fills the message box', /research/i.test(text), text);
    await page.waitForTimeout(1500);
    check('shortcut sends nothing by itself', page.url() === url && (await page.$$eval('[id^="message-"]', n => n.length)) === before);
    await page.click('#owp-pet .owp-hit');
    await page.click('#owp-menu .owp-mi >> nth=1');
    check('a second shortcut is appended, not replacing', (await page.textContent('#chat-input')).length > text.length + 5);
    check('no errors', state.errors.length === 0, state.errors);
  }

  console.log('\nReacting to the chat');
  {
    const { page, state } = await session(seed.user);
    await ready(page);
    await page.click('#chat-input');
    await page.keyboard.type('Hello SLOW please');
    check('watches while you type', await waitMode(page, ['watch'], 2500));
    await page.keyboard.press('Enter');
    check('thinks while the model answers', await waitMode(page, ['think'], 5000));
    await shot(page, '05-think');
    check('cheers when the answer is done', await waitMode(page, ['happy'], 15000));
    check('settles back to idle', await waitMode(page, ['idle', 'walk'], 8000));
    await page.waitForTimeout(1500);
    const chatUrl = page.url();
    await page.goto(seed.base + '/', { waitUntil: 'load' }); await page.waitForSelector('#chat-input');
    await page.waitForTimeout(1200);
    await page.goto(chatUrl, { waitUntil: 'load' }); await page.waitForSelector('#chat-input');
    await page.waitForTimeout(2500);
    check('opening an old chat does not look like a new answer', ['idle', 'walk', 'greet'].includes(await mode(page)), await mode(page));
    check('no errors', state.errors.length === 0, state.errors);
  }

  console.log('\nDragging and keyboard moves');
  {
    const { page } = await session(seed.user);
    await ready(page);
    const before = await rect(page, '#owp-pet');
    const box = await rect(page, '#message-input-container');
    await page.mouse.move(before.left + before.width / 2, before.top + before.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.left + 60, before.top + before.height / 2, { steps: 12 });
    await page.mouse.up();
    await page.waitForTimeout(400);
    const after = await rect(page, '#owp-pet');
    check('drag moves the pet along the box', after.left < before.left - 100, { before: before.left, after: after.left });
    check('drag does not open the menu', !(await visible(page, '#owp-menu')));
    check('dragged place is remembered', (await debugState(page)).prefs.x < 0.3);
    await page.focus('#owp-pet .owp-hit');
    const x0 = (await debugState(page)).prefs.x;
    await page.keyboard.press('ArrowRight');
    check('arrow keys move the pet', (await debugState(page)).prefs.x > x0);
    check('pet never leaves the box', after.left >= box.left && after.right <= box.right);
  }

  console.log('\nOwn pet, with a hostile name and a photo');
  {
    const { page, state } = await session(seed.user);
    await ready(page);
    await page.evaluate(() => window.__owuiChatPetsDebug.openPicker());
    await page.click('.owp-card[data-id=custom]');
    await page.click('.owp-dfoot .owp-primary');
    check('a name is required', await visible(page, '.owp-err'));
    const evil = '<img src=x onerror=alert(1)>Bob';
    await page.fill('.owp-field input[type=text] >> nth=0', evil + ' with a very long name that goes on');
    check('name is capped at 24 characters', (await page.inputValue('.owp-field input[type=text] >> nth=0')).length === 24);
    await page.fill('.owp-field input[type=text] >> nth=0', evil.slice(0, 24));
    await page.click('.owp-chip >> text=Minuit');
    await page.click('.owp-sw >> nth=8');
    await page.fill('.owp-field input[type=text] >> nth=1', 'Thesis buddy');
    await page.setInputFiles('.owp-field input[type=file]', { name: 'notes.png', mimeType: 'text/plain', buffer: Buffer.from('hello') });
    check('a non-image is refused', (await page.textContent('.owp-field .owp-err:not([hidden])').catch(() => '')).length > 0);
    await page.setInputFiles('.owp-field input[type=file]', { name: 'me.png', mimeType: 'image/png', buffer: PNG });
    await page.waitForTimeout(600);
    check('photo shows in the preview', await page.$eval('.owp-preview .owp-photo-img', i => /^data:image\/(webp|png|jpeg);base64,/.test(i.getAttribute('href') || '')).catch(() => false));
    await shot(page, '06-create');
    await page.click('.owp-dfoot .owp-primary');
    await page.waitForTimeout(900);
    const st = await debugState(page);
    check('own pet is adopted', st.pet && st.pet.kind === 'custom' && st.pet.base === 'chat' && st.pet.role === 'Thesis buddy' && /^#[0-9a-f]{6}$/.test(st.pet.color));
    check('photo is shrunk before it is kept', st.pet.photo && st.pet.photo.length < 60000, st.pet.photo && st.pet.photo.length);
    await page.click('#owp-pet .owp-hit');
    check('hostile name is shown as text', await page.$eval('#owp-menu .owp-mname', e => e.textContent) === evil.slice(0, 24) && !(await page.$('#owp-menu img')));
    check('no script ran from the name', state.dialogs.length === 0 && !(await page.evaluate(() => window.__pwned)), state.dialogs);
    check('own pet uses its animal\'s shortcuts', /tomorrow|calendar/i.test(await page.textContent('#owp-menu')));
    await shot(page, '07-own-pet');
    await page.keyboard.press('Escape');
    const saved = await serverPet(seed.user);
    check('own pet is saved in the account', saved && saved.pet && saved.pet.kind === 'custom' && saved.pet.photo === st.pet.photo);
  }

  console.log('\nTwo tabs');
  {
    const { context, page } = await session(seed.user);
    await ready(page);
    const second = await context.newPage();
    await ready(second);
    await adopt(page, 'tortue');
    await second.waitForTimeout(1200);
    check('the other tab follows the new pet', (await debugState(second)).pet.id === 'tortue');
  }

  console.log('\nHiding and removing');
  {
    const { page } = await session(seed.user);
    await ready(page);
    await page.click('#owp-pet .owp-hit');
    await page.click('#owp-menu .owp-mi >> text=Hide for 1 hour');
    await page.waitForTimeout(1800);
    check('nap hides the pet', !(await visible(page, '#owp-pet')) && await visible(page, '#owp-paw'));
    await page.reload({ waitUntil: 'load' }); await page.waitForSelector('#chat-input'); await page.waitForTimeout(1500);
    check('nap lasts through a reload', !(await visible(page, '#owp-pet')));
    await page.click('#owp-paw'); await page.click('.owp-card[data-id=hibou]'); await page.click('.owp-dfoot .owp-primary');
    await page.waitForTimeout(700);
    check('choosing again ends the nap', await visible(page, '#owp-pet'));
    await page.click('#owp-pet .owp-hit');
    await page.click('#owp-menu .owp-mi >> text=Remove my pet');
    await page.waitForTimeout(800);
    check('remove takes the pet away and leaves the paw', !(await visible(page, '#owp-pet')) && await visible(page, '#owp-paw'));
    check('removal is remembered', (await serverPet(seed.user)).pet === null);
    await page.keyboard.press('Alt+Shift+P');
    check('Alt+Shift+P opens the picker', !!(await page.$('.owp-modal')));
    await page.click('.owp-card[data-id=chat]'); await page.click('.owp-dfoot .owp-primary');
    await page.waitForTimeout(600);
  }

  console.log('\nOpen WebUI dialogs stay on top');
  {
    const { page } = await session(seed.user);
    await ready(page);
    await page.click('#owp-pet .owp-hit');
    await page.waitForTimeout(200);
    await page.mouse.click(5, 400);
    check('clicking elsewhere closes the menu', !(await visible(page, '#owp-menu')));
    await page.keyboard.press('Control+Shift+/');
    await page.waitForTimeout(700);
    const covered = await page.evaluate(() => {
      const r = document.getElementById('owp-pet').getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { covered: !document.getElementById('owp-pet').contains(top), modal: !!document.querySelector('.modal, [role=dialog]') };
    });
    check('an Open WebUI dialog covers the pet', !covered.modal || covered.covered, covered);
  }

  console.log('\nPages without a message box, and signed out');
  {
    const { page, state } = await session(seed.user);
    await ready(page, '/notes');
    const onNotes = await rect(page, '#owp-pet');
    check('pet stays on screen on the notes page', !onNotes || (onNotes.left >= 0 && onNotes.right <= 1280 && onNotes.top >= 0 && onNotes.bottom <= 820), onNotes);
    await ready(page, '/channels');
    check('pet moves to the corner where there is no message box', await visible(page, '#owp-pet') && await page.$eval('#owp-pet', e => e.classList.contains('owp-corner')));
    await page.click('#owp-pet .owp-hit');
    await page.click('#owp-menu .owp-mi >> nth=0');
    await page.waitForSelector('#chat-input', { timeout: 20000 });
    await page.waitForTimeout(1500);
    check('a shortcut from another page opens a chat and fills it', /tomorrow/i.test(await page.textContent('#chat-input')), await page.textContent('#chat-input'));
    await page.evaluate(() => localStorage.removeItem('token'));
    await page.goto(seed.base + '/auth', { waitUntil: 'load' });
    await page.waitForTimeout(1500);
    check('nothing shows when signed out', !(await visible(page, '#owp-pet')) && !(await visible(page, '#owp-paw')));
    check('no errors', state.errors.length === 0, state.errors);
  }

  console.log('\nPhone, dark mode, reduced motion, French');
  {
    const { page, state } = await session(seed.user, { viewport: { width: 390, height: 780 }, extra: { isMobile: true, hasTouch: true }, locale: 'fr-FR' });
    await ready(page);
    const pet = await rect(page, '#owp-pet');
    check('smaller pet on a phone', Math.round(pet.width) === 46, pet.width);
    check('no sideways scrolling', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const buttons = await page.$$eval('#message-input-container button', bs => bs.map(b => b.getBoundingClientRect().toJSON()));
    check('pet does not cover the message box buttons', buttons.every(b => b.bottom <= pet.top || b.top >= pet.bottom || b.right <= pet.left || b.left >= pet.right));
    await page.tap('#owp-pet .owp-hit');
    check('menu fits on the screen', await page.$eval('#owp-menu', m => { const r = m.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0; }));
    check('words are in French', /Changer de compagnon/.test(await page.textContent('#owp-menu')));
    await page.evaluate(() => document.documentElement.classList.add('dark'));
    await page.waitForTimeout(200);
    check('menu follows dark mode', await page.$eval('#owp-menu', m => getComputedStyle(m).backgroundColor) === 'rgb(30, 33, 39)');
    await shot(page, '08-phone-dark');
    check('no errors', state.errors.length === 0, state.errors);
  }
  {
    const { page } = await session(seed.user, { extra: { reducedMotion: 'reduce' } });
    await ready(page);
    check('reduced motion calms the pet', await page.$eval('#owp-pet', e => e.classList.contains('owp-calm')));
  }

  console.log('\nBroken or old saved data');
  {
    await resetPet(seed.other);
    const junk = { ['owui-pets:' + seed.other.id]: JSON.stringify({ chosen: true, updatedAt: Date.now() + 10, pet: { kind: 'custom', base: 'dragon', name: '\u0000    ', color: 'red;background:url(x)', photo: 'javascript:alert(1)' }, prefs: { x: 9, place: 'roof' } }) };
    const { page, state } = await session(seed.other, { storage: junk });
    await ready(page);
    const st = await debugState(page);
    check('invalid saved pet is dropped', st.pet === null && st.prefs.x === 0.86 && st.prefs.place === 'composer', st);
    check('no errors from bad data', state.errors.length === 0, state.errors);
    const garbage = { ['owui-pets:' + seed.other.id]: '{not json' };
    const s2 = await session(seed.other, { storage: garbage });
    await ready(s2.page);
    check('unreadable saved data is ignored', s2.state.errors.length === 0);
  }

  console.log('\nSettings saved by Open WebUI keep the pet');
  {
    await resetPet(seed.other);
    const { page } = await session(seed.other);
    await ready(page);
    await adopt(page, 'berger');
    await page.waitForTimeout(900);
    await api(seed.other, 'POST', '/api/v1/users/user/settings/update', { ui: { notificationSound: false, chatDirection: 'auto' } });
    const saved = await serverPet(seed.other);
    check('saving interface settings leaves the pet alone', saved && saved.pet && saved.pet.id === 'berger');
  }

  console.log('\nServer unreachable');
  {
    const { context, page, state } = await session(seed.other);
    await ready(page);
    const blockPets = r => (r.request().url().includes('raw=true') || (r.request().postData() || '').includes('owui_pets')) ? r.abort() : r.continue();
    await context.route('**/api/v1/users/user/settings**', blockPets);
    await page.reload({ waitUntil: 'load' }); await page.waitForSelector('#chat-input'); await page.waitForTimeout(1800);
    check('pet still comes from this browser', await visible(page, '#owp-pet') && (await debugState(page)).pet.id === 'berger');
    await adopt(page, 'chat');
    await page.waitForTimeout(1000);
    check('a failed save does not break anything', await visible(page, '#owp-pet') && state.errors.length === 0, state.errors);
    await context.unroute('**/api/v1/users/user/settings**', blockPets);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await page.waitForTimeout(1500);
    check('the save is retried when back online', ((await serverPet(seed.other)) || {}).pet?.id === 'chat');
  }

  console.log('\nAdmin valves');
  {
    await setValves({ available_pets: 'chat,hibou', allow_custom_pets: false, default_pet: 'hibou', speech_bubbles: false, quick_actions_json: JSON.stringify({ hibou: [{ fr: 'Mes emails du jour', en: 'Today\'s emails' }] }) });
    await resetPet(seed.other);
    const { page } = await session(seed.other);
    await ready(page);
    check('default pet appears without choosing', await visible(page, '#owp-pet') && (await page.$eval('#owp-pet .owp-hit', b => b.getAttribute('aria-label'))).includes('Plume'));
    check('no bubbles when the admin turned them off', !(await visible(page, '#owp-bubble')));
    await page.click('#owp-pet .owp-hit');
    check('admin shortcuts replace the built-in ones', (await page.$$eval('#owp-menu .owp-mi', b => b.map(x => x.textContent))).some(t => t.includes('Today\'s emails')));
    check('bubble toggle is hidden when bubbles are off', !(await page.$$eval('#owp-menu .owp-mi', b => b.some(x => /bubbles/i.test(x.textContent)))));
    await page.keyboard.press('Escape');
    await page.evaluate(() => window.__owuiChatPetsDebug.openPicker());
    check('picker shows only allowed pets and no own pet', await page.$$eval('.owp-card', c => c.map(x => x.dataset.id).join(',')) === 'hibou,chat');
    await page.keyboard.press('Escape');
    await setValves({ hide_on_mobile: true });
    const phone = await session(seed.other, { viewport: { width: 390, height: 780 } });
    await ready(phone.page);
    check('hide on phones', !(await visible(phone.page, '#owp-pet')) && !(await visible(phone.page, '#owp-paw')));
    await setValves({ sleep_after_minutes: 1 });
  }

  console.log('\nFalling asleep');
  {
    const { page } = await session(seed.user);
    await page.clock.install();
    await ready(page);
    await page.clock.runFor(65000);
    check('falls asleep after the idle time', await mode(page) === 'sleep', await mode(page));
    await shot(page, '09-sleep');
    await page.mouse.move(300, 300);
    await page.clock.runFor(1500);
    check('wakes up on activity', ['greet', 'idle', 'walk'].includes(await mode(page)), await mode(page));
  }
} catch (error) {
  failed.push('crashed: ' + error.message);
  console.error(error);
} finally {
  await setValves({}).catch(() => {});
  for (const c of contexts) await c.close().catch(() => {});
  await browser.close();
}

console.log(`\n${passed} passed, ${failed.length} failed`);
if (failed.length) { console.log('Failed: ' + failed.join('; ')); process.exit(1); }
