"use strict";

const fileDeletionDialog = document.querySelector("#delete-file-dialog");
if (fileDeletionDialog?.showModal) {
  const description = fileDeletionDialog.querySelector("[data-delete-description]");
  const title = fileDeletionDialog.querySelector("h2");
  const csrf = fileDeletionDialog.querySelector("[name=csrf]").value;
  const confirmationStorageKey = "paridad:archivos:eliminados";
  let deletion;
  let busy = false;

  function showDeletion(action, opener, label, message, token) {
    deletion = { action, opener, token };
    title.textContent = label;
    description.textContent = message;
    fileDeletionDialog.returnValue = "";
    fileDeletionDialog.showModal();
  }

  document.addEventListener("click", event => {
    const link = event.target.closest("[data-delete-file]");
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    // An Actions popover can close as soon as the dialog takes focus.
    event.preventDefault();
    if (busy || fileDeletionDialog.open) return;
    const name = link.dataset.deleteFile;
    showDeletion(link.href, link.closest(".file-menu").querySelector("summary"), "¿Eliminar archivo?",
      `Se eliminará «${name}» junto con sus comparaciones.`);
  });

  document.querySelector("#file-selection")?.addEventListener("submit", async event => {
    if (event.submitter?.value !== "delete") return;
    event.preventDefault();
    if (busy || fileDeletionDialog.open) return;
    busy = true;
    const body = new FormData(event.target);
    body.set("action", "delete");
    try {
      const { token, count } = await noticeRequest(event.target.getAttribute("action"), body);
      showDeletion("/admin/submissions/lote/eliminar", event.submitter,
        count === 1 ? "¿Eliminar archivo?" : "¿Eliminar archivos?",
        count === 1 ? "Se eliminará el archivo seleccionado junto con sus comparaciones." :
          `Se eliminarán los ${count} archivos seleccionados junto con sus comparaciones.`, token);
    } catch (error) {
      showNoticeToast(error.message || "No se pudo preparar la eliminación.", "error");
    } finally {
      busy = false;
    }
  });

  dismissOnBackdrop(fileDeletionDialog);
  fileDeletionDialog.addEventListener("close", async () => {
    const item = deletion;
    deletion = undefined;
    item?.opener?.focus({ preventScroll: true });
    if (!item || fileDeletionDialog.returnValue !== "delete") return;
    busy = true;
    try {
      const body = new FormData();
      body.set("csrf", csrf);
      if (item.token) body.set("token", item.token);
      const { deleted } = await noticeRequest(item.action, body);
      const message = deleted === 1 ? "Archivo eliminado." :
        deleted > 1 ? `${deleted} archivos eliminados.` : "Los archivos seleccionados ya no están disponibles.";
      try {
        sessionStorage.setItem(confirmationStorageKey, message);
      } catch {
        // Storage can be disabled. The refreshed list will show the current files.
      }
      window.location.reload();
    } catch (error) {
      showNoticeToast(error.message || "No se pudo confirmar la eliminación.", "error");
    } finally {
      busy = false;
    }
  });

  try {
    const message = sessionStorage.getItem(confirmationStorageKey);
    if (message) {
      sessionStorage.removeItem(confirmationStorageKey);
      showNoticeToast(message, "success");
    }
  } catch {
    // Some browsers restrict storage in embedded sessions.
  }
}
