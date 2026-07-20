/** Toast stack + the guardrail force-confirm dialog. */

import { confirmRequest, toasts } from "../state";
import { S } from "../strings";

export function Toasts() {
  if (!toasts.value.length) return null;
  return (
    <div class="toast-stack" role="status" aria-live="polite">
      {toasts.value.map((t) => (
        <div class={`toast toast-${t.kind}`} key={t.id}>{t.text}</div>
      ))}
    </div>
  );
}

export function ConfirmDialog() {
  const req = confirmRequest.value;
  if (!req) return null;
  const close = () => (confirmRequest.value = null);
  return (
    <div class="modal-backdrop" onClick={close}>
      <div class="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <p>{req.text}</p>
        <div class="modal-actions">
          <button class="btn" onClick={close}>{S.cancel}</button>
          <button
            class="btn btn-danger"
            onClick={() => {
              close();
              req.onConfirm();
            }}
          >
            {S.confirm}
          </button>
        </div>
      </div>
    </div>
  );
}
