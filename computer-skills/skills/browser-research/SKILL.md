---
name: browser-research
description: Use the Open Terminal browser (Tether's Launch Chrome or Connect to running Chrome) to read websites, follow procedures, collect tables and check links. Use for university sites, eCampus, government portals, research databases and any page that needs a real browser or the user's own sign-in. Read-only by default, cites every URL, never submits or pays.
---

# Browser research

## Two browser modes in Tether

- Launch Chrome: a separate clean profile. Use for public sites and anything that should not touch the user's accounts.
- Connect to running Chrome: the user's own Chrome with their sign-ins (requires Open Terminal 0.16.3+ and remote debugging approved in Chrome). Use only when the page needs their login, such as the university intranet or eCampus.

If browser tools are missing, Tether has browser tools switched off (Settings, Open Terminal, Tools) or the sandbox is on, which disables the browser. Say that instead of falling back to scraping with curl.

## How to work

1. State the target URL and what you are looking for.
2. Navigate, read the page text, and follow at most the links needed. Prefer the site's own search box for "find" tasks.
3. Record evidence: for every fact, keep the page URL and the visible "last updated" date if there is one.
4. For tables, extract to CSV in the scratch folder, then build the deliverable (Excel, Markdown) from it.
5. Answer with the facts first and a "Sources" list of URLs. Paraphrase; quote at most one short sentence per page.

## Always stop and ask before

- clicking submit, send, pay, book, publish, delete, accept or "I agree"
- typing anything into a login, password, card or one-time-code field (the user types those)
- downloading files (say name, source and size) or opening downloaded files
- changing account or site settings

Signed-in sessions are read-only unless the user asked for a specific change.

## Politeness and reliability

- Wait about a second between page loads when crawling, stay on the domain asked for, and respect robots.txt for anything automated.
- Cap crawls (default 150 pages) and say when you hit the cap.
- Pages move. If a page is missing, say so and show where you looked; do not invent the content.
- Cookie banners: choose the most privacy-preserving option (refuse non-essential).

## Useful starting points (Évry and France)

University: www.univ-evry.fr, intranet.univ-evry.fr (staff sign-in), ecampus.paris-saclay.fr (Moodle), www.universite-paris-saclay.fr. Students: www.etudiant.gouv.fr, www.crous-versailles.fr, www.monmaster.gouv.fr, www.campusfrance.org. Admin and law: www.service-public.fr, www.legifrance.gouv.fr, www.data.gouv.fr. Research: hal.science, theses.fr, arxiv.org, anr.fr. Jobs: www.francetravail.fr.

## Screenshots

Take screenshots only to show the user something or to check a layout. They are temp files: save them in the scratch folder and keep only the ones the user asked for.
