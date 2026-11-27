"use strict";

let noticeAudio;
let pendingReceiveSound = false;

async function unlockNoticeAudio(event) {
  if (!event.isTrusted || noticeAudio?.state === "running") return;
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) return;
  try {
    noticeAudio ??= new Audio();
    await noticeAudio.resume();
    if (pendingReceiveSound && noticeAudio.state === "running") {
      pendingReceiveSound = false;
      playNoticeSound("receive");
    }
  } catch {
    // The browser or device can keep audio muted.
  }
}

document.addEventListener("click", unlockNoticeAudio);
document.addEventListener("keydown", unlockNoticeAudio);

function playNoticeSound(kind) {
  if (noticeAudio?.state !== "running") {
    if (kind === "receive") pendingReceiveSound = true;
    return;
  }
  try {
    const melody = kind === "send" ? [494, 659] : [587, 784];
    const start = noticeAudio.currentTime;
    for (const [index, frequency] of melody.entries()) {
      const tone = noticeAudio.createOscillator();
      const volume = noticeAudio.createGain();
      const onset = start + 0.09 * index;
      tone.type = "sine";
      tone.frequency.setValueAtTime(frequency, onset);
      volume.gain.setValueAtTime(0, onset);
      volume.gain.linearRampToValueAtTime(0.06, onset + 0.022);
      volume.gain.linearRampToValueAtTime(0, onset + 0.15);
      tone.connect(volume);
      volume.connect(noticeAudio.destination);
      tone.addEventListener("ended", () => {
        tone.disconnect();
        volume.disconnect();
      });
      tone.start(onset);
      tone.stop(onset + 0.15);
    }
  } catch {
    // Audio feedback is optional when the device is unavailable.
  }
}

function dismissOnBackdrop(dialog) {
  dialog.addEventListener("click", event => {
    if (event.target === dialog) dialog.close();
  });
}

async function noticeRequest(url, body) {
  const response = await fetch(url, { method: "POST", headers: { Accept: "application/json" }, body });
  const result = await response.json().catch(() => null);
  if (!response.ok || !result) {
    throw new Error(typeof result?.detail === "string" ? result.detail : "");
  }
  return result;
}

const sendNoticeDialog = document.querySelector("#send-notice-dialog");
if (sendNoticeDialog?.showModal) {
  const toast = document.querySelector("#notice-toast");
  let confirmation;
  let sendingNotice = false;
  let toastTimeout;

  function showNoticeToast(message, kind) {
    window.clearTimeout(toastTimeout);
    toast.dataset.kind = kind;
    toast.textContent = message;
    toastTimeout = window.setTimeout(() => { toast.textContent = ""; }, 6500);
  }

  document.addEventListener("submit", event => {
    const form = event.target;
    if (!form.matches("[data-notice-send]") || event.submitter?.hasAttribute("formaction")) return;
    event.preventDefault();
    if (sendingNotice || sendNoticeDialog.open) return;
    const body = new FormData(form);
    body.set("preview", "false");
    const opener = form.closest("details")?.querySelector("summary") || event.submitter;
    confirmation = { url: form.getAttribute("action"), body, opener };
    sendNoticeDialog.returnValue = "";
    sendNoticeDialog.showModal();
  });

  dismissOnBackdrop(sendNoticeDialog);
  sendNoticeDialog.addEventListener("close", async () => {
    const request = confirmation;
    confirmation = undefined;
    request?.opener?.focus({ preventScroll: true });
    if (!request || sendNoticeDialog.returnValue !== "send") return;
    sendingNotice = true;
    try {
      const result = await noticeRequest(request.url, request.body);
      if (!result.published) {
        showNoticeToast("No se enviaron avisos. Hay un aviso reciente o cuentas inactivas.", "neutral");
        return;
      }
      const sent = result.published === 1 ? "Aviso enviado." : `${result.published} avisos enviados.`;
      const skipped = result.skipped === 1 ? " Se omitió una cuenta." :
        result.skipped > 1 ? ` Se omitieron ${result.skipped} cuentas.` : "";
      showNoticeToast(sent + skipped, "success");
      playNoticeSound("send");
    } catch (error) {
      showNoticeToast(error.message || "No se pudo confirmar el envío. Vuelve a intentarlo.", "error");
    } finally {
      sendingNotice = false;
    }
  });
}

const clarificationCounter = document.querySelector("#admin-clarification-count");
if (clarificationCounter) {
  const value = clarificationCounter.querySelector("[data-clarification-count]");
  const requests = document.querySelector("#clarification-requests");
  let pollingClarifications = false;

  window.setInterval(async () => {
    if (pollingClarifications || document.hidden) return;
    pollingClarifications = true;
    try {
      const response = await fetch(requests ? window.location.href : "/api/admin/aclaraciones/pendientes",
        { cache: "no-store" });
      if (!response.ok || response.redirected) return;
      let pending;
      if (requests) {
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        pending = Number(page.querySelector("[data-clarification-count]")?.textContent);
        const updated = page.querySelector("#clarification-requests");
        if (updated && !requests.contains(document.activeElement) && requests.innerHTML !== updated.innerHTML) {
          requests.innerHTML = updated.innerHTML;
        }
      } else {
        pending = (await response.json()).pending;
      }
      if (!Number.isInteger(pending)) return;
      if (pending > Number(value.textContent)) playNoticeSound("receive");
      if (value.textContent !== String(pending)) value.textContent = pending;
      clarificationCounter.hidden = pending === 0;
    } catch {
      // Retry the refresh on the next interval.
    } finally {
      pollingClarifications = false;
    }
  }, 8000);
}
