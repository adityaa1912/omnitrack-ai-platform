import { forwardRef, useId, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils/cn";

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  error?: string;
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(
  ({ label, hint, error, className, id, ...props }, ref) => {
    const generatedId = useId();
    const fieldId = id ?? generatedId;
    return (
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor={fieldId}
          className="text-xs font-medium text-content-secondary"
        >
          {label}
        </label>
        <input
          ref={ref}
          id={fieldId}
          aria-invalid={error ? true : undefined}
          className={cn(
            "h-9 rounded-lg border bg-surface-200 px-3 text-sm text-content-primary",
            "placeholder:text-content-muted focus-visible:outline-none focus-visible:ring-2",
            error
              ? "border-status-error/50 focus-visible:ring-status-error/50"
              : "border-border focus-visible:ring-accent/50",
            className,
          )}
          {...props}
        />
        {error ? (
          <span className="text-xs text-status-error">{error}</span>
        ) : hint ? (
          <span className="text-xs text-content-muted">{hint}</span>
        ) : null}
      </div>
    );
  },
);
TextField.displayName = "TextField";
