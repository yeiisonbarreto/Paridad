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
