import fs from "node:fs";
import assert from "node:assert/strict";
import { JSDOM, VirtualConsole } from "jsdom";

const source = fs.readFileSync("secure_onboarding.py", "utf8");
const match = source.match(/ONBOARDING_HTML = r"""([\s\S]*?)"""\n\nFEATURE_DEFINITIONS =/);
assert.ok(match, "embedded onboarding HTML was not found");

const featureKeys = [
  "web_search", "image_generation", "code_interpreter", "file_upload",
  "notes", "folders", "channels", "memories", "automations", "calendar",
  "multiple_models", "stt", "tts", "call"
];
const sectionFlags = [
  "show_cover", "show_welcome", "show_composer", "show_plus_menu",
  "show_tool_permissions", "show_integrations", "show_tools", "show_web_search",
  "show_models", "show_suggestions", "show_response_actions", "show_feedback",
  "show_sidebar_navigation", "show_user_menu", "show_notes", "show_folders",
  "show_channels", "show_calendar", "show_automations", "show_builtin_tools",
  "show_admin_section", "show_safety", "show_file_upload", "show_knowledge",
  "show_prompts", "show_memory", "show_image_generation",
  "show_code_interpreter", "show_voice", "show_multiple_models",
  "show_features_tab", "show_language_switch", "show_try_buttons"
];

const snapshot = {
  schema: 2,
  template_revision: 9,
  guide_revision: 1,
  update_notes: { fr: "", en: "" },
  generated_at: 1,
  brand: {
    product: "Test Assistant",
    organization: "Example Organization",
    primary: "#1F4E79",
    secondary: "#0EA5B7"
  },
  progress_scope: "smoke-test",
  ui: {
    ...Object.fromEntries(sectionFlags.map((key) => [key, true])),
    default_language: "en",
    is_admin: true
  },
  links: {
    support_url: "https://example.org/support",
    privacy_url: "",
    acceptable_use_url: "",
    feedback_url: ""
  },
  selected_model_id: "model-1",
  available: {
    models: true,
    prompts: true,
    tools: true,
    knowledge: true
  },
  access: {
    workspace: {
      models: true,
      prompts: true,
      skills: true,
      tools: true,
      knowledge: true
    }
  },
  resources: {
    features: featureKeys.map((key) => ({ key, enabled: true })),
    tools: [
      { name: "Library Search", description: "Search permitted library data" },
      { name: "Course Helper", description: "Work with permitted course data" }
    ],
    toggles: [
      { name: "Study Mode", description: "Guided study support" }
    ],
    actions: [
      { name: "Save Note", description: "Save selected content as a note" }
    ]
  }
};

const payload = JSON.stringify(snapshot)
  .replaceAll("<", "\\u003c")
  .replaceAll(">", "\\u003e")
  .replaceAll("&", "\\u0026");
const html = match[1].replace("__SNAPSHOT_JSON__", payload);

const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => errors.push(error));
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  url: "https://onboarding.invalid/",
  virtualConsole,
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
assert.ok(document.getElementById("g"), "guide shell is missing");
assert.ok(document.getElementById("gBody").textContent.trim().length > 100, "guide body is blank");
assert.equal(document.querySelectorAll(".g-tab").length, 2, "Tour and Features tabs are required");
assert.ok(document.querySelectorAll(".chip").length >= 10, "chapter navigation did not render");
assert.ok(document.querySelector(".cover"), "cover screen did not render");
assert.ok(document.querySelector(".cbtn.main"), "Start tour action is missing");
assert.ok(document.querySelector(".g-count"), "step counter is missing");

document.querySelector(".cbtn.main").click();
assert.ok(document.querySelector(".stage"), "first interactive tour stage is missing");
assert.ok(document.querySelectorAll(".note").length > 0, "interactive explanations are missing");
document.querySelector(".note").click();
assert.ok(document.querySelector(".note.on"), "explanation selection did not update");

const totalMatch = document.querySelector(".g-count").textContent.match(/of (\d+)$/);
assert.ok(totalMatch, "English step count is invalid");
const total = Number(totalMatch[1]);
assert.ok(total >= 20, "full role-aware tour did not render");
for (let index = 1; index < total - 1; index += 1) {
  const next = [...document.querySelectorAll(".g-foot .btn")].at(-1);
  assert.ok(next, `Next button missing at step ${index}`);
  next.click();
  assert.ok(document.getElementById("gBody").textContent.trim().length > 50, `step ${index + 1} is blank`);
}

document.querySelector("[data-tab='lib']").click();
assert.ok(document.querySelectorAll(".tile").length >= 10, "feature library did not render");
document.querySelector(".tile").click();
assert.ok(document.querySelector(".overlay"), "feature detail sheet did not open");
assert.ok(document.querySelector(".sheet"), "feature detail content is missing");
document.querySelector(".sheet-x").click();
assert.equal(document.querySelector(".overlay"), null, "feature detail sheet did not close");

document.querySelector("[data-lang='fr']").click();
assert.equal(document.documentElement.lang, "fr");
assert.equal(document.querySelector("[data-lang='fr']").getAttribute("aria-pressed"), "true");
assert.match(document.querySelector("[data-tab='tour']").textContent, /Visite guidée/);

document.getElementById("close").click();
assert.ok(document.getElementById("g").classList.contains("slim"), "dismissed state did not render");
assert.ok(document.querySelector(".slim-msg"), "dismissed message is missing");

assert.equal(errors.length, 0, errors.map((error) => error.message).join("\n"));
dom.window.close();
console.log("DOM_RENDER_OK");
