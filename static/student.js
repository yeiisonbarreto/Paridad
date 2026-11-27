"use strict";

function initializeStudentModule(panel) {
  const language = panel.querySelector("#language");
  const source = panel.querySelector("#source");
  const declared = panel.querySelector("#ai-declared");
  const credit = panel.querySelector("#ai-credit");

  if (language && source && declared && credit) {
    function syncLanguage() {
      source.accept = language.selectedOptions[0]?.dataset.extensions || "";
    }
    function syncCredit() {
      credit.disabled = !declared.checked;
      credit.required = declared.checked;
      credit.closest("label").hidden = !declared.checked;
    }
    language.addEventListener("change", syncLanguage);
    declared.addEventListener("change", syncCredit);
    syncLanguage();
    syncCredit();
  }

  const selection = panel.matches("[data-server-time]") ? panel : panel.querySelector("[data-server-time]");
  if (!selection) return;
  const serverTime = Number(selection.dataset.serverTime);
  const loadedAt = performance.now();
  const cards = Array.from(selection.querySelectorAll("[data-delivery-id]"));
  const submit = panel.querySelector('#submission-form button[type="submit"]');
  const labels = { open: "Abierta", scheduled: "Programada", closed: "Cerrada", expired: "Plazo vencido" };

  function remaining(seconds) {
    const minutes = Math.ceil(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);
    if (seconds < 60) return "menos de un minuto";
    if (days) return `${days} d ${hours % 24} h`;
    if (hours) return `${hours} h ${minutes % 60} min`;
    return `${minutes} min`;
  }

  function updateWindows() {
    const now = serverTime + (performance.now() - loadedAt) / 1000;
    for (const card of cards) {
      const opens = Number(card.dataset.opens);
      const closes = Number(card.dataset.closes);
      let state = "open";
      if (card.dataset.accepting === "false") state = "closed";
      else if (opens > now) state = "scheduled";
      else if (closes && closes <= now) state = "expired";

      const badge = card.querySelector("[data-window-status]");
      if (badge.textContent !== labels[state]) badge.textContent = labels[state];
      badge.classList.toggle("pending", state === "scheduled");
      badge.classList.toggle("alert", state === "closed" || state === "expired");
      const countdown = card.querySelector("[data-countdown]");
      let message = "";
      if (state === "scheduled") message = `Abre en ${remaining(opens - now)}`;
      if (state === "open") message = closes ? `Tiempo restante · ${remaining(closes - now)}` : "Sin fecha límite";
      if (countdown.textContent !== message) countdown.textContent = message;

      const link = card.querySelector("[data-upload-link]");
      if (link) link.hidden = state !== "open";
      if (submit && state !== "open") submit.disabled = true;
    }
  }

  updateWindows();
  const ticker = window.setInterval(() => {
    if (panel.isConnected) updateWindows();
    else window.clearInterval(ticker);
  }, 1000);
}

for (const panel of document.querySelectorAll("[data-student-module]")) initializeStudentModule(panel);
document.addEventListener("paridad:record-opened", event => {
  if (event.target.matches("[data-student-module]")) initializeStudentModule(event.target);
});

