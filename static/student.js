"use strict";

const activity = document.querySelector("#activity");
const language = document.querySelector("#language");
const source = document.querySelector("#source");
const declared = document.querySelector("#ai-declared");
const credit = document.querySelector("#ai-credit");

if (activity && language && source && declared && credit) {
  function syncLanguages() {
    for (const option of language.options) {
      if (!option.dataset.activities) continue;
      const allowed = option.dataset.activities.split(" ").includes(activity.value);
      option.disabled = !allowed;
      option.hidden = !allowed;
      if (!allowed && option.selected) language.value = "";
    }
    source.accept = language.selectedOptions[0]?.dataset.extensions || "";
  }

  function syncCredit() {
    credit.disabled = !declared.checked;
    credit.required = declared.checked;
    credit.closest("label").hidden = !declared.checked;
  }

  activity.addEventListener("change", syncLanguages);
  language.addEventListener("change", syncLanguages);
  declared.addEventListener("change", syncCredit);
  syncLanguages();
  syncCredit();
}

if (document.querySelector("[data-deterrence]")) {
  document.addEventListener("contextmenu", event => {
    if (event.target.closest("[data-deterrence]")) event.preventDefault();
  });
  document.addEventListener("copy", event => {
    if (event.target.closest?.("[data-deterrence]")) event.preventDefault();
  });
  document.addEventListener("keydown", event => {
    if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const key = event.key.toLowerCase();
    const modifier = event.ctrlKey || event.metaKey;
    if (key === "f12" || (modifier && key === "u") ||
        (modifier && event.shiftKey && ["i", "j", "c"].includes(key))) {
      event.preventDefault();
    }
  });
}

let polling = false;
window.setInterval(async () => {
  if (polling || document.hidden) return;
  polling = true;
  try {
    const response = await fetch(window.location.href, { cache: "no-store" });
    if (response.redirected) {
      window.location.assign(response.url);
      return;
    }
    if (!response.ok) return;
    const next = new DOMParser().parseFromString(await response.text(), "text/html");
    for (const selector of [".history", "#notice-inbox", "#student-notice-count"]) {
      const current = document.querySelector(selector);
      const updated = next.querySelector(selector);
      if (current && updated && current.innerHTML !== updated.innerHTML &&
          !current.contains(document.activeElement)) {
        current.innerHTML = updated.innerHTML;
      }
    }
  } catch {
    // A temporary network failure will be retried on the next poll.
  } finally {
    polling = false;
  }
}, 10000);
