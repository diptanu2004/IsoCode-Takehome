export function Logo({ className }: { className?: string }) {
  return (
    <span className={className}>
      <span className="inline-flex items-center gap-2 text-[15px] font-semibold tracking-tight text-ink">
        <svg viewBox="0 0 32 32" className="size-6" aria-hidden>
          <rect width="32" height="32" rx="7" className="fill-accent" />
          <path
            d="M9 17.5l4.5 4.5L23 11"
            stroke="white"
            strokeWidth="3"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Settle
      </span>
    </span>
  );
}
