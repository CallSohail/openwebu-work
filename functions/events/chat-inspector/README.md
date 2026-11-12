# Chat Inspector for Open WebUI

How full is this chat? Chat Inspector adds **Context** to the chat's **⋯** menu. It opens a pop-up that shows how much of the model's context window the chat uses, the way Claude does: a grid of squares, one per percent, coloured by what fills them, and a breakdown of each part. It also lists every tool the chat called, the way Claude shows what it used: MCP tools, workspace tools, tool servers and each built-in tool, with web searches, pages read, sources, code runs and images, how each call went and how long it took.

Version v1.2.0. Event Function. Requires Open WebUI v0.13.0 or later, the release that added the Tabs panel; tested in a browser on v0.13.0 and v0.13.3.

It is a companion to [Chat Gallery](../chat-gallery/), [Chat Files](../chat-files/) and [Artifacts](../chat-artifacts/), built the same way.

| The pop-up, from the ⋯ menu | Phone: a sheet from the bottom | Full view, over the window |
| --- | --- | --- |
| ![The Context pop-up under the chat's ⋯ button, beside the conversation and above the message box: 1,371 of 8,192 tokens, 17% full, a grid and a breakdown.](screenshots/popup-light.png) | ![Context as a sheet over the lower part of a phone screen in dark mode, with a ten-column grid and the platform's name, EveGPT, in the breakdown.](screenshots/phone-dark.png) | ![The full view of a chat at 121%, every square filled, with a red warning that the model can't see everything any more.](screenshots/over-dark.png) |

![Tools and activity in the pop-up: the last answer's calls in order, search_web and fetch_url under Built-in · Web search, search_pages under MCP · Docs MCP, add under Calculator, a failed call in red and a declined one in grey, one opened to show its input and result.](screenshots/tools-light.png)

The wording uses your platform's own name. These screenshots come from a server named EveGPT.

## What it shows

- **How full the chat is.** Tokens in use, out of the model's context window, as a number, a percentage and a grid. It warns as the chat fills up (80 % by default) and once it is past the window, and says roughly how many more exchanges like the recent ones fit.
- **What is using the space.**

  | Part | Where it comes from |
  | --- | --- |
  | System prompt | The model's, the chat's or your own, and the folder's |
  | Tools, documents and instructions Open WebUI added | Worked out: the model's own count minus everything that could be estimated. Tool definitions, retrieved documents, memories and Open WebUI's own instructions live here |
  | Summary of earlier messages | Once Open WebUI has compacted the chat |
  | Messages | The conversation on the branch you are looking at |
  | Latest answer | The newest answer's visible text |
  | Since the last count | Messages after the model last counted |
  | Tool calls and results | Each call's name and input and the result it returned, which go back to the model on later turns |
  | Free space, and reserved before summarising | What is left, and the part beyond Open WebUI's summary threshold when compaction is on |

  Each part is labelled *counted* by the model, *estimated* from the text, or *worked out*.
- **Tools and activity**, over the conversation:
  - counts of tool calls (with how many failed, were declined or are still running), web searches, pages read, knowledge lookups, sources used, code runs and images made;
  - one row per tool, saying where it comes from and how many times it ran, with failures, time and the tokens its calls and results take:

    | Kind | How it is told apart |
    | --- | --- |
    | MCP | Its name starts with an MCP server's id (`docs_search_pages` is `search_pages` on *Docs MCP*) |
    | Workspace tool | A function of a tool in the workspace, named after that tool (*Calculator*) |
    | Tool server | A function of an OpenAPI tool server, named after the server |
    | Built-in | Open WebUI's own tools, grouped as web search, knowledge, memory, chat files, notes, past chats, channels, images, code, time, skills, sub-agents, tasks, automations, calendar and notifications |
    | Run by the provider | Web search, file search or computer use the provider ran itself (OpenAI Responses) |
    | Added by the platform | Calls Open WebUI makes by itself before the answer, such as recalling memories or loading a skill |

  - the queries searched for, and any tool server that couldn't connect.
- **The last answer.** Tokens the model read and wrote, reasoning and cached tokens, how many times the model was called (once per round of tool calls), thinking time, speed, time and cost when the provider reports one. Then each tool call in order, with its state; opening one shows its input and result.
- **The conversation.** Messages, answers, how many the model counted, the tokens read and written in all, model calls, the tokens tool results take, and cost.
- **The model.** What it is set up to do, its tools and knowledge bases, the filters that run on it (and which only when switched on), its actions, and the pipe function that answers for it.

In a chat that asks several models at once, a picker chooses whose context to show.

## Where the numbers come from

**The model's own count wins.** Most providers report how many tokens they read for each answer. When they do, Context fits everything to that number: the parts it can estimate are scaled to it, and the rest is shown as the *worked-out* share. Totals then match what the model saw.

**Otherwise it estimates**, with tuning made against the real tokenizers of GPT-4o, GPT-4, Llama 3.1, Qwen 2.5 and Mistral. On 1,109 passages it was not tuned on, in 20 languages plus code, the average error is:

| Model family | Context's estimate | Characters ÷ 4 |
| --- | --- | --- |
| GPT-4o, GPT-4.1, GPT-5, o-series | 8 % | 19 % |
| GPT-4, GPT-3.5 | 9 % | 36 % |
| Llama | 12 % | 26 % |
| Qwen | 12 % | 30 % |
| Mistral | 7 % | 42 % |

The family is recognised from the model's id. Other models get Llama's tuning, which had the smallest worst-case error across all five. [`tests/fit_tokens.py`](tests/fit_tokens.py) reproduces the tuning.

**Tool calls are read from what each answer saved.** Open WebUI saves a native call and its result as a pair; a call made without native function calling leaves its result in the answer's sources; chats from older versions have calls inside the answer text. All three are counted. With tool calls, Open WebUI adds the provider's input counts of every round together; Context fits the window to the last round's own prompt count, which is what the model held, and shows the added-up figure as what the model read.

**The context window** comes from, in this order:

1. an administrator's rule in the `context_windows` valve;
2. the model's `num_ctx` setting, or the chat's;
3. your own `num_ctx` setting, for models served by Ollama;
4. what the provider reports (`context_length`), as OpenRouter and many others do;
5. for a model made in the workspace, the window of the model it is built on.

When none of these is known, Context shows the tokens in use without a percentage, and says so.

## Opening it

In any saved chat, open the chat's **⋯** menu (beside the chat's name, in the tab row or the top bar) and choose **Context**, just under **Overview**. Every account has that menu, plain users included.

Context opens as a **pop-up** under the ⋯ button. The conversation stays visible and usable beside it: you can keep typing and sending, and the pop-up updates when each answer finishes. It stops above the message box rather than covering it. Close it with its **×** or **Escape**, and focus goes back to the ⋯ button. If you move to another chat while it is open, it shows that chat.

On a **phone** it opens as a sheet from the bottom of the screen; tap outside it to close it.

Context reads the chat when it opens and once an answer has finished. It watches the page, not the server, and doesn't poll. While an answer is being written, it says so.

Prefer the full view? Set `open_as` to `tab` and Context fills the chat's pane like the Tabs panel's own tabs. Two more ways in are optional: the Tabs panel's **+** menu (`show_in_plus_menu`) and a button beside the tabs (`header_button`).

## Its name for the platform

Wherever Context names the platform ("tools and instructions EveGPT added", "summarised by EveGPT"), it uses the name your administrator gave Open WebUI with `WEBUI_NAME`, read from the server. Without a custom name it says "Open WebUI". The `platform_name` valve sets it explicitly.

## Light, dark and themes

Context takes its colours from Open WebUI's palette and follows light, dark, OLED black and *Her*. Every text was measured at 4.5:1 contrast or better in all four, warnings included.

## Install or upgrade

1. Open **Admin Panel > Functions** as an administrator.
2. Create a function with the id `chat_inspector`, or open the existing Chat Inspector, and paste [`chat_inspector.py`](chat_inspector.py). Review the code first, as with any function: it runs on your server.
3. Enable it, then hard-refresh open Open WebUI tabs (`Ctrl+Shift+R`, or `Cmd+Shift+R` on a Mac).

Upgrading from 0.2.0 or 1.0.0 keeps the same function id. In 1.1.0 Context moved to the chat's ⋯ menu and opens as a pop-up; the + menu entry is off unless you turn on `show_in_plus_menu`.

## Settings

| Valve | Default | What it does |
| --- | --- | --- |
| `context_windows` | empty | Context windows for models whose provider doesn't report one, or to correct one. One rule per line or comma, pattern `=` size: `gpt-4o* = 128000, llama3.1* = 128k, campus-model = 32768`. `k` and `m` mean thousand and million. The first matching rule wins, and a rule beats what the provider says. |
| `warn_percent` | 80 | How full the window is before Context says it is getting full. |
| `show_capabilities` | on | Show what the model is set up to do. |
| `platform_name` | empty | The name Context uses for the platform, such as `EveGPT`. Empty uses the server's `WEBUI_NAME`. |
| `open_as` | `popup` | `popup`: a pop-up under the ⋯ button, with the chat still in view. `tab`: the full view, like the Tabs panel's tabs. |
| `show_in_chat_menu` | on | Add Context to the chat's ⋯ menu, under Overview. |
| `show_in_plus_menu` | off | Also add it to the Tabs panel's + menu, where it opens as a tab. |
| `header_button` | off | Also add a Context button beside the tabs. It opens the way `open_as` says. |
| `language` | `auto` | `auto` follows each person's Open WebUI language: French where it is French, English elsewhere. |

## What it reads

Chat Inspector runs in the browser with the signed-in person's own access and only reads:

- the open chat, from `GET /api/v1/chats/{id}`, including Open WebUI's compaction threshold when compaction is on;
- the models the person can use, from `GET /api/models`, at most every five minutes;
- the person's settings, from `GET /api/v1/users/user/settings`, for their system prompt and context size;
- the chat's folder, from `GET /api/v1/folders/{id}`, for its system prompt;
- the platform's name, from `GET /api/config`;
- the tools and functions the person can see, from `GET /api/v1/tools/` and `GET /api/v1/functions/`, at most every five minutes, to tell what kind each called tool is and which filters and actions apply. If either list is refused, tools are still counted, just without saying where they come from.

It changes nothing, stores nothing, adds no server routes and sends nothing anywhere else. The server half only serves the script through `/static/loader.js`.

## Limits

- **Estimates are estimates.** Without the model's own count, Context can't see tool definitions, retrieved documents or memories Open WebUI adds. It says so, and the numbers will be low.
- **Images** cost tokens that depend on the model; they aren't estimated, and are counted only within the model's own count.
- **The count is from the last answer.** Anything added since, such as a newly attached file, shows up only as estimated text until the next answer.
- **Reasoning tokens** are shown for the last answer but not counted as context, since most providers don't send reasoning back.
- **Tool kinds come from names.** Open WebUI doesn't save which kind of tool ran. A tool in a list the person can't see, or one whose name a workspace tool and a built-in share, may be shown as a plain *Tool* or under the wrong kind.
- **Filters don't leave a trace.** Which filters apply is worked out from the model and the functions' settings; a filter that is switched on in the message box at send time isn't saved, so toggleable ones are listed as *when switched on*.
- **Web searches made before the answer** (the web search button rather than a tool call) are counted from Open WebUI's status lines; a chat saved without them shows only the sources.
- **Cost** is shown only when the provider reports one, in the provider's own unit.
- **A temporary or unsaved chat** has nothing stored to read.
- **The summary threshold** is when Open WebUI compacts the chat, not the model's window; both are shown.
- **Interface markers.** Like its companions, Context finds Open WebUI's interface by markers that stayed stable from v0.13.0 to v0.13.3 (`panel-tab-strip`, `data-panel-group`, `app-dropdown-menu`, `controls-container`, `chat-container`, `chat-pane`, `messages-container`). A later release may need an update.
- **Languages:** English and French.

## Tests

Server half, without Open WebUI:

```bash
python -m pip install "pydantic>=2,<3" starlette httpx pytest
python -m pytest -q tests/test_chat_inspector.py
```

Besides the served script and valves, it runs the shipped token counter on fixed passages and checks it against real tokenizer counts stored in `tests/token_samples.json`.

In a browser, against a **throwaway** Open WebUI with no users yet, wired to the fake model:

```bash
python tests/fake_model.py 9999 &
OPENAI_API_BASE_URL=http://127.0.0.1:9999/v1 OPENAI_API_KEY=test ENABLE_OPENAI_API=true open-webui serve
python tests/seed.py http://127.0.0.1:8080 chat_inspector.py ../chat-artifacts/chat_artifacts.py ../chat-files/chat_files.py ../chat-gallery/chat_gallery.py > seed.json
node tests/e2e.mjs seed.json
```

`fake_model.py` is an OpenAI-compatible server that streams answers and reports usage, adding 300 tokens of its own to every prompt the way a server's tools and instructions would. Given tools, a message containing `TOOLS` makes it call each offered tool named in the message, natively, the way OpenAI streams tool calls. Results on 2026-11-12:

| Open WebUI | With Artifacts, Files and Gallery | Checks |
| --- | --- | --- |
| v0.13.3 | yes | 103 of 103 passed |
| v0.13.3 | no | 98 of 98 passed |
| v0.13.0 | yes | 103 of 103 passed |

They cover: **Tools and activity** on a chat with every kind of call (an MCP tool named by its server, a workspace tool called natively, through the prompt and in the old text format, built-ins by group, a failed and a declined call, an automatic memory recall, a tool server that couldn't connect, web searches, pages, a source, code and an image), the calls of the last answer in order and opened, the context fitted to the last round's count, filters and actions, and **a real native tool call through Open WebUI** to a workspace tool and the built-in clock, counted once each; Context in the chat's ⋯ menu right after Overview, styled like its items, for a plain user with no + menu; the pop-up opening on screen, clear of the message box and without covering the chat; typing and sending with it open, and it following the answer; Escape and × closing it with focus back on the ⋯ button; it following a change of chat; the platform's name, EveGPT, in its wording and the `platform_name` override; the phone sheet and closing it by tapping outside; `open_as = tab`; turning the menu entry off; opening from the + menu when it is turned on; the breakdown summing to the model's count; the grid; the last answer and totals; no polling; an estimated chat with French, Arabic, Chinese and an image; the administrator's window; nearly full and over the window; two models and the picker; a summarised chat; a model that no longer exists; an empty chat; **a real answer streamed through Open WebUI**, with Context saying it is being written and then updating by itself, and the fake model's 300 extra tokens showing up as the worked-out share; taking turns with the other three tabs; a plain user with no + menu and the optional button; contrast in four themes, the tools list included; a phone, without sideways scrolling; and every valve.

Not tested in a browser: a real provider other than the fake model, Ollama's own usage fields, split screen, and Chat Tabs.
