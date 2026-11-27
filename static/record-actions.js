"use strict";

const recordDialog = document.querySelector("#record-action-dialog");
if (recordDialog?.showModal) {
  const content = recordDialog.querySelector("[data-record-content]");
  const error = recordDialog.querySelector("[data-record-error]");
  const loading = recordDialog.querySelector("[data-record-loading]");
  let opener;
  let busy = false;
  let credentialsUrl;

  function closeRecord() {
    if (!busy) recordDialog.close();
  }

  recordDialog.addEventListener("click", event => {
    if (event.target === recordDialog || event.target.closest("[data-record-close]")) {
      event.preventDefault();
      closeRecord();
    }
  });
  recordDialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  recordDialog.addEventListener("close", () => {
    if (credentialsUrl) {
      URL.revokeObjectURL(credentialsUrl);
      credentialsUrl = undefined;
    }
    content.replaceChildren();
    opener?.focus({ preventScroll: true });
  });

  function showRecord(url, origin, body) {
    if (recordDialog.open) return;
    opener = origin.closest(".file-menu")?.querySelector("summary") || origin;
    const menu = opener.closest(".file-menu");
    if (menu) menu.open = false;
    error.hidden = true;
    loading.hidden = false;
    recordDialog.removeAttribute("aria-labelledby");
    recordDialog.showModal();
    loadRecord(url, body);
  }

  async function loadRecord(url, body, form) {
    busy = true;
    error.hidden = true;
    const submit = form?.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      const response = await fetch(url, {
        method: body ? "POST" : "GET",
        body,
        headers: { Accept: "application/json" },
        mode: "same-origin",
      });
      if (!response.ok) {
        const result = await response.json().catch(() => null);
        throw new Error(result?.detail || "No se pudo completar la acción.");
      }
      if (form?.matches("[data-credentials-import]") &&
          response.headers.get("content-type")?.startsWith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")) {
        credentialsUrl = URL.createObjectURL(await response.blob());
        const download = content.querySelector("[data-credentials-download]");
        download.href = credentialsUrl;
        form.hidden = true;
        content.querySelector("[data-credentials-ready]").hidden = false;
        download.focus();
        document.querySelector("[data-auto-filters]")?.requestSubmit();
        return;
      }
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const panel = page.querySelector("[data-record-panel]");
      if (response.redirected && !panel?.matches("[data-record-keep-open]")) {
        window.location.assign(response.url);
        return;
      }
      if (!panel) throw new Error("No se pudo abrir la acción.");
      content.replaceChildren(panel);
      recordDialog.scrollTop = 0;
      initializeListControls(panel);
      const title = content.querySelector("h1");
      title.id = "record-action-title";
      title.tabIndex = -1;
      recordDialog.setAttribute("aria-labelledby", title.id);
      title.focus();
      panel.dispatchEvent(new CustomEvent("paridad:record-opened", { bubbles: true }));
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      if (!form) {
        const dismiss = document.createElement("button");
        dismiss.type = "button";
        dismiss.className = "button secondary";
        dismiss.dataset.recordClose = "";
        dismiss.textContent = "Cerrar";
        content.replaceChildren(dismiss);
        dismiss.focus();
      }
    } finally {
      busy = false;
      loading.hidden = true;
      if (submit) submit.disabled = false;
    }
  }

  document.addEventListener("click", event => {
    const link = event.target.closest("a[data-record-action]");
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (content.contains(link)) {
      if (!busy) loadRecord(link.href);
    } else {
      showRecord(link.href, link);
    }
  });

  document.addEventListener("submit", event => {
    const form = event.target;
    const submitter = event.submitter;
    if (form.matches("form[data-record-action]") ||
        (form.dataset.recordSubmit && submitter?.value === form.dataset.recordSubmit)) {
      event.preventDefault();
      showRecord(form.getAttribute("action"), submitter || form, new FormData(form, submitter));
    } else if (content.contains(form)) {
      event.preventDefault();
      if (!busy) loadRecord(form.getAttribute("action"), new FormData(form), form);
    }
  });
}

document.addEventListener("click", async event => {
  const button = event.target.closest("[data-copy-invitation]");
  if (!button) return;
  const panel = button.closest("[data-record-panel]");
  const input = panel.querySelector("#invitation-link");
  const status = panel.querySelector("[data-copy-status]");
  try {
    await navigator.clipboard.writeText(input.value);
    status.textContent = "Enlace copiado.";
  } catch {
    input.focus();
    input.select();
    status.textContent = document.execCommand("copy") ? "Enlace copiado." : "Enlace seleccionado. Puedes copiarlo.";
  }
});
