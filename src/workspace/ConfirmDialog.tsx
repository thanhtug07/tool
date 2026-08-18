import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * CONFIRM — a compact destructive-confirmation modal (Custom page actions:
 * Reset Current Tool / Clear Project). Consistent with the dark studio theme:
 * a red confirm button, Escape or the backdrop to dismiss.
 */
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div
      data-role="confirm-dialog"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-4 shadow-xl">
        <div className="flex items-start gap-2.5">
          <span className="grid size-7 shrink-0 place-items-center rounded-full bg-destructive/15">
            <AlertTriangle className="size-4 text-red-400" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold">{title}</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{message}</p>
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={onCancel} autoFocus>
            {cancelLabel}
          </Button>
          <Button
            type="button"
            size="sm"
            data-role="confirm-destructive"
            className="bg-destructive text-white hover:bg-destructive/90"
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
