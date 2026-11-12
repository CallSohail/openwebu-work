# How Chat Inspector works

Chat Inspector is built like [Chat Gallery](../chat-gallery/ARCHITECTURE.md): one Python file whose server half only serves a script through the Shared Assets Protocol, and whose browser half is the whole interface. The tab placement is the code Canvas, Files and Gallery share. This page covers the counting.

## 1. Reading a chat

`load` reads the chat, the model list (cached for five minutes) and the person's settings in parallel, then the chat's folder if it has one. `snapshotOf` turns them into what is drawn:

1. `branchOf` follows `history.currentId` back to the first message: the branch the person is looking at.
2. The model is the one picked, else the last answer's, else the chat's first.
3. If Open WebUI compacted the chat, the latest message carrying `contextSummary` starts the active part, and the summary text stands in for what came before.

## 2. Counting

`countTokens` makes one pass over the text, counting 17 character classes (ASCII words, extra letters in long words, digits, punctuation, line breaks, runs of spaces, accented Latin, Vietnamese, CJK, Hangul, Greek, Cyrillic, Hebrew, Arabic, Devanagari, Thai, emoji), and multiplies them by the `TUNING` row of the model's family. Data URIs are dropped first. Each message adds 4 tokens of framing, as Open WebUI's own estimate does.

The rows were fitted by `tests/fit_tokens.py`: a least-squares fit on relative error over 1,109 passages, each family pulled towards the shared fit so no class is learnt from one sample, then scored on 1,109 other passages.

## 3. Fitting to the model's count

The newest answer on the active branch whose usage has an input count anchors everything:

```text
reported   = the prompt the model read for that answer's last round
estimated  = system prompt + summary + messages before that answer + that answer's own tool calls and results
scale      = reported / estimated   if the estimate is higher, else 1
worked out = reported − estimated   (never below 0)
in use     = reported + that answer's visible text + messages since, all scaled
```

With tool calls an answer takes several rounds, and Open WebUI adds each round's `input_tokens` together while keeping the provider's `prompt_tokens` from the last one. The last round's count is the context; the total is what was read, and is shown as such. An answer with several rounds and only a total can't anchor, and the next older answer does.

So the parts always add up to the model's count, and the gap the text can't explain (tools, retrieved documents, memories, Open WebUI's instructions) is shown, not guessed. Without such an answer every part is estimated and nothing is worked out.

In a chat with several models, the anchor is the chosen model's answer: the branch's own, or its sibling under the same question.

## 4. The window and the grid

`windowOf` takes the first of: an `context_windows` rule (glob patterns on the model's id, its base model and its name), `num_ctx` in the model's or the chat's parameters, the person's `num_ctx` for Ollama models, and the provider's `context_length` or `top_provider.context_length`.

`grid` gives each part as many of the 100 squares as its share, by largest remainder, so the used squares equal the percentage shown. A part too small for a square borrows one from the largest. Squares beyond Open WebUI's summary threshold are hatched.

## 5. Tools and activity

`activityOf` reads one answer:

- `output` items: each `function_call` is paired with its `function_call_output` by `call_id`. Status comes from both, and a result that is only `{"error": …}` counts as failed. `hidden: true` marks calls Open WebUI made by itself (`recall_memories`, `list_available_skills`, `list_attached_knowledge`, a preloaded `view_skill`). A run of calls between two other items is one round. `reasoning`, `open_webui:code_interpreter` and provider items (`web_search_call`, `file_search_call`, `computer_call`) are counted too.
- `sources` entries with `tool_result: true`, which is where a call made without native function calling leaves its result, named `tool_id/function`.
- `<details type="tool_calls">` blocks in the text, from older versions.
- `statusHistory`: generated search queries, the pages a web search read, knowledge searches and tool servers that failed to connect.

`classify` tells the kind from the name, since it isn't saved: a function of a tool or tool server the person can see (from `/api/v1/tools/`, including the `<tool id>_<function>` form Open WebUI uses when two share a name), then MCP server prefixes, longest first (`<server id>_<tool>`), then the built-in names, else a plain tool. `tally` adds up the chosen model's answers on the branch. `functionsFor` lists active filters and actions that are global or on the model, from `/api/v1/functions/`, and the pipe behind a function model.

## 6. Staying current without polling

A timer checks, every 1.2 seconds and only while the tab is in front and the page visible, the number of messages on the page and the length of the last one. When they change, the header says an answer is being written; once they have been still for two checks, the chat is read once. Opening the tab, returning to it and changing chat also read it.

## 7. Where to change things

| To change | Look at |
| --- | --- |
| A valve | `Event.Valves` and `_config()` in Python, then `CONFIG.…` in the script |
| A word or a language | `STRINGS`; the test fails if a key is missing in a language |
| Token tuning or families | `TUNING`, `FAMILIES`, `DEFAULT_FAMILY`; refit with `tests/fit_tokens.py` |
| Where the window comes from | `windowOf`, `adminWindow` |
| The parts and how they are fitted | `snapshotOf` |
| The grid | `grid` |
| Tool kinds, built-in groups, statuses | `BUILTIN_TOOLS`, `classify`, `activityOf`, `tally` |
