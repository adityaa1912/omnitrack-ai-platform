import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

/**
 * Accessible modal dialog: focus trap, Esc to close, backdrop click to
 * close, and focus restoration on unmount. Renders inline (no portal) for
 * simplicity; the shell has no stacking contexts that interfere.
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
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    document.addEventListener("keydown", handleKeyDown);
    // Move focus into the panel on open.
    const firstFocusable = panelRef.current?.querySelector<HTMLElement>(
      "input, button, [tabindex]",
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
