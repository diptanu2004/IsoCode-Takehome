import { ChevronLeft, ChevronRight } from "lucide-react";
import { formatCount } from "../lib/format";

export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  if (total <= pageSize) return null;
  const last = Math.ceil(total / pageSize);
  const first = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  const button =
    "grid size-8 place-items-center rounded-md border border-line-strong bg-surface text-muted hover:bg-canvas hover:text-ink disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <nav aria-label="Pages" className="mt-3 flex items-center justify-between gap-3 text-sm text-muted">
      <p className="num">
        {formatCount(first)}–{formatCount(end)} of {formatCount(total)}
      </p>
      <div className="flex items-center gap-2">
        <button type="button" className={button} disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <ChevronLeft className="size-4" aria-hidden />
        </button>
        <span className="num px-1">
          {formatCount(page)} / {formatCount(last)}
        </span>
        <button type="button" className={button} disabled={page >= last} onClick={() => onPage(page + 1)} aria-label="Next page">
          <ChevronRight className="size-4" aria-hidden />
        </button>
      </div>
    </nav>
  );
}
