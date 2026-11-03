// Browser tests: drives every demo inside a parent page that reproduces Open WebUI 0.12.5's embed iframe
// (sandbox="allow-scripts allow-forms allow-popups allow-downloads", opaque origin, iframe:height and prompt messages).
//
// Usage:
//   python tests/build_demos.py tests/out
//   python -m http.server 8765 --directory tests/out &
//   node tests/browser_test.js [http://localhost:8765] [screenshot_dir]
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://localhost:8765';
const SHOTS = process.argv[3] || '';
const results = [];
function check(name, ok, info) { results.push({ name, ok: !!ok, info }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${info ? ' :: ' + info : ''}`); }

async function open(browser, name, width = 760) {
  const page = await browser.newPage({ viewport: { width: width + 48, height: 1000 } });
  const errors = [];
  const dialogs = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('dialog', async (d) => { dialogs.push(d.message()); await d.dismiss(); });
  await page.goto(`${BASE}/harness_${name}_${width}.html`);
  const frame = page.frameLocator('#f');
  await frame.locator('#gui-root > *').first().waitFor({ timeout: 10000 });
  await page.waitForTimeout(250);
  return { page, frame, errors, dialogs };
}
const text = (loc) => loc.innerText();

(async () => {
  const browser = await chromium.launch();

  // ---- dashboard: reactive state, computed values, segmented switching, reset, prompt action ----
  {
    const { page, frame, errors } = await open(browser, 'dashboard');
    const util = frame.locator('.g-metric').first().locator('.g-metric-v');
    const before = await text(util);
    check('dashboard renders computed utilization', /^\d+%$/.test(before.trim()), before);
    await frame.locator('input[type=range]').first().evaluate((el) => { el.value = 400; el.dispatchEvent(new Event('input', { bubbles: true })); });
    await page.waitForTimeout(80);
    const after = await text(util);
    check('slider recalculates metric without the LLM', after.trim() === '100%' && after !== before, `${before} -> ${after}`);
    check('visible_if shows over-capacity callout', await frame.locator('.g-callout.tone-warning').isVisible());
    check('visible_if hides healthy callout', !(await frame.locator('.g-callout.tone-success').isVisible()));
    const charts = frame.locator('.g-chart');
    const visibleBefore = await charts.evaluateAll((els) => els.filter((e) => !e.closest('[hidden]') && !e.hidden).length);
    check('exactly one performance chart visible', visibleBefore === 1, String(visibleBefore));
    await frame.getByRole('radio', { name: 'Latency' }).click();
    await page.waitForTimeout(80);
    const capLat = await charts.evaluateAll((els) => els.filter((e) => !e.hidden).map((e) => e.querySelector('svg')?.getAttribute('aria-label') || ''));
    check('segmented control switches chart to latency', capLat.length === 1 && /Latency/.test(capLat[0]), capLat.join('|'));
    await frame.getByRole('radio', { name: '12 days' }).click();
    await page.waitForTimeout(80);
    const xl = await frame.locator('.g-chart:not([hidden]) svg text').allTextContents();
    check('range control switches to 12-day labels', xl.some((t) => t.startsWith('Day')), xl.slice(-3).join(','));
    // tooltip
    const plot = frame.locator('.g-chart:not([hidden]) .g-plot');
    const box = await plot.boundingBox();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4);
    await page.waitForTimeout(60);
    const tip = frame.locator('.g-chart:not([hidden]) .g-tip');
    check('chart tooltip shows on hover', await tip.isVisible() && /ms/.test(await text(tip)), (await tip.isVisible()) ? await text(tip) : 'hidden');
    // keyboard on chart
    await plot.focus(); await plot.press('ArrowRight'); await plot.press('ArrowRight');
    check('chart tooltip works with keyboard', await tip.isVisible());
    // table view toggle
    await frame.locator('.g-chart:not([hidden]) button[aria-label="Show data table"]').click();
    check('chart table view toggles', await frame.locator('.g-chart:not([hidden]) table.g-tbl').isVisible());
    await frame.getByRole('button', { name: 'Reset' }).click();
    await page.waitForTimeout(80);
    check('reset restores initial state', (await text(util)) === before, await text(util));
    await frame.getByRole('button', { name: 'Ask how to scale' }).click();
    await frame.locator('.g-fu button').first().click();
    await page.waitForTimeout(150);
    const prompts = await page.evaluate(() => window.__prompts);
    check('prompt action posts input:prompt:submit to Open WebUI', prompts.length === 2 && prompts[0].type === 'input:prompt:submit' && /80 requests/.test(prompts[0].text), JSON.stringify(prompts));
    const heights = await page.evaluate(() => window.__heights);
    check('iframe:height messages size the embed', heights.length > 0 && heights[heights.length - 1] > 400, heights.slice(-3).join(','));
    const ih = await page.locator('#f').evaluate((f) => f.getBoundingClientRect().height);
    const dh = await page.frames()[1].evaluate(() => document.documentElement.getBoundingClientRect().height);
    check('embed is not clipped (iframe height >= content height)', ih + 1 >= dh, `${ih} vs ${dh}`);
    check('dashboard: no page errors', errors.length === 0, errors.join(' | '));
    await page.close();
  }

  // ---- table ----
  {
    const { page, frame } = await open(browser, 'table');
    const rows = () => frame.locator('tbody tr').count();
    check('table paginates (6 per page)', (await rows()) === 6);
    await frame.locator('input[type=search]').fill('qwen');
    await page.waitForTimeout(50);
    check('table search filters rows', (await rows()) === 2, String(await rows()));
    await frame.locator('input[type=search]').fill('');
    await frame.locator('th button', { hasText: 'Params' }).click();
    const first = await text(frame.locator('tbody tr').first().locator('td').first());
    check('numeric sort (descending first click)', first.includes('gpt-oss-120b'), first);
    check('aria-sort set', (await frame.locator('th[aria-sort="descending"]').count()) === 1);
    await frame.locator('select[aria-label="Filter Architecture"]').selectOption('MoE');
    check('column filter', (await rows()) === 3, String(await rows()));
    await frame.locator('select[aria-label="Filter Architecture"]').selectOption('');
    await frame.getByRole('button', { name: 'Next page' }).click();
    check('next page', (await rows()) === 2, String(await rows()));
    const dl = page.waitForEvent('download', { timeout: 3000 }).catch(() => null);
    await frame.getByRole('button', { name: 'CSV' }).click();
    const d = await dl;
    check('CSV download works in sandbox', !!d && d.suggestedFilename().endsWith('.csv'), d ? d.suggestedFilename() : 'no download');
    await page.close();
  }

  // ---- checklist ----
  {
    const { page, frame } = await open(browser, 'checklist');
    const total = frame.locator('.g-check-f b');
    const t0 = await text(total);
    check('checklist shows all-items range total', t0.replace(/\s/g, '') === '€265–515', t0);
    await frame.getByRole('checkbox').nth(0).click();
    await frame.getByRole('checkbox').nth(3).click();
    check('checklist count updates', (await text(frame.locator('.g-check-c'))) === '2 of 5 selected');
    check('checklist total follows selection', (await text(total)).replace(/\s/g, '') === '€170–300', await text(total));
    await frame.getByRole('button', { name: /Copy selected shopping list/ }).click();
    await page.waitForTimeout(50);
    check('copy gives feedback (clipboard or fallback)', await frame.locator('.g-toast').count() === 1);
    await page.close();
  }

  // ---- explorer ----
  {
    const { page, frame } = await open(browser, 'explorer');
    check('explorer shows first detail', (await text(frame.locator('.g-detail-t'))) === 'Qwen3.5-35B-A3B');
    await frame.getByRole('option', { name: /GPT-OSS/ }).click();
    check('selecting an item swaps the detail panel', (await text(frame.locator('.g-detail-t'))) === 'gpt-oss-20b');
    await frame.getByRole('option', { name: /GPT-OSS/ }).press('ArrowDown');
    check('arrow keys move selection', (await text(frame.locator('.g-detail-t'))) === 'Llama-3.3-70B-Instruct');
    await frame.getByRole('button', { name: /Explore this model/ }).click();
    await page.waitForTimeout(150);
    const p = await page.evaluate(() => window.__prompts);
    check('detail button sends prompt', p.length === 1 && /Llama/.test(p[0].text), JSON.stringify(p));
    await page.close();
  }

  // ---- itinerary tabs + map ----
  {
    const { page, frame } = await open(browser, 'itinerary');
    check('day 1 visible', await frame.getByText('Louvre Museum').first().isVisible());
    await frame.getByRole('tab', { name: 'Day 2' }).click();
    check('tab switches to day 2', await frame.getByText('Eiffel Tower').first().isVisible() && !(await frame.getByText('Louvre Museum').first().isVisible()));
    const src = await frame.locator('.g-tl ~ * iframe, iframe.g-map-f').first().getAttribute('src').catch(() => '');
    check('map embed points to OpenStreetMap bbox', /openstreetmap\.org\/export\/embed\.html\?bbox=/.test(src || ''), src);
    const dir = await frame.locator('a.g-btn', { hasText: 'Directions' }).last().getAttribute('href');
    check('directions link built from stops', /google\.com\/maps\/dir\/\?api=1&origin=48\.8584,2\.2945/.test(dir), dir);
    await page.close();
  }

  // ---- workflow steps ----
  {
    const { page, frame } = await open(browser, 'workflow');
    await frame.getByRole('button', { name: /Mark done/ }).click();
    check('interactive steps advance', /Step 2 of 4 · 1 done/.test(await text(frame.locator('.g-steps').locator('xpath=..'))));
    await page.close();
  }

  // ---- calculator / mortgage ----
  {
    const { page, frame } = await open(browser, 'mortgage');
    const pay = frame.locator('.g-metric').first().locator('.g-metric-v');
    const p0 = await text(pay);
    check('mortgage payment computed with pmt()', /€1,8\d\d/.test(p0.replace(/ /g, '')) || /1,8\d\d/.test(p0), p0);
    await frame.getByRole('radio', { name: '30 y' }).click();
    await page.waitForTimeout(60);
    const p1 = await text(pay);
    check('changing term recalculates', p1 !== p0, `${p0} -> ${p1}`);
    const num = frame.locator('input[type=number]');
    await num.fill('10');
    check('number input validates range', /between/.test(await text(frame.locator('.g-field-e'))));
    check('invalid input keeps last valid value', (await text(pay)) === p1);
    await page.close();
  }

  // ---- diagram ----
  {
    const { page, frame } = await open(browser, 'diagram');
    await frame.locator('g.nd', { hasText: 'Retriever' }).click();
    check('diagram node click shows details', /reranks/.test(await text(frame.locator('.g-diag ~ .g-detail'))));
    await frame.getByRole('button', { name: 'Why rerank?' }).click();
    check('accordion expands', await frame.getByText(/cross-encoder/).isVisible());
    await page.close();
  }

  // ---- errors & sandboxed app ----
  {
    const { page, frame, errors, dialogs } = await open(browser, 'errors');
    check('broken chart shows contained error state', await frame.locator('.g-err').count() >= 1);
    check('rest of card still renders after errors', await frame.getByText('Sandboxed mini app').isVisible());
    const app = frame.frameLocator('iframe.g-app');
    await app.locator('#b').click();
    check('sandboxed app is interactive', (await app.locator('#b').innerText()) === 'Clicked 1 times');
    await page.waitForTimeout(300);
    check('sandboxed app has no network access', /blocked/.test(await app.locator('#n').innerText()));
    check('no alert dialogs executed', dialogs.length === 0, dialogs.join('|'));
    check('errors page: no uncaught exceptions', errors.length === 0, errors.join(' | '));
    await page.close();
  }

  // ---- hostile input ----
  {
    const { page, frame, dialogs } = await open(browser, 'hostile');
    await page.waitForTimeout(300);
    const pwnedTop = await page.evaluate(() => window.__pwned);
    const pwnedFrame = await page.frames()[1].evaluate(() => window.__pwned);
    check('XSS payloads do not execute (top)', pwnedTop === undefined);
    check('XSS payloads do not execute (embed)', pwnedFrame === undefined);
    check('no dialogs from hostile spec', dialogs.length === 0);
    const jsLinks = await page.frames()[1].evaluate(() => [...document.querySelectorAll('a[href]')].filter((a) => /^javascript:/i.test(a.getAttribute('href'))).length);
    check('no javascript: links rendered', jsLinks === 0, String(jsLinks));
    check('payload shown as inert text', (await text(frame.locator('.g-top-t'))).includes('<script>'));
    await page.close();
  }

  // ---- performance ----
  {
    const t0 = Date.now();
    const { page, frame } = await open(browser, 'perf');
    const ready = Date.now() - t0;
    const renderMs = await page.frames()[1].evaluate(() => performance.getEntriesByType('navigation')[0]?.domComplete || 0);
    check('2000-row table + 8 charts render under 3 s', ready < 3000, `${ready} ms to first paint of root, domComplete ${Math.round(renderMs)} ms`);
    const t1 = Date.now();
    await frame.locator('input[type=search]').fill('row 19');
    await page.waitForTimeout(10);
    const n = await frame.locator('tbody tr').count();
    check('search over 2000 rows is fast', Date.now() - t1 < 800 && n > 0, `${Date.now() - t1} ms, ${n} rows on page`);
    await page.close();
  }

  // ---- mobile layout: no horizontal overflow ----
  for (const name of ['dashboard', 'calculator', 'comparison', 'table', 'itinerary', 'diagram', 'checklist', 'explorer', 'chart', 'status', 'science', 'mortgage']) {
    const { page, errors } = await open(browser, name, 390);
    const overflow = await page.frames()[1].evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(`mobile 390px ${name}: no horizontal overflow`, overflow <= 1 && errors.length === 0, `overflow ${overflow}px ${errors.join('|')}`);
    if (SHOTS) await page.locator('#f').screenshot({ path: `${SHOTS}/${name}_mobile.png` });
    await page.close();
  }

  if (SHOTS) {
    for (const name of ['dashboard', 'calculator', 'comparison', 'table', 'itinerary', 'diagram', 'workflow', 'checklist', 'explorer', 'chart', 'status', 'mortgage', 'science', 'errors', 'weather', 'weathersample']) {
      const { page } = await open(browser, name, 760);
      await page.waitForTimeout(300);
      await page.locator('#f').screenshot({ path: `${SHOTS}/${name}.png` });
      await page.close();
    }
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} browser checks passed`);
  process.exit(failed.length ? 1 : 0);
})();
