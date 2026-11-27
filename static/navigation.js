"use strict";

const dropdowns = document.querySelectorAll("[data-dropdown]");

for (const menu of dropdowns) {
  menu.addEventListener("toggle", () => {
    if (!menu.open) return;
    for (const other of dropdowns) {
      if (other !== menu) other.open = false;
    }
  });
}

if (typeof HTMLElement.prototype.showPopover === "function") {
  for (const menu of document.querySelectorAll(".file-menu")) {
    const trigger = menu.querySelector("summary");
    const panel = menu.querySelector(".file-actions");
    panel.setAttribute("popover", "manual");

    const updateMenu = () => {
      if (!menu.open) {
        if (panel.matches(":popover-open")) panel.hidePopover();
        return;
      }
      panel.showPopover();
      const rect = trigger.getBoundingClientRect();
      const { width, height } = panel.getBoundingClientRect();
      const edge = 8;
      const below = rect.bottom + edge;
      const above = rect.top - height - edge;
      const top = below + height <= window.innerHeight - edge ? below : Math.max(edge, above);
      const left = Math.min(window.innerWidth - width - edge, Math.max(edge, rect.right - width));
      panel.style.top = `${top}px`;
      panel.style.left = `${Math.max(edge, left)}px`;
    };

    menu.addEventListener("toggle", updateMenu);
    window.addEventListener("resize", () => { menu.open = false; });
    document.addEventListener("scroll", event => {
      if (!panel.contains(event.target)) menu.open = false;
    }, true);
  }
}

function closeOutsideDropdown(target) {
  for (const menu of dropdowns) {
    if (menu.open && !menu.contains(target)) menu.open = false;
  }
}

document.addEventListener("click", event => closeOutsideDropdown(event.target));
document.addEventListener("focusin", event => closeOutsideDropdown(event.target));

document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  for (const menu of dropdowns) {
    if (menu.open && menu.contains(event.target)) {
      event.preventDefault();
      menu.open = false;
      menu.querySelector("summary").focus();
      break;
    }
  }
});

for (const form of document.querySelectorAll("[data-bulk-selection]")) {
  const page = form.querySelector("[data-bulk-page]");
  const all = form.querySelector("[data-bulk-all]");
  const items = Array.from(form.elements).filter(item => item.hasAttribute("data-bulk-item"));
  const actions = form.querySelectorAll('button[type="submit"]');
  form.querySelector("[data-page-selection]").hidden = false;

  function updateSelection() {
    const count = items.filter(item => item.checked).length;
    page.checked = all.checked || count === items.length;
    page.indeterminate = !all.checked && count > 0 && count < items.length;
    page.disabled = all.checked;
    for (const item of items) item.disabled = all.checked;
    for (const action of actions) action.disabled = !all.checked && count === 0;
  }

  page.addEventListener("change", () => {
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
