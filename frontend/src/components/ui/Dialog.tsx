import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

/**
 * Accessible modal dialog: focus trap, Esc to close, backdrop click to
 * close, and focus restoration on unmount. Renders inline (no portal) for
 * simplicity; the shell has no stacking contexts that interfere.
 *
 * Focus trap: while open, Tab cycles focus among focusable elements inside
 * the panel; focus cannot escape to the backdrop or page behind. When the
 * dialog closes, focus is restored to the previously focused element.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "Tab") {
        const panel = panelRef.current;
        if (!panel) return;
        const focusable = Array.from(
          panel.querySelectorAll<HTMLElement>(
            "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
          )
        );
        if (focusable.length === 0) {
          e.preventDefault();
          return;
        }
        const current = document.activeElement as HTMLElement | null;
        const index = focusable.indexOf(current as HTMLElement);
        if (e.shiftKey) {
          if (index <= 0) {
            e.preventDefault();
            (focusable[focusable.length - 1] as HTMLElement)?.focus();
          }
        } else {
          if (index === -1 || index >= focusable.length - 1) {
            e.preventDefault();
            (focusable[0] as HTMLElement)?.focus();
          }
        }
      }
    },
    [onClose],
  );

  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    document.addEventListener("keydown", handleKeyDown);
    // Move focus into the panel on open.
    const firstFocusable = panelRef.current?.querySelector<HTMLElement>(
      "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
    );
    firstFocusable?.focus();
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused.current?.focus();
    };
  }, [open, handleKeyDown]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="absolute inset-0 bg-surface-0/70 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        className={cn(
          "relative w-full max-w-md rounded-2xl border border-border bg-surface-100 p-6 shadow-panel",
          "duration-150 animate-in",
        )}
      >
        <h2 className="text-sm font-semibold text-content-primary">{title}</h2>
        {description ? (
          <p className="mt-1 text-xs text-content-muted">{description}</p>
        ) : null}
        <div className="mt-5">{children}</div>
      </div>
    </div>
  );
}
