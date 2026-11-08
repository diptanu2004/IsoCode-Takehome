const MINUS = "−";

const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const wholeRupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

function signed(text: string, negative: boolean): string {
  return negative ? MINUS + text : text;
}

/** ₹1,40,900.00 — Indian digit grouping, true minus sign. Amounts are integer paise. */
export function formatRupees(paise: number): string {
  return signed(rupees.format(Math.abs(paise) / 100), paise < 0);
}

/** A difference: "+₹12.00", "−₹30.09", or "₹0.00". */
export function formatSigned(paise: number): string {
  return paise > 0 ? `+${formatRupees(paise)}` : formatRupees(paise);
}

/** ₹64.8 L, ₹4.32 Cr, or whole rupees below one lakh. For KPIs, not tables. */
export function formatCompact(paise: number): string {
  const value = Math.abs(paise) / 100;
  let text: string;
  if (value >= 1e7) text = `₹${trim((value / 1e7).toFixed(2))} Cr`;
  else if (value >= 1e5) text = `₹${trim((value / 1e5).toFixed(1))} L`;
  else text = wholeRupees.format(value);
  return signed(text, paise < 0);
}

function trim(fixed: string): string {
  return fixed.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
}

const count = new Intl.NumberFormat("en-IN");
const dateTime = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** 1,00,000 — Indian digit grouping for counts. */
export function formatCount(value: number): string {
  return count.format(value);
}

/** 3 Nov, 19:42 */
export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso));
}

const calendarDate = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });
const monthYear = new Intl.DateTimeFormat("en-IN", { month: "short", year: "numeric" });

/** A calendar date from the API ("2025-11-03") as "3 Nov 2025", read in local time so it never shifts a day. */
export function formatDate(isoDate: string): string {
  return calendarDate.format(localDate(isoDate));
}

/** "2026-10-01" → "Oct 2026" */
export function formatMonth(isoDate: string): string {
  return monthYear.format(localDate(isoDate));
}

function localDate(isoDate: string): Date {
  const [year, month, day] = isoDate.slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatSeconds(seconds: number): string {
  return seconds < 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds)} s`;
}
