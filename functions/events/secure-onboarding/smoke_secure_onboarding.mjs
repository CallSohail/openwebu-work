import fs from "node:fs";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const source = fs.readFileSync("secure_onboarding.py", "utf8");
const match = source.match(/ONBOARDING_HTML = r"""([\s\S]*?)"""\n\n\nFEATURE_DEFINITIONS =/);
assert.ok(match, "embedded onboarding HTML was not found");

const snapshot = {
  schema: 1,
  generated_at: 1,
  generated_at_label: { fr: "01/01/2026", en: "2026-01-01" },
  user: { name: "", role: "", role_label: { fr: "", en: "" } },
  brand: { product: "Test Assistant", organization: "" },
  ui: {
    default_language: "en",
    show_welcome: true,
    show_access_counts: false,
    font: "Arial, Helvetica, sans-serif",
    show_models: true,
    show_chat_basics: true,
    show_prompts: false,
    show_skills: false,
    show_knowledge: false,
    show_knowledge_creation: false,
    show_notes: false,
    show_web_search: false,
    show_file_upload: false,
    show_tools: false,
    show_channels: false,
    show_folders: false,
    show_memory: false,
    show_calendar: false,
    show_automations: false,
    show_image_generation: false,
    show_code_interpreter: false,
    show_voice: false,
    show_multiple_models: false,
    show_safety: true
  },
  links: {},
  selected_model_id: "model-1",
  counts: {},
  available: {
    models: true,
    prompts: false,
    tools: false,
    skills: false,
    knowledge: false,
    channels: false
  },
  access: { workspace: {}, chat: {}, features: {} },
  resources: {
    models: [{
      id: "model-1",
      name: "Example Model",
      description: "A model visible to this test user.",
      tags: [],
      capabilities: ["vision"],
      examples: [],
      actions: [],
      filters: []
    }],
    prompts: [],
    tools: [],
    skills: [],
    knowledge: [],
    channels: [],
    features: []
  }
};

const payload = JSON.stringify(snapshot)
  .replaceAll("<", "\\u003c")
  .replaceAll(">", "\\u003e")
  .replaceAll("&", "\\u0026");
const html = match[1].replace("__SNAPSHOT_JSON__", payload);

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  url: "https://onboarding.invalid/",
  beforeParse(window) {
    window.ResizeObserver = class {
      constructor(callback) {
        this.callback = callback;
      }
      observe() {
        this.callback();
      }
      disconnect() {}
    };
  }
});

await new Promise((resolve) => setTimeout(resolve, 50));

const { document } = dom.window;
assert.equal(document.getElementById("product").textContent, "Test Assistant");
assert.match(document.getElementById("welcome").textContent, /Welcome/);
assert.ok(document.querySelectorAll("#rail button").length >= 3, "navigation did not render");
assert.ok(document.querySelectorAll(".card").length >= 1, "tutorial cards did not render");
assert.ok(document.getElementById("stage").textContent.trim().length > 40, "stage is blank");
assert.equal(document.getElementById("edgePrev").disabled, true);
assert.equal(document.getElementById("edgeNext").disabled, false);

document.querySelector(".card").click();
assert.equal(document.getElementById("backdrop").hidden, false, "detail dialog did not open");
assert.ok(document.getElementById("modalTitle").textContent.length > 0, "dialog title is blank");

dom.window.close();
console.log("DOM_RENDER_OK");
