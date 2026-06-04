import type { ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

/** Elevated surface container used as the base for cards and sections. */
export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border-subtle bg-surface-100/80 shadow-panel backdrop-blur-sm",
        className,
      )}
    >
      {children}
    </div>
  );
}
