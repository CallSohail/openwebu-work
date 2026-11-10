const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');
const assert = require('assert');

(async () => {
  const script = fs.readFileSync(require('path').join(__dirname, '../inspector.js'), 'utf8');
  let requests = [];
  let chatStatus = 200;
  const chat = {current_message_id:'u', chat:{history:{currentId:'other', messages:{
    u:{role:'user',parentId:'a'}, a:{role:'assistant',model:'model-1',usage:{prompt_tokens:42,completion_tokens:0,total_tokens:42},parentId:null},
    other:{role:'assistant',model:'wrong',usage:{total_tokens:999}}
  }}}, context_usage:{estimated_tokens:20,threshold:100}};
  const server = http.createServer((req, res) => {
    requests.push({url:req.url, auth:req.headers.authorization});
    if (req.url.startsWith('/api/v1/chats/')) {
      res.statusCode = chatStatus;
      res.setHeader('content-type','application/json');
      return res.end(chatStatus === 200 ? JSON.stringify(chat) : '{}');
    }
    if (req.url === '/api/models') { res.setHeader('content-type','application/json'); return res.end(JSON.stringify({data:[{id:'model-1',info:{meta:{capabilities:{vision:true,web_search:false}}}}]})); }
    res.setHeader('content-type','text/html'); res.end('<!doctype html><html><head></head><body><div id="chat-container"><nav><div class="flex flex-none items-center self-center"></div></nav></div></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844}});
    await page.goto(`http://127.0.0.1:${server.address().port}/c/123`);
    await page.clock.install();
    await page.evaluate(() => localStorage.setItem('token', 'example-token'));
    await page.addScriptTag({content:script});
    await page.locator('#owui-chat-inspector').waitFor();
    assert.equal(requests.filter(r => r.url.startsWith('/api/')).length, 0, 'no data read until user opens');
    // Reproduce the user's load: navbar remains in the DOM but becomes hidden;
    // the full-width tab strip and the Gallery button appear afterward.
    await page.evaluate(() => {
      document.querySelector('#chat-container nav').style.display = 'none';
      const controls = document.createElement('div'); controls.id = 'controls-container';
      controls.innerHTML = '<div data-panel-tab-bar><div id="bar"><div role="tablist" class="panel-tab-strip"><button role="tab">Chat</button></div><div><button id="plus" aria-haspopup="true" aria-expanded="false">+</button></div><button id="owui-chat-gallery-header">Gallery</button></div></div>';
      document.body.append(controls);
    });
    await page.waitForFunction(() => document.querySelector('#owui-chat-inspector')?.parentElement?.id === 'bar');
    assert.equal(await page.locator('#owui-chat-inspector').evaluate(e => e.nextElementSibling.id), 'owui-chat-gallery-header');
    // A Svelte rerender replaces the whole tab row; the icon must reattach.
    await page.evaluate(() => { const old = document.querySelector('#bar'); const fresh = old.cloneNode(true); fresh.querySelector('#owui-chat-inspector').remove(); old.replaceWith(fresh); });
    await page.waitForFunction(() => document.querySelector('#owui-chat-inspector')?.parentElement?.id === 'bar');
    await page.evaluate(() => {
      document.querySelector('#plus').setAttribute('aria-expanded', 'true');
      const menu = document.createElement('div'); menu.setAttribute('role', 'menu');
      menu.innerHTML = '<div class="app-dropdown-menu"></div>'; document.body.append(menu);
    });
    await page.locator('#owui-chat-inspector-menu').waitFor();
    await page.locator('#owui-chat-inspector-menu').click();
    await page.getByText('Configured on', {exact:true}).waitFor();
    let text = await page.locator('dialog').innerText();
    for (const expected of ['42','0','model-1','Configured off','Not explicitly set','Estimated use','20 / 100 tokens','Not measured by Open WebUI']) assert(text.includes(expected), expected);
    assert(!text.includes('999'), 'only active branch');
    assert(requests.filter(r => r.url.startsWith('/api/')).every(r => r.auth === 'Bearer example-token'));
    assert.equal(await page.locator('.ci-progress').getAttribute('role'), 'progressbar');
    // The panel must not block the chat, and a later server snapshot must appear.
    await page.locator('#plus').click();
    chat.context_usage.estimated_tokens = 65;
    chat.chat.history.messages.a.usage.total_tokens = 60;
    await page.clock.fastForward(31000);
    await page.getByText('65 / 100 tokens', {exact:false}).waitFor();
    assert((await page.locator('dialog').innerText()).includes('60'));
    const beforeHidden = requests.filter(r => r.url.startsWith('/api/')).length;
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', {configurable:true, value:true});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.clock.fastForward(61000);
    assert.equal(requests.filter(r => r.url.startsWith('/api/')).length, beforeHidden, 'no polling in hidden tabs');
    chat.context_usage.estimated_tokens = 66;
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', {configurable:true, value:false});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.getByText('66 / 100 tokens', {exact:false}).waitFor();
    chatStatus = 503;
    await page.clock.fastForward(31000);
    await page.getByText(/Update unavailable/).waitFor();
    assert((await page.locator('dialog').innerText()).includes('66 / 100 tokens'), 'retain snapshot after poll failure');
    chatStatus = 200;
    await page.locator('dialog').getByRole('button', {name:'Close'}).click();
    const beforeClosedPoll = requests.filter(r => r.url.startsWith('/api/')).length;
    await page.clock.fastForward(31000);
    assert.equal(requests.filter(r => r.url.startsWith('/api/')).length, beforeClosedPoll, 'no polling after close');
    await page.evaluate(() => document.querySelector('#plus').setAttribute('aria-expanded', 'false'));
    await page.waitForFunction(() => !document.querySelector('#owui-chat-inspector-menu'));
    assert.equal(await page.locator('dialog').evaluate(e => e.open), false);
    assert.equal(await page.locator('.ci-content').innerText(), '', 'clears private data on close');
    chat.chat.history.messages.a.model = '<img src=x onerror=alert(1)>';
    await page.locator('#owui-chat-inspector').click();
    await page.getByText('<img src=x onerror=alert(1)>', {exact:true}).waitFor();
    assert.equal(await page.locator('dialog img').count(), 0, 'untrusted text is inert');
    await page.evaluate(() => { history.pushState({}, '', '/'); document.body.append(document.createElement('i')); });
    await page.waitForFunction(() => !document.querySelector('dialog').open);
    await page.locator('#owui-chat-inspector').click();
    await page.getByText(/Temporary and new chats/).waitFor();
    // The first reply promotes a new chat to a saved route. Keep the panel open.
    await page.evaluate(() => { history.pushState({}, '', '/c/456'); document.body.append(document.createElement('i')); });
    await page.getByText('66 / 100 tokens', {exact:false}).waitFor();
    assert.equal(await page.locator('dialog').evaluate(e => e.open), true);
    // Invalid server and response usage falls back to a labeled local estimate.
    chat.context_usage = {estimated_tokens:-1, threshold:0};
    chat.chat.history.messages.a.usage = {total_tokens:'60'};
    await page.locator('dialog').getByRole('button', {name:'Refresh'}).click();
    await page.getByText('Local approximation', {exact:true}).waitFor();
    assert.equal(await page.locator('dialog .ci-progress').count(), 0);
    // Values beyond the threshold remain visible without overflowing the bar.
    chat.context_usage = {estimated_tokens:120,threshold:100};
    await page.locator('dialog').getByRole('button', {name:'Refresh'}).click();
    await page.getByText('Over threshold', {exact:true}).waitFor();
    assert.equal(await page.locator('.ci-progress-fill').evaluate(e => e.style.width), '100%');
    // Access denial clears the previous private snapshot; retry can recover.
    chatStatus = 403;
    await page.locator('dialog').getByRole('button', {name:'Refresh'}).click();
    await page.getByText(/Could not read this chat/).waitFor();
    assert.equal(await page.getByText('120 / 100 tokens', {exact:false}).count(), 0);
    chatStatus = 200;
    await page.locator('dialog').getByRole('button', {name:'Refresh'}).click();
    await page.getByText('120 / 100 tokens', {exact:false}).waitFor();
    await page.evaluate(() => {
      localStorage.setItem('token', 'changed-token');
      window.dispatchEvent(new StorageEvent('storage'));
    });
    await page.waitForFunction(() => !document.querySelector('dialog').open);
    assert.equal(await page.locator('.ci-content').innerText(), '', 'clears data after session change');
    await page.close();
    console.log('Browser checks passed');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
