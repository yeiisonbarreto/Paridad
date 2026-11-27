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

const noticeCounter = document.querySelector("#student-notice-count");
const studentNoticeDialog = document.querySelector("#student-notice-dialog");
let newestNotice = Number(noticeCounter?.dataset.newest || 0);
let pendingNoticeReads = 0;
let noticeReadRevision = 0;
let noticeReadQueue = Promise.resolve();

function setUnreadCount(count) {
  if (!noticeCounter) return;
  noticeCounter.querySelector(".notice-count").hidden = count === 0;
  const value = noticeCounter.querySelector("[data-unread-count]");
  if (value.textContent !== String(count)) value.textContent = count;
}

if (studentNoticeDialog?.showModal) {
  const content = studentNoticeDialog.querySelector("[data-notice-content]");
  const readError = studentNoticeDialog.querySelector("[data-notice-read-error]");
  dismissOnBackdrop(studentNoticeDialog);

  document.addEventListener("submit", event => {
    const form = event.target;
    if (!form.matches("[data-notice-open]")) return;
    event.preventDefault();
    if (studentNoticeDialog.open) return;

    const noticeId = form.elements.notice_id.value;
    content.replaceChildren(form.querySelector("template").content.cloneNode(true));
    readError.hidden = true;
    studentNoticeDialog.dataset.openNotice = noticeId;
    studentNoticeDialog.showModal();
    content.querySelector("h2").focus();
    if (!form.hasAttribute("data-unread") || form.hasAttribute("aria-busy")) return;

    const body = new FormData(form);
    form.setAttribute("aria-busy", "true");
    pendingNoticeReads++;
    noticeReadRevision++;
    noticeReadQueue = noticeReadQueue.then(async () => {
      try {
        const result = await noticeRequest(form.getAttribute("action"), body);
        form.removeAttribute("data-unread");
        form.closest(".notice-entry").classList.remove("notice-unread");
        form.querySelector("[data-notice-new]")?.remove();
        setUnreadCount(result.unread);
      } catch (error) {
        if (studentNoticeDialog.dataset.openNotice === noticeId) {
          readError.textContent = error.message || "No se pudo registrar la lectura. Vuelve a abrir el aviso.";
          readError.hidden = false;
        }
      } finally {
        pendingNoticeReads--;
        form.removeAttribute("aria-busy");
      }
    });
  });
}

let polling = false;
window.setInterval(async () => {
  if (polling || document.hidden || pendingNoticeReads) return;
  polling = true;
  const revision = noticeReadRevision;
  try {
    const response = await fetch(window.location.href, { cache: "no-store" });
    if (response.redirected) {
      window.location.assign(response.url);
      return;
    }
    if (!response.ok) return;
    const next = new DOMParser().parseFromString(await response.text(), "text/html");
    if (revision !== noticeReadRevision) return;

    const updatedCounter = next.querySelector("#student-notice-count");
    if (updatedCounter) {
      const count = Number(updatedCounter.querySelector("[data-unread-count]").textContent);
      const latest = Number(updatedCounter.dataset.newest);
      if (count > 0 && latest > newestNotice) playNoticeSound("receive");
      newestNotice = Math.max(newestNotice, latest);
      setUnreadCount(count);
    }
    for (const selector of [".history", "#notice-inbox"]) {
      if (selector === "#notice-inbox" && studentNoticeDialog?.open) continue;
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
