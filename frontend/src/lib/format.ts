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
