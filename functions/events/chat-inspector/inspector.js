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
  let button, menuButton, dialog, content, controller, openedPath, openedToken, sequence = 0;
  let pollTimer = null, lastModelId = null, cachedModels = null, lastModelFetch = 0, failedPolls = 0;
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
  function row(list, name, value) {
    const pair = el('div', 'ci-row');
    pair.append(el('dt', '', name), el('dd', '', value)); list.append(pair);
  }
  function section(title, description) {
    const block = el('section'); block.append(el('h3', '', title));
    if (description) block.append(el('p', 'ci-note', description));
    const list = el('dl'); block.append(list); content.append(block); return list;
  }
  function branchMessages(chat) {
    const history = chat?.chat?.history;
    const messages = history?.messages;
    if (!messages || typeof messages !== 'object') return [];
    let id = chat.current_message_id || history.currentId;
    const seen = new Set(), result = [];
    while (typeof id === 'string' && id && !seen.has(id) && seen.size < 10000) {
      seen.add(id);
      const msg = Object.prototype.hasOwnProperty.call(messages, id) ? messages[id] : null;
      if (!msg || typeof msg !== 'object') break;
      result.push(msg); id = msg.parentId;
    }
    return result.reverse();
  }
  function estimateTokens(value) {
    if (value === null || value === undefined || value === '') return 0;
    let serialized;
    try { serialized = typeof value === 'string' ? value : JSON.stringify(value); }
    catch { return 0; }
    // Image and other data URIs do not represent that many text tokens.
    if (typeof serialized !== 'string') return 0;
    serialized = serialized.replace(/data:[\w/+.;=%-]*;base64,[A-Za-z0-9+/=]*/g, '');
    return serialized ? Math.max(1, Math.floor(serialized.length / 4)) : 0;
  }
  function estimateMessage(message) {
    return 4 + (Array.isArray(message.output) && message.output.length
      ? estimateTokens(message.output)
      : estimateTokens(message.content) + estimateTokens(message.tool_calls));
  }
  function localEstimate(branch) {
    if (!branch.length) return null;
    let start = 0, summary = '';
    branch.forEach((message, index) => {
      const value = message.contextSummary || message.context_summary;
      if (typeof value === 'string' && value.trim()) { start = index; summary = value; }
    });
    const active = branch.slice(start);
    for (let index = active.length - 1; index >= 0; index--) {
      const usage = active[index].usage ?? active[index].info?.usage;
      const input = firstNumber(usage, ['input_tokens', 'prompt_tokens', 'prompt_eval_count']);
      const output = firstNumber(usage, ['output_tokens', 'completion_tokens', 'eval_count']);
      const count = firstNumber(usage, ['total_tokens']) ?? (input !== null || output !== null ? (input ?? 0) + (output ?? 0) : null);
      if (count !== null && count > 0) return count + active.slice(index + 1).reduce((sum, message) => sum + estimateMessage(message), 0);
    }
    return estimateTokens(summary) + active.reduce((sum, message) => sum + estimateMessage(message), 0);
  }
  function renderChat(data) {
    const branch = branchMessages(data);
    const msg = [...branch].reverse().find(item => item.role === 'assistant') ?? null;
    const usage = msg?.usage ?? msg?.info?.usage;
    const model = typeof msg?.model === 'string' ? msg.model : null;
    const context = data?.context_usage;
    const serverEstimate = firstNumber(context, ['estimated_tokens', 'tokens']);
    const estimated = serverEstimate ?? localEstimate(branch);
    const threshold = firstNumber(context, ['threshold']);
    const ctx = section('Context', 'Estimated from the saved conversation branch. Local approximation omits unknown prompt, tool and attachment costs. The compaction threshold, when available, is not necessarily the model context window.');
    row(ctx, 'Source', serverEstimate !== null ? 'Open WebUI server estimate' : estimated !== null ? 'Local approximation' : 'Not available');
    if (estimated !== null && threshold !== null && threshold > 0) {
      const percent = Math.round(estimated / threshold * 100);
      row(ctx, 'Estimated use', fmt(estimated) + ' / ' + fmt(threshold) + ' tokens · ' + percent + '%');
      const bar = el('div', 'ci-progress'); bar.setAttribute('role', 'progressbar');
      bar.setAttribute('aria-label', 'Estimated use of compaction threshold');
      bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', String(threshold));
      bar.setAttribute('aria-valuenow', String(Math.min(estimated, threshold)));
      const fill = el('div', 'ci-progress-fill'); fill.style.width = Math.min(100, percent) + '%';
      bar.append(fill); ctx.parentElement.append(bar);
      row(ctx, estimated > threshold ? 'Over threshold' : 'Until threshold', fmt(Math.abs(threshold - estimated)) + ' tokens');
    } else {
      row(ctx, 'Estimated use', estimated === null ? 'Not available' : fmt(estimated) + ' tokens');
      row(ctx, 'Threshold', threshold === null ? 'Not available' : fmt(threshold) + ' tokens');
    }
    row(ctx, 'Messages on this branch', fmt(branch.length));
    row(ctx, 'Breakdown by prompt, tools and skills', 'Not measured by Open WebUI');
    const list = section('Last assistant response', 'Provider or Open WebUI usage for one response. Counts may be absent or incomplete during generation.');
    row(list, 'Model used', model || 'Not recorded');
    row(list, 'Input tokens', fmt(firstNumber(usage, ['input_tokens', 'prompt_tokens', 'prompt_eval_count'])));
    row(list, 'Output tokens', fmt(firstNumber(usage, ['output_tokens', 'completion_tokens', 'eval_count'])));
    row(list, 'Total tokens', fmt(firstNumber(usage, ['total_tokens'])));
    const inventory = section('Saved chat configuration', 'This is saved metadata, not the live composer selection or a list of tools actually sent to the model.');
    const chat = data?.chat;
    for (const [field, label] of [['tool_ids','Workspace tools'], ['tool_servers','Tool servers'], ['filter_ids','Filters'], ['skill_ids','Skills']]) {
      const value = chat?.[field];
      row(inventory, label, Array.isArray(value) ? fmt(value.length) + ' listed' : 'Not recorded');
    }
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
  function schedulePoll(delay = 30000) {
    clearTimeout(pollTimer);
    if (dialog?.open && !document.hidden) pollTimer = setTimeout(() => refresh(false), delay);
  }
  async function refresh(showLoading = true) {
    if (!showLoading && (controller || document.hidden || !dialog?.open)) return;
    if (dialog?.open && openedPath !== location.pathname) {
      if (openedPath === '/' && route()) openedPath = location.pathname;
      else { close(); return; }
    }
    clearRequest(); const mySequence = sequence;
    controller = new AbortController(); const current = controller;
    const timeout = setTimeout(() => current.abort(), 15000);
    if (showLoading) content.replaceChildren(el('p', 'ci-note', 'Loading chat information…'));
    try {
      const id = route();
      if (sessionToken() !== openedToken) { close(); return; }
      if (!id) { content.replaceChildren(el('p', 'ci-note', 'Open a saved chat to inspect its last response. Temporary and new chats are not available here.')); return; }
      const chat = await get('/api/v1/chats/' + encodeURIComponent(id), current.signal, openedToken);
      if (mySequence !== sequence || location.pathname !== openedPath || sessionToken() !== openedToken || !dialog?.open) return;
      content.replaceChildren();
      const modelId = renderChat(chat);
      if (modelId) {
        try {
          if (!cachedModels || lastModelId !== modelId || Date.now() - lastModelFetch > 300000) {
            cachedModels = await get('/api/models', current.signal, openedToken);
            lastModelFetch = Date.now(); lastModelId = modelId;
          }
          if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken && dialog?.open) renderModel(cachedModels, modelId);
        } catch (e) {
          if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken && dialog?.open) {
            const list = section('Model configuration'); row(list, 'Settings', current.signal.aborted ? 'Request timed out' : 'Unavailable');
          }
        }
      } else renderModel(null, null);
      failedPolls = 0;
    } catch (e) {
      if (mySequence === sequence && location.pathname === openedPath && sessionToken() === openedToken && dialog?.open) {
        failedPolls = Math.min(failedPolls + 1, 3);
        if (showLoading) content.replaceChildren(el('p', 'ci-note', current.signal.aborted ? 'The request timed out. Try Refresh.' : 'Could not read this chat. Check your access and try Refresh.'));
        else { content.querySelector('.ci-error')?.remove(); content.prepend(el('p', 'ci-note ci-error', 'Update unavailable; showing the previous snapshot.')); }
      }
    } finally {
      clearTimeout(timeout); if (controller === current) controller = null;
      if (mySequence === sequence) schedulePoll(30000 * (2 ** failedPolls));
    }
  }
  function close() {
    clearTimeout(pollTimer); clearRequest(); openedToken = undefined; cachedModels = null;
    if (dialog?.open) dialog.close();
    content?.replaceChildren();
    if (button?.isConnected) button.focus();
  }
  function open() {
    if (dialog?.open) { close(); return; }
    if (!dialog) {
      dialog = el('dialog', 'ci-dialog'); dialog.setAttribute('aria-labelledby', 'ci-title');
      const header = el('header', 'ci-header'); const title = el('h2', '', 'Chat context'); title.id = 'ci-title';
      const dismiss = el('button', 'ci-button', 'Close'); dismiss.type = 'button'; dismiss.addEventListener('click', close);
      header.append(title, dismiss);
      const note = el('p', 'ci-note', 'Updates every 30 seconds while visible. The chat stays usable. Refresh for an immediate snapshot.');
      content = el('div', 'ci-content');
      const reload = el('button', 'ci-button', 'Refresh'); reload.type = 'button'; reload.addEventListener('click', () => refresh(true));
      dialog.append(header, note, content, reload); document.body.append(dialog);
      dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
      dialog.addEventListener('close', () => { clearTimeout(pollTimer); clearRequest(); openedToken = undefined; cachedModels = null; content.replaceChildren(); });
    }
    openedPath = location.pathname; openedToken = sessionToken(); failedPolls = 0;
    try { dialog.show(); } catch { return; }
    refresh(true);
  }
  // The native navbar can stay mounted but hidden when the full-width tab bar
  // appears. Only attach to a visible host; otherwise the button disappears.
  function visible(node) { return !!node && node.isConnected && node.getClientRects().length > 0; }
  function sync() {
    if (dialog?.open && openedToken !== sessionToken()) close();
    if (dialog?.open && openedPath !== location.pathname) {
      if (openedPath === '/' && route()) { openedPath = location.pathname; refresh(true); }
      else close();
    }
    const chatRoute = location.pathname === '/' || !!route();
    const groups = [...document.querySelectorAll('#chat-container nav .flex.flex-none.items-center.self-center')];
    const nav = groups.reverse().find(visible);
    const controls = document.getElementById('controls-container');
    const lists = controls && visible(controls)
      ? [...controls.querySelectorAll('.panel-tab-strip')].filter(visible) : [];
    const host = chatRoute ? (nav || lists[0]?.parentElement) : null;
    if (!host) button?.remove();
    else {
      if (!button) {
        button = el('button', 'ci-trigger', 'ⓘ'); button.type = 'button'; button.id = 'owui-chat-inspector';
        button.setAttribute('aria-label', 'Chat inspector'); button.title = 'Chat inspector'; button.addEventListener('click', open);
      }
      // Gallery keeps its button at the end of the tab row. Insert before it,
      // otherwise the two independent MutationObservers would fight forever.
      const gallery = host.querySelector(':scope > #owui-chat-gallery-header');
      if (button.parentElement !== host) host.insertBefore(button, gallery || null);
    }

    // Match Gallery's + menu integration. Never attach to a chat's unrelated
    // overflow menu; only use the visible panel whose + button is expanded.
    const expanded = chatRoute && lists.find(list => {
      const plus = list.parentElement?.querySelector(':scope > :not([role="tablist"]) > button[aria-haspopup="true"]');
      return plus?.getAttribute('aria-expanded') === 'true';
    });
    const menus = [...document.querySelectorAll('body > [role="menu"]')].filter(visible);
    const menu = expanded ? menus.at(-1)?.querySelector('.app-dropdown-menu') : null;
    if (!menu) menuButton?.remove();
    else {
      if (!menuButton) {
        menuButton = el('button', 'ci-menu-button', 'ⓘ  Chat inspector');
        menuButton.type = 'button'; menuButton.id = 'owui-chat-inspector-menu';
        menuButton.addEventListener('click', open);
      }
      if (menuButton.parentElement !== menu) menu.append(menuButton);
    }
  }
  let pending = false;
  function schedule() { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; sync(); }); } }
  function start() {
    const style = el('style'); style.textContent = `
      .ci-trigger{border:0;background:transparent;color:inherit;font:inherit;font-size:1.1rem;cursor:pointer;padding:.4rem;border-radius:.5rem}
      .ci-trigger:hover,.ci-button:hover{background:rgba(127,127,127,.15)}
      .ci-dialog{position:fixed;inset:3rem 1rem auto auto;margin:0;width:min(22rem,calc(100vw - 2rem));max-height:min(80vh,48rem);overflow:auto;box-sizing:border-box;border:1px solid #8886;border-radius:1rem;padding:1.2rem;background:var(--color-white,#fff);color:var(--color-gray-900,#171717);font:inherit;font-size:.85rem;box-shadow:0 12px 40px #0005;z-index:55}
      .dark .ci-dialog{background:var(--color-gray-900,#171717);color:var(--color-gray-100,#eee);color-scheme:dark}
      .ci-progress{height:.4rem;border-radius:1rem;background:#8883;overflow:hidden;margin:.5rem 0}.ci-progress-fill{height:100%;background:#3b82f6}.ci-error{color:#b45309}.ci-dialog::backdrop{background:#0008}.ci-header{display:flex;align-items:center;justify-content:space-between;gap:1rem}.ci-header h2{font-size:1.15rem;font-weight:650;margin:0}
      .ci-dialog h3{font-weight:650;margin:1rem 0 .25rem}.ci-note{font-size:.85rem;opacity:.8;margin:.5rem 0}.ci-row{display:flex;justify-content:space-between;gap:1rem;padding:.3rem 0;border-bottom:1px solid #8883}.ci-row dd{margin:0;text-align:right;overflow-wrap:anywhere}.ci-button{font:inherit;color:inherit;background:transparent;border:1px solid #8887;border-radius:.5rem;padding:.4rem .7rem;cursor:pointer}.ci-dialog :focus-visible,.ci-trigger:focus-visible{outline:2px solid #3b82f6;outline-offset:2px}
    `; document.head.append(style);
    new MutationObserver(schedule).observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['class', 'style', 'hidden', 'aria-expanded']});
    window.addEventListener('popstate', schedule); window.addEventListener('storage', schedule);
    window.addEventListener('focus', () => { schedule(); if (dialog?.open && !controller) refresh(false); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(pollTimer); else if (dialog?.open && !controller) refresh(false); }); schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true}); else start();
})();
