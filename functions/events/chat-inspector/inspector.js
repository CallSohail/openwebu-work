/* Chat Inspector: source for the embedded browser fragment. */
(() => {
  'use strict';
  if (window.__owuiChatInspector) return;
  window.__owuiChatInspector = true;
  const labels = {
    vision: 'Vision', file_upload: 'File uploads', file_context: 'File context',
    web_search: 'Web search', image_generation: 'Image generation',
    code_interpreter: 'Code interpreter', terminal: 'Terminal', usage: 'Usage display',
    citations: 'Citations', status_updates: 'Status updates', memory: 'Memory', builtin_tools: 'Built-in tools'
  };
  let button, dialog, content, controller, openedPath, openedToken, sequence = 0;
  const sessionToken = () => { try { return localStorage.getItem('token') || ''; } catch { return ''; } };
  const el = (tag, cls, value) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value != null) node.textContent = String(value);
    return node;
  };
  const route = () => {
    const match = /^\/c\/([a-zA-Z0-9-]+)\/?$/.exec(location.pathname);
    return match ? match[1] : null;
  };
  const number = value => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
  const firstNumber = (object, keys) => {
    for (const key of keys) { const n = number(object?.[key]); if (n !== null) return n; }
    return null;
  };
  const fmt = n => n === null ? 'Not reported' : n.toLocaleString();
  function lastAssistant(chat) {
    const history = chat?.chat?.history;
    const messages = history?.messages;
    if (!messages || typeof messages !== 'object') return null;
    let id = chat.current_message_id || history.currentId;
    const seen = new Set();
    while (typeof id === 'string' && id && !seen.has(id) && seen.size < 10000) {
      seen.add(id);
      const msg = Object.prototype.hasOwnProperty.call(messages, id) ? messages[id] : null;
      if (!msg || typeof msg !== 'object') return null;
      if (msg.role === 'assistant') return msg;
      id = msg.parentId;
    }
    return null;
  }
  function row(list, name, value) {
    const pair = el('div', 'ci-row');
    pair.append(el('dt', '', name), el('dd', '', value)); list.append(pair);
  }
  function section(title, description) {
    const block = el('section'); block.append(el('h3', '', title));
    if (description) block.append(el('p', 'ci-note', description));
    const list = el('dl'); block.append(list); content.append(block); return list;
  }
  function renderChat(data) {
    const msg = lastAssistant(data);
    const usage = msg?.usage ?? msg?.info?.usage;
    const model = typeof msg?.model === 'string' ? msg.model : null;
    const list = section('Last assistant response returned by the server', 'Usage comes from the provider or Open WebUI. It is not a bill, a quota, or a conversation total.');
    row(list, 'Model', model || 'Not recorded');
    row(list, 'Input tokens', fmt(firstNumber(usage, ['input_tokens', 'prompt_tokens', 'prompt_eval_count'])));
    row(list, 'Output tokens', fmt(firstNumber(usage, ['output_tokens', 'completion_tokens', 'eval_count'])));
    row(list, 'Total tokens', fmt(firstNumber(usage, ['total_tokens'])));
    const context = data?.context_usage;
    const ctx = section('Saved context estimate', 'Open WebUI provides this estimate when context compaction is enabled. Its threshold is not necessarily the model context window.');
    row(ctx, 'Estimated tokens', fmt(firstNumber(context, ['estimated_tokens', 'tokens'])));
    row(ctx, 'Compaction threshold', fmt(firstNumber(context, ['threshold'])));
    return model;
  }
  function renderModel(data, modelId) {
    const list = section('Model configuration', 'These are explicit model settings, not proof a feature is enabled for this user or selected in this chat. Provider support, permissions, global settings and tools also matter.');
    if (!modelId) { row(list, 'Settings', 'No saved response model'); return; }
    const models = Array.isArray(data?.data) ? data.data : [];
    const model = models.find(item => item && item.id === modelId);
    if (!model) { row(list, 'Settings', 'Model not found in the available server catalog'); return; }
    const caps = model?.info?.meta?.capabilities;
    for (const [key, label] of Object.entries(labels)) {
      const value = caps && Object.prototype.hasOwnProperty.call(caps, key) ? caps[key] : undefined;
      row(list, label, value === true ? 'Configured on' : value === false ? 'Configured off' : 'Not explicitly set');
    }
  }
  async function get(path, signal, token) {
    const headers = {Accept: 'application/json'};
    if (token) headers.Authorization = 'Bearer ' + token;
    const response = await fetch(path, {method: 'GET', headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal});
    if (!response.ok) throw new Error('Request unavailable');
    return response.json();
  }
  function clearRequest() { sequence++; controller?.abort(); controller = null; }
  async function refresh() {
    clearRequest(); const mySequence = sequence;
    controller = new AbortController(); const current = controller;
    const timeout = setTimeout(() => current.abort(), 15000);
    content.replaceChildren(el('p', 'ci-note', 'Loading saved chat information…'));
    try {
      const id = route();
      if (sessionToken() !== openedToken) { close(); return; }
      if (!id) { content.replaceChildren(el('p', 'ci-note', 'Open a saved chat to inspect its last response. Temporary and new chats are not available here.')); return; }
      const chat = await get('/api/v1/chats/' + encodeURIComponent(id), current.signal, openedToken);
      if (mySequence !== sequence || location.pathname !== openedPath || sessionToken() !== openedToken) return;
      content.replaceChildren();
      const modelId = renderChat(chat);
      if (modelId) {
        try {
          const models = await get('/api/models', current.signal, openedToken);
          if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken) renderModel(models, modelId);
        } catch (e) {
          if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken) {
            const list = section('Model configuration'); row(list, 'Settings', current.signal.aborted ? 'Request timed out' : 'Unavailable');
          }
        }
      } else renderModel(null, null);
    } catch (e) {
      if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken) {
        content.replaceChildren(el('p', 'ci-note', current.signal.aborted ? 'The request timed out. Try Refresh.' : 'Could not read this chat. Check your access and try Refresh.'));
      }
    } finally { clearTimeout(timeout); if (controller === current) controller = null; }
  }
  function close() {
    clearRequest(); openedToken = undefined;
    if (dialog?.open) dialog.close();
    content?.replaceChildren();
    if (button?.isConnected) button.focus();
  }
  function open() {
    if (!dialog) {
      dialog = el('dialog', 'ci-dialog'); dialog.setAttribute('aria-labelledby', 'ci-title');
      const header = el('header', 'ci-header'); const title = el('h2', '', 'Chat inspector'); title.id = 'ci-title';
      const dismiss = el('button', 'ci-button', 'Close'); dismiss.type = 'button'; dismiss.addEventListener('click', close);
      header.append(title, dismiss);
      const note = el('p', 'ci-note', 'Server snapshot of this chat. A response in progress may be incomplete; local unsaved changes and temporary chats are not included.');
      content = el('div', 'ci-content'); content.setAttribute('aria-live', 'polite');
      const reload = el('button', 'ci-button', 'Refresh'); reload.type = 'button'; reload.addEventListener('click', refresh);
      dialog.append(header, note, content, reload); document.body.append(dialog);
      dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
      dialog.addEventListener('close', () => { clearRequest(); openedToken = undefined; content.replaceChildren(); });
    }
    openedPath = location.pathname; openedToken = sessionToken();
    try { dialog.showModal(); } catch { return; }
    refresh();
  }
  function sync() {
    if (dialog?.open && (openedPath !== location.pathname || openedToken !== sessionToken())) close();
    const host = [...document.querySelectorAll('#chat-container nav .flex.flex-none.items-center.self-center')].pop() || document.querySelector('#controls-container .panel-tab-strip')?.parentElement;
    if (!host || !(location.pathname === '/' || route())) { button?.remove(); return; }
    if (!button) {
      button = el('button', 'ci-trigger', 'ⓘ'); button.type = 'button'; button.id = 'owui-chat-inspector';
      button.setAttribute('aria-label', 'Chat inspector'); button.title = 'Chat inspector'; button.addEventListener('click', open);
    }
    if (button.parentElement !== host) host.append(button);
  }
  let pending = false;
  function schedule() { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; sync(); }); } }
  function start() {
    const style = el('style'); style.textContent = `
      .ci-trigger{border:0;background:transparent;color:inherit;font:inherit;font-size:1.1rem;cursor:pointer;padding:.4rem;border-radius:.5rem}
      .ci-trigger:hover,.ci-button:hover{background:rgba(127,127,127,.15)}
      .ci-dialog{width:min(36rem,calc(100vw - 2rem));max-height:min(85vh,52rem);overflow:auto;box-sizing:border-box;border:1px solid #8886;border-radius:1rem;padding:1.2rem;background:var(--color-white,#fff);color:var(--color-gray-900,#171717);font:inherit;box-shadow:0 12px 40px #0005}
      .dark .ci-dialog{background:var(--color-gray-900,#171717);color:var(--color-gray-100,#eee);color-scheme:dark}
      .ci-dialog::backdrop{background:#0008}.ci-header{display:flex;align-items:center;justify-content:space-between;gap:1rem}.ci-header h2{font-size:1.15rem;font-weight:650;margin:0}
      .ci-dialog h3{font-weight:650;margin:1rem 0 .25rem}.ci-note{font-size:.85rem;opacity:.8;margin:.5rem 0}.ci-row{display:flex;justify-content:space-between;gap:1rem;padding:.3rem 0;border-bottom:1px solid #8883}.ci-row dd{margin:0;text-align:right;overflow-wrap:anywhere}.ci-button{font:inherit;color:inherit;background:transparent;border:1px solid #8887;border-radius:.5rem;padding:.4rem .7rem;cursor:pointer}.ci-dialog :focus-visible,.ci-trigger:focus-visible{outline:2px solid #3b82f6;outline-offset:2px}
    `; document.head.append(style);
    new MutationObserver(schedule).observe(document.body, {childList:true, subtree:true});
    window.addEventListener('popstate', schedule); window.addEventListener('storage', schedule); window.addEventListener('focus', schedule); schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true}); else start();
})();
