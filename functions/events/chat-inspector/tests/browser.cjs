const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');
const assert = require('assert');

(async () => {
  const script = fs.readFileSync(require('path').join(__dirname, '../inspector.js'), 'utf8');
  let requests = [];
  const chat = {current_message_id:'u', chat:{history:{currentId:'other', messages:{
    u:{role:'user',parentId:'a'}, a:{role:'assistant',model:'model-1',usage:{prompt_tokens:42,completion_tokens:0,total_tokens:42},parentId:null},
    other:{role:'assistant',model:'wrong',usage:{total_tokens:999}}
  }}}, context_usage:{estimated_tokens:20,threshold:100}};
  const server = http.createServer((req, res) => {
    requests.push({url:req.url, auth:req.headers.authorization});
    if (req.url.startsWith('/api/v1/chats/')) { res.setHeader('content-type','application/json'); return res.end(JSON.stringify(chat)); }
    if (req.url === '/api/models') { res.setHeader('content-type','application/json'); return res.end(JSON.stringify({data:[{id:'model-1',info:{meta:{capabilities:{vision:true,web_search:false}}}}]})); }
    res.setHeader('content-type','text/html'); res.end('<!doctype html><html><head></head><body><div id="chat-container"><nav><div class="flex flex-none items-center self-center"></div></nav></div></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844}});
    await page.goto(`http://127.0.0.1:${server.address().port}/c/123`);
    await page.evaluate(() => localStorage.setItem('token', 'example-token'));
    await page.addScriptTag({content:script});
    await page.locator('#owui-chat-inspector').waitFor();
    assert.equal(requests.filter(r => r.url.startsWith('/api/')).length, 0, 'no data read until user opens');
    await page.locator('#owui-chat-inspector').click();
    await page.getByText('Configured on', {exact:true}).waitFor();
    let text = await page.locator('dialog').innerText();
    for (const expected of ['42','0','model-1','Configured off','Not explicitly set','Compaction threshold']) assert(text.includes(expected), expected);
    assert(!text.includes('999'), 'only active branch');
    assert(requests.filter(r => r.url.startsWith('/api/')).every(r => r.auth === 'Bearer example-token'));
    await page.keyboard.press('Escape');
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
    await page.close();
    console.log('Browser checks passed');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
