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
