import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4">
      <span className="font-mono text-5xl font-bold text-surface-400">404</span>
      <p className="text-sm text-content-secondary">This route does not exist.</p>
      <Link
        to="/"
        className="rounded-lg border border-border bg-surface-100 px-4 py-2 text-sm text-content-primary transition-colors hover:bg-surface-200"
      >
        Back to Overview
      </Link>
    </div>
  );
}
