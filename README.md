# Open WebUI Extensions

Twelve extensions for [Open WebUI](https://github.com/open-webui/open-webui), built for a university deployment and written so anyone can install them. Some change how answers look, some add whole new tabs and panels, and one lets people build small features for themselves by describing them.

Every extension is a single Python file you paste into Open WebUI. Each folder has its own README, a changelog, tests, and a diagram of how it works. Read the code before you install anything: Open WebUI runs it on your server.

## What they look like

| [Artifacts](functions/events/chat-artifacts/) | [Mods](functions/events/chat-mods/) | [Generative UI](tools/generative-ui/) |
| --- | --- | --- |
| ![A document artifact open in a panel beside the chat, with its versions and export buttons.](functions/events/chat-artifacts/screenshots/document-light.png) | ![The /mod card above the message box, explaining how to create a mod, with three example requests.](functions/events/chat-mods/screenshots/slash-light.png) | ![An interactive chart card inside an answer, with a legend and controls.](tools/generative-ui/screenshots/chart.png) |
| [Chat Files](functions/events/chat-files/) | [Chat Gallery](functions/events/chat-gallery/) | [Chat Inspector](functions/events/chat-inspector/) |
| ![The Files tab listing a chat's documents with type badges, sizes and dates.](functions/events/chat-files/screenshots/chat-light.png) | ![The Gallery tab showing the images of a chat as a grid.](functions/events/chat-gallery/screenshots/chat-wide.png) | ![The Context pop-up: a grid of 100 squares coloured by what fills the model's context window.](functions/events/chat-inspector/screenshots/popup-light.png) |

## How they fit into Open WebUI

![How the extensions plug into Open WebUI: Filters and Tools in the chat pipeline on the server, Actions under answers, Event functions that react to server events and publish a browser script, and Rich UI embeds in answers.](docs/architecture.svg)

Open WebUI gives plugins four places to run, and this repository uses all of them.

Filters sit in the chat pipeline. They see the request on its way to the model (`inlet`), each chunk as it streams back (`stream`), and the finished answer (`outlet`). Rich Responses, Study Mode and Artifacts mode work this way.

Tools are functions the model decides to call. Generative UI, the Email Composer and the RAGFlow connector are Tools. The first two answer with an HTML embed, which Open WebUI shows in a sandboxed iframe inside the answer.

Actions are buttons under an answer. Quick Actions is one.

Event functions react to things that happen on the server: a sign-up, a login, a settings change. Secure Onboarding uses them to build a welcome chat. The browser features (Chat Inspector, Gallery, Files, Artifacts and Mods) use them differently. They publish a script through [Classic298's Shared Assets Protocol](https://github.com/Classic298/open-webui-plugins/tree/main/shared-assets-protocol), which Open WebUI loads on every page from `/static/loader.js`. That script then works in the browser with the signed-in person's own access, through Open WebUI's normal API. These five add no server routes of their own, and they can sit side by side because they share that one loader.

## Extensions

| Extension | Type | Version | What it does |
| --- | --- | --- | --- |
| [Generative UI](tools/generative-ui/) | Tool + Filter | 1.2.0 | Interactive cards in answers: charts, tables, calculators, checklists, comparisons, timelines, diagrams, maps, live weather. The model writes a JSON spec; the Tool validates it and renders it. |
| [Rich Email Composer](tools/email-composer/) | Tool | 1.0.0 | An editable email card in the chat, with formatting, recipient checks, *Ask for changes*, copy, `.eml` export and a hand-off to the person's own mail app. |
| [RAGFlow Advanced Connector](tools/ragflow/) | Tool | 3.0.0 | Retrieval from RAGFlow: datasets, multi-query search, document search and knowledge graphs, with sources. |
| [Rich Responses](functions/filters/rich-responses/) | Filter | 1.0.1 | Works out what kind of answer is wanted (explain, compare, how-to, ...), asks the model for a matching structure, and adds icons to headings as they stream. |
| [Study Mode](functions/filters/study-mode/) | Filter | 1.1.0 | Guided learning and Socratic tutoring, with interactive quizzes built from hidden data in the answer. |
| [Quick Actions](functions/actions/quick-actions/) | Action | 3.0.1 | A menu under each answer: rewrite, humanize, verify and more, plus actions people and teams define themselves. English and French. |
| [Secure Dynamic Onboarding](functions/events/secure-onboarding/) | Event | 9.3.0 | One pinned welcome chat per person, showing only what they are allowed to use, in English, French, Spanish or Catalan, kept up to date as permissions change. |
| [Chat Inspector](functions/events/chat-inspector/) | Event | 1.2.0 | *Context* in the chat menu: how full the model's context window is, what fills it, and every tool call, fitted to the model's own token count. |
| [Chat Gallery](functions/events/chat-gallery/) | Event | 1.1.0 | A Gallery tab with every image in the chat and in the person's files: zoom, generation details, zip, delete. A maintained fork of @G30's Chat Gallery. |
| [Chat Files](functions/events/chat-files/) | Event | 2.0.0 | A Files tab with every document in the chat and in the person's account, as a list or a grid, with previews, rename, delete and zip. |
| [Artifacts](functions/events/chat-artifacts/) | Event + Filter | 1.4.0 | Documents, slides, spreadsheets, motion graphics, diagrams, pages and apps open in a panel beside the chat, with versions, edits and exports (PowerPoint, Word, PDF, Excel, MP4). Generated code runs in a sandbox. |
| [Mods](functions/events/chat-mods/) | Event | 1.0.0 | Type `/mod` and describe a feature; a model writes it, Mods checks and test-runs the code, and it appears. Each mod runs in a sandbox, limited to the permissions it was given. |

## How each one works

Each diagram is drawn from the code. Blue boxes are this repository, green is Open WebUI itself, purple is outside services, and the dashed red boxes are sandboxed code. Click a name for the full README.

### [Generative UI](tools/generative-ui/)

![Generative UI architecture](tools/generative-ui/diagrams/architecture.svg)

The model never writes HTML. It writes one JSON spec, and the Tool does the rest: it repairs common JSON mistakes, keeps only the fields each component allows, fetches live weather or images when the spec asks, and builds a self-contained card with its own renderer. No CDN is involved, so the cards still work on servers with no internet access. More in [ARCHITECTURE.md](tools/generative-ui/ARCHITECTURE.md).

### [Rich Email Composer](tools/email-composer/)

![Rich Email Composer architecture](tools/email-composer/diagrams/architecture.svg)

The server never sends mail. *Send* opens the draft in the person's own mail app, which keeps credentials out of Open WebUI and stops a draft card from quietly turning into a mailer. Editing, formatting and the `.eml` export all happen in the card.

### [RAGFlow Advanced Connector](tools/ragflow/)

![RAGFlow connector architecture](tools/ragflow/diagrams/architecture.svg)

The RAGFlow API key stays on the server. Each person can narrow the datasets through their own settings, and the connector removes duplicate chunks before the model sees them.

### [Rich Responses](functions/filters/rich-responses/)

![Rich Responses architecture](functions/filters/rich-responses/diagrams/architecture.svg)

Two small jobs. On the way in, it guesses the kind of answer from the question and adds instructions for that shape. On the way out, it adds an icon to each heading as soon as the line is complete, so the answer looks right while it is still streaming.

### [Study Mode](functions/filters/study-mode/)

![Study Mode architecture](functions/filters/study-mode/diagrams/architecture.svg)

Quizzes travel inside the answer as hidden data. The Filter keeps that data out of sight while the answer streams, then turns it into an interactive quiz with shuffled answers and removes the raw data from the saved message.

### [Quick Actions](functions/actions/quick-actions/)

![Quick Actions architecture](functions/actions/quick-actions/diagrams/architecture.svg)

It looks at the answer before offering anything, so a code block gets different actions from a paragraph. The finished prompt lands in the message box for review, or is sent straight away, depending on the person's setting.

### [Secure Dynamic Onboarding](functions/events/secure-onboarding/)

![Secure Dynamic Onboarding architecture](functions/events/secure-onboarding/diagrams/architecture.svg)

The guide is rebuilt from a snapshot of what the person can actually use: their permissions, models, tools, prompts and knowledge. It only rewrites the welcome chat when that snapshot changes, and by default it doesn't bring back a chat someone deleted. A Redis lock stops two workers from creating the same chat twice.

### [Chat Inspector](functions/events/chat-inspector/)

![Chat Inspector architecture](functions/events/chat-inspector/diagrams/architecture.svg)

It only reads. It never polls and adds nothing to the request. Token counts are estimates tuned against five real tokenizers, then fitted to the prompt size the model itself reported, when it reported one.

### [Chat Gallery](functions/events/chat-gallery/) and [Chat Files](functions/events/chat-files/)

![Chat Gallery architecture](functions/events/chat-gallery/diagrams/architecture.svg)

![Chat Files architecture](functions/events/chat-files/diagrams/architecture.svg)

Two tabs built the same way, and designed to sit together: Gallery takes the images, Files takes everything else. Both read through Open WebUI's API as the signed-in person, so nobody sees a file they couldn't open anyway. Zips are built in the browser.

### [Artifacts](functions/events/chat-artifacts/)

![Artifacts architecture](functions/events/chat-artifacts/diagrams/architecture.svg)

Artifacts are found in ordinary answers, so any model works. Pages and apps run in a frame with no site origin, behind a content policy that allows only the libraries the administrator configured. Every export, MP4 video included, is built in the browser. More in [ARCHITECTURE.md](functions/events/chat-artifacts/ARCHITECTURE.md).

### [Mods](functions/events/chat-mods/)

![Mods architecture](functions/events/chat-mods/diagrams/architecture.svg)

The interesting part is Create. A model's first answer is often wrong in small ways, so Mods reads it leniently, checks it, and test-runs it in a throwaway sandbox before anyone sees it. Anything that fails goes back to the model, up to three times. Installed mods run one per sandboxed frame, with no network, and every request they make is checked against what the person allowed. More in [ARCHITECTURE.md](functions/events/chat-mods/ARCHITECTURE.md).

The diagrams are generated by [`docs/diagrams/build.py`](docs/diagrams/build.py) from [`docs/diagrams/specs.py`](docs/diagrams/specs.py). Change a spec and run `python docs/diagrams/build.py` to redraw them.

## Where things are

| Folder | Holds | Installed from |
| --- | --- | --- |
| [`tools/`](tools/) | Tools: functions the model can call | Workspace > Tools |
| [`functions/filters/`](functions/filters/) | Filters: change the request, the stream or the answer | Admin Panel > Functions |
| [`functions/actions/`](functions/actions/) | Actions: buttons under an answer | Admin Panel > Functions |
| [`functions/events/`](functions/events/) | Event functions: react to server events, or add features to the page | Admin Panel > Functions |
| [`docs/`](docs/) | The overview diagram and the script that draws every diagram | |

Each extension folder has the Python file to paste, a README, a CHANGELOG, tests, and `diagrams/architecture.svg`.

## Install

Open the extension's README for its steps. In short: copy its `.py` file into Open WebUI (Tools under **Workspace > Tools**, everything else under **Admin Panel > Functions**), enable it, and set its Valves. Keys and server-specific settings go in Valves, never in the code.

## Compatibility

| Open WebUI | Extensions |
| --- | --- |
| 0.13.0 or newer | Chat Inspector, Chat Gallery, Chat Files, Artifacts, Mods (they use the Tabs panel added in 0.13.0; all tested in a browser on 0.13.0 and 0.13.3) |
| 0.12.0 or newer | Generative UI, Rich Responses |
| 0.11.3 or newer | Secure Dynamic Onboarding |
| 0.11.1 or newer | Study Mode, Quick Actions |
| No stated minimum | Rich Email Composer (needs Rich UI embeds), RAGFlow Advanced Connector |

## Security

Tools and Functions run Python on your Open WebUI server, so treat them as trusted server code and read them first. This repository never holds API keys, passwords, private endpoints, personal or institutional addresses, or user data. See [SECURITY.md](SECURITY.md) for the policy, and each README's security section for what the extension can reach.

## Releases and changes

Each extension is versioned on its own with semantic versioning and tagged `<name>-v<version>` (for example `chat-mods-v1.0.0`). Its `CHANGELOG.md` sits next to the code; [CHANGELOG.md](CHANGELOG.md) at the top covers the whole repository. See [RELEASING.md](RELEASING.md) for the checklist and [CONTRIBUTING.md](CONTRIBUTING.md) for how to add an extension.

## License

Released under the [MIT License](LICENSE).

## Star History

<a href="https://www.star-history.com/?repos=callsohail%2Fopenwebu-work&type=date&logscale=&releases=&legend=bottom-right">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=callsohail/openwebu-work&type=timeline&theme=dark&logscale&legend=bottom-right" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=callsohail/openwebu-work&type=timeline&logscale&legend=bottom-right" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=callsohail/openwebu-work&type=timeline&logscale&legend=bottom-right" />
 </picture>
</a>