const uploadReceipt = document.querySelector("[data-submission-success]");
if (uploadReceipt) {
  const cleanUrl = new URL(window.location.href);
  cleanUrl.searchParams.delete("entrega");
  history.replaceState(null, "", cleanUrl);
  window.setTimeout(() => uploadReceipt.remove(), 6500);
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
let pendingNoticeActions = 0;
let noticeVersion = 0;
let noticeReadQueue = Promise.resolve();

function setUnreadCount(count) {
  if (!noticeCounter) return;
  noticeCounter.querySelector(".notice-count").hidden = count === 0;
  const value = noticeCounter.querySelector("[data-unread-count]");
  if (value.textContent !== String(count)) value.textContent = count;
}

const clarificationForm = document.querySelector("[data-request-clarification]");
if (clarificationForm) {
  const clarificationPanel = clarificationForm.closest(".clarification-card");
  const errorMessage = clarificationForm.querySelector("[data-clarification-error]");
  const submit = clarificationForm.querySelector('button[type="submit"]');

  clarificationForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    errorMessage.hidden = true;
    pendingNoticeActions++;
    noticeVersion++;
    try {
      const result = await noticeRequest(clarificationForm.getAttribute("action"), new FormData(clarificationForm));
      clarificationPanel.innerHTML = result.content;
      clarificationPanel.querySelector("h2").focus();
      setUnreadCount(result.unread);
      newestNotice = Math.max(newestNotice, result.newest_notice);
      playNoticeSound("receive");
    } catch (error) {
      errorMessage.textContent = error.message || "No se pudo enviar la solicitud. Inténtelo de nuevo.";
      errorMessage.hidden = false;
    } finally {
      pendingNoticeActions--;
      submit.disabled = false;
    }
  });
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
    const needsRefresh = form.hasAttribute("data-unread") || form.hasAttribute("data-refresh-thread");
    if (!needsRefresh || form.hasAttribute("aria-busy")) return;

    const body = new FormData(form);
    form.setAttribute("aria-busy", "true");
    pendingNoticeActions++;
    noticeVersion++;
    noticeReadQueue = noticeReadQueue.then(async () => {
      try {
        const result = await noticeRequest(form.getAttribute("action"), body);
        if (result.content && studentNoticeDialog.open && studentNoticeDialog.dataset.openNotice === noticeId) {
          const restoreFocus = content.contains(document.activeElement);
          if (content.innerHTML.trim() !== result.content.trim()) {
            content.innerHTML = result.content;
            if (restoreFocus) content.querySelector("h2").focus();
          }
          if (result.activity_tick > newestNotice) playNoticeSound("receive");
          newestNotice = Math.max(newestNotice, result.activity_tick);
        }
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
        pendingNoticeActions--;
        form.removeAttribute("aria-busy");
      }
    });
  });
}

let polling = false;
window.setInterval(async () => {
  if (polling || document.hidden || pendingNoticeActions) return;
  polling = true;
  const revision = noticeVersion;
  try {
    const response = await fetch(window.location.href, { cache: "no-store" });
    if (response.redirected) {
      window.location.assign(response.url);
      return;
    }
    if (!response.ok) return;
    const next = new DOMParser().parseFromString(await response.text(), "text/html");
    if (revision !== noticeVersion) return;

    const updatedCounter = next.querySelector("#student-notice-count");
    if (updatedCounter) {
      const count = Number(updatedCounter.querySelector("[data-unread-count]").textContent);
      const latest = Number(updatedCounter.dataset.newest);
      if (count > 0 && latest > newestNotice) playNoticeSound("receive");
      newestNotice = Math.max(newestNotice, latest);
      setUnreadCount(count);
    }
    for (const selector of [".history", "#notice-inbox", ".clarification-card"]) {
      if (selector === "#notice-inbox" && studentNoticeDialog?.open) continue;
      const current = document.querySelector(selector);
      const updated = next.querySelector(selector);
      if (!current || !updated || current.innerHTML === updated.innerHTML ||
          current.querySelector("[data-request-clarification]")) continue;
      const active = document.activeElement;
      const focusedNotice = selector === "#notice-inbox" && active.closest("[data-notice-open]");
      if (current.contains(active) && !focusedNotice) continue;
      const focusedId = focusedNotice ? focusedNotice.elements.notice_id.value : null;
      current.innerHTML = updated.innerHTML;
      if (focusedId) {
        const matchingNotice = Array.from(current.querySelectorAll("[data-notice-open]"))
          .find(form => form.elements.notice_id.value === focusedId);
        matchingNotice?.querySelector("button").focus({ preventScroll: true });
      }
    }
  } catch {
    // A temporary network failure will be retried on the next poll.
  } finally {
    polling = false;
  }
}, 10000);
