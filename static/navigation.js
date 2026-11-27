"use strict";

function openDropdowns() {
  return document.querySelectorAll("[data-dropdown][open]");
}

function positionActions(menu) {
  const panel = menu.querySelector(".file-actions[popover]");
  if (!panel) return;
  if (!menu.open) {
    if (panel.matches(":popover-open")) panel.hidePopover();
    return;
  }
  panel.showPopover();
  const rect = menu.querySelector("summary").getBoundingClientRect();
  const { width, height } = panel.getBoundingClientRect();
  const edge = 8;
  const below = rect.bottom + edge;
  const above = rect.top - height - edge;
  const top = below + height <= window.innerHeight - edge ? below : Math.max(edge, above);
  const left = Math.min(window.innerWidth - width - edge, Math.max(edge, rect.right - width));
  panel.style.top = `${top}px`;
  panel.style.left = `${Math.max(edge, left)}px`;
}

document.addEventListener("toggle", event => {
  const menu = event.target;
  if (!menu.matches("[data-dropdown]")) return;
  if (menu.open) for (const other of openDropdowns()) {
    if (other !== menu) other.open = false;
  }
  positionActions(menu);
}, true);

function closeOutsideDropdown(target) {
  for (const menu of openDropdowns()) {
    if (!menu.contains(target)) menu.open = false;
  }
}

document.addEventListener("click", event => closeOutsideDropdown(event.target));
document.addEventListener("focusin", event => closeOutsideDropdown(event.target));
window.addEventListener("resize", () => {
  for (const menu of openDropdowns()) if (menu.matches(".file-menu")) menu.open = false;
});
document.addEventListener("scroll", event => {
  for (const menu of openDropdowns()) {
    const panel = menu.querySelector(".file-actions[popover]");
    if (panel && !panel.contains(event.target)) menu.open = false;
  }
}, true);

document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  for (const menu of openDropdowns()) {
    if (!menu.contains(event.target)) continue;
    event.preventDefault();
    menu.open = false;
    menu.querySelector("summary").focus();
    break;
  }
});

function initializeListControls(root) {
  if (typeof HTMLElement.prototype.showPopover === "function") {
    for (const panel of root.querySelectorAll(".file-menu .file-actions")) {
      panel.setAttribute("popover", "manual");
    }
  }
  for (const form of root.querySelectorAll("[data-bulk-selection]")) {
    const page = form.querySelector("[data-bulk-page]");
    const all = form.querySelector("[data-bulk-all]");
    const items = Array.from(form.elements).filter(item => item.hasAttribute("data-bulk-item"));
    const actions = form.querySelectorAll('button[type="submit"]');
    if (page) form.querySelector("[data-page-selection]").hidden = false;

    function updateSelection() {
      const count = items.filter(item => item.checked).length;
      if (page) {
        page.checked = all.checked || count === items.length;
        page.indeterminate = !all.checked && count > 0 && count < items.length;
        page.disabled = all.checked;
      }
      for (const item of items) item.disabled = all.checked;
      for (const action of actions) action.disabled = !all.checked && count === 0;
    }

    page?.addEventListener("change", () => {
      for (const item of items) item.checked = page.checked;
      updateSelection();
    });
    all.addEventListener("change", () => {
      if (all.checked) for (const item of items) item.checked = true;
      updateSelection();
    });
    for (const item of items) item.addEventListener("change", updateSelection);
    updateSelection();
  }
}
initializeListControls(document);

const filterForm = document.querySelector("[data-auto-filters]");
const resultsRegion = document.querySelector("[data-filter-results]");
if (filterForm && resultsRegion) {
  const status = filterForm.querySelector("[data-filter-status]");
  const search = filterForm.elements.q;
  const reportTabs = document.querySelector("[data-report-tabs]");
  let pending;
  let delay;

  async function applyFilters() {
    const request = new AbortController();
    pending = request;
    const url = new URL(filterForm.action);
    for (const [name, value] of new FormData(filterForm)) {
      if (value.trim()) url.searchParams.set(name, value.trim());
    }
    try {
      const response = await fetch(url, { signal: request.signal });
      if (pending !== request) return;
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      if (!response.ok) throw new Error();
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const replacement = page.querySelector("[data-filter-results]");
      if (!replacement) throw new Error();
      if (pending !== request) return;
      resultsRegion.replaceChildren(...replacement.childNodes);
      initializeListControls(resultsRegion);
      if (reportTabs) {
        for (const link of reportTabs.querySelectorAll("[data-report-tab]")) {
          const updated = page.querySelector(`[data-report-tab="${link.dataset.reportTab}"]`);
          link.href = updated.getAttribute("href");
          if (updated.hasAttribute("aria-current")) link.setAttribute("aria-current", "page");
          else link.removeAttribute("aria-current");
        }
      }
      history.replaceState(null, "", url);
      resultsRegion.inert = false;
      status.textContent = "";
    } catch {
      if (!request.signal.aborted) status.textContent = "No se pudieron actualizar los resultados. Pulsa Enter para reintentar.";
    } finally {
      if (pending === request) resultsRegion.removeAttribute("aria-busy");
    }
  }

  function scheduleFilters(wait) {
    pending?.abort();
    pending = undefined;
    window.clearTimeout(delay);
    resultsRegion.inert = true;
    resultsRegion.setAttribute("aria-busy", "true");
    status.textContent = "";
    if (wait) delay = window.setTimeout(applyFilters, wait);
    else applyFilters();
  }

  reportTabs?.addEventListener("click", event => {
    const link = event.target.closest("[data-report-tab]");
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    filterForm.elements.vista.value = link.dataset.reportTab;
    scheduleFilters(0);
  });

  filterForm.addEventListener("submit", event => {
    event.preventDefault();
    scheduleFilters(0);
  });
  filterForm.addEventListener("change", event => {
    if (event.target !== search) scheduleFilters(0);
  });
  search.addEventListener("input", event => {
    if (!event.isComposing) scheduleFilters(300);
  });
  search.addEventListener("compositionend", () => scheduleFilters(300));
}
