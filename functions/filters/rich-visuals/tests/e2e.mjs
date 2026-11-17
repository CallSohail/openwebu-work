// Browser test for Rich Visuals against a real Open WebUI 0.13.4 (see seed.py and fake_model.py).
//
//   node tests/e2e.mjs seed.json http://127.0.0.1:9994 [screenshot-dir]
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const seed = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const fake = process.argv[3];
const shots = process.argv[4] || '';
if (shots) fs.mkdirSync(shots, { recursive: true });
let passes = 0, failures = 0;
const check = (ok, label) => { ok ? passes++ : failures++; console.log((ok ? 'ok   ' : 'FAIL ') + label); };
const log = async () => (await fetch(fake + '/log')).json();
const api = async (url) => (await fetch(seed.base + url, { headers: { Authorization: 'Bearer ' + seed.user } })).json();

const browser = await chromium.launch();
async function chat(model, text, theme = 'light') {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: theme });
  await context.addInitScript(([token, theme]) => { try { localStorage.token = token; localStorage.theme = theme; } catch {} }, [seed.user, theme]);
  const page = await context.newPage();
  await page.goto(seed.base + '/?models=' + model, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#chat-input', { timeout: 60000 });
  await page.waitForTimeout(1500);
  await page.click('#chat-input');
  await page.keyboard.type(text);
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/c\//, { timeout: 30000 });
  // Wait for the answer to finish
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"], #stop-response-button'), null, { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(2500);
  const chatId = page.url().split('/c/')[1];
  return { context, page, chatId };
}
const lastAssistant = (chat) => {
  const messages = chat?.chat?.history?.messages || {};
  let at = chat?.chat?.history?.currentId; return messages[at];
};

/* ---- a weather question, with Visualization on ---- */
{
  const before = (await log()).length;
  const { context, page, chatId } = await chat('fake-visual', 'What will the temperature be in Paris this week?');
  const requests = (await log()).slice(before);
  check(requests[0]?.rich_visuals && requests[0]?.icon_rules && requests[0]?.visual_rules && requests[0]?.no_emoji_rule, 'the model is asked for a visual with icons, without emojis');
  check(requests[0]?.tools.includes('render_visualization'), 'Open WebUI offers render_visualization alongside');
  const section = page.locator('[data-visualization-call]').first();
  await section.waitFor({ timeout: 30000 }).catch(() => {});
  check(await section.count() === 1, 'the visualization is shown in the answer');
  const frame = page.frameLocator('[data-visualization-call] iframe').first();
  await frame.locator('svg.owui-icon').first().waitFor({ timeout: 20000 }).catch(() => {});
  const icons = await frame.locator('svg.owui-icon').evaluateAll(nodes => nodes.map(n => n.getAttribute('class')));
  check(icons.some(c => /thermometer/.test(c)) && icons.some(c => /cloud-rain/.test(c)) && icons.some(c => /wind/.test(c)), 'icon tokens became real icons (' + icons.length + ')');
  check(icons.some(c => /owui-icon-sun/.test(c)), 'the emoji in the visual became a real icon');
  const frameText = await frame.locator('body').innerText().catch(() => '');
  check(!/\[\[icon/.test(frameText) && !/[☀-➿\u{1F300}-\u{1FAFF}]/u.test(frameText), 'no tokens and no emojis left inside the visual');
  const labelled = await frame.locator('svg.owui-icon[role="img"][aria-label="Chance of rain"]').count();
  check(labelled === 1, 'a labelled icon keeps its accessible label');
  const size = await frame.locator('svg.owui-icon').first().boundingBox();
  check(size && size.width > 12 && size.width < 40, 'icons are drawn at a sensible size (' + Math.round(size?.width || 0) + ' px)');
  if (shots) await page.screenshot({ path: path.join(shots, 'weather-light.png') });
  const chatJson = await api('/api/v1/chats/' + chatId);
  const message = lastAssistant(chatJson);
  const content = String(message?.content || '');
  const shown = await page.locator('#messages-container').innerText();
  check(!/[✅🌤🌧]/u.test(shown), 'emojis are left out of the text');
  check(!content.includes('[[icon'), 'no icon tokens are saved in the message');
  const status = JSON.stringify(message?.statusHistory || []);
  check(!/rich.?visuals/i.test(status), 'Rich Visuals adds no status line');
  await context.close();
}

/* ---- dark mode ---- */
{
  const { context, page } = await chat('fake-visual', 'Weather and temperature in Paris this week?', 'dark');
  const frame = page.frameLocator('[data-visualization-call] iframe').first();
  await frame.locator('svg.owui-icon').first().waitFor({ timeout: 20000 }).catch(() => {});
  const colour = await frame.locator('svg.owui-icon').first().evaluate(n => getComputedStyle(n).color).catch(() => '');
  check(colour && colour !== 'rgb(0, 0, 0)', 'icons take the theme colour in dark mode (' + colour + ')');
  if (shots) await page.screenshot({ path: path.join(shots, 'weather-dark.png') });
  await context.close();
}

/* ---- greetings are left alone ---- */
{
  const before = (await log()).length;
  const { context, page } = await chat('fake-visual', 'Bonjour !');
  const requests = (await log()).slice(before);
  check(requests.length >= 1 && requests.every(r => !r.rich_visuals), 'a greeting reaches the model untouched');
  check(/👋/u.test(await page.locator('#messages-container').innerText()), 'and its answer is not changed');
  await context.close();
}

/* ---- a model without Visualization ---- */
{
  const before = (await log()).length;
  const { context } = await chat('fake-visual-off', 'What will the temperature be in Paris this week?');
  const requests = (await log()).slice(before);
  check(requests[0]?.rich_visuals && !requests[0]?.icon_rules && !requests[0]?.tools.includes('render_visualization'), 'without Visualization, only tidy-text instructions are added');
  await context.close();
}

await browser.close();
console.log(`\n${passes} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
