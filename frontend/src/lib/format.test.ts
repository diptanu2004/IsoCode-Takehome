import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatCompact,
  formatCount,
  formatDate,
  formatMonth,
  formatRupees,
  formatSeconds,
  formatSigned,
} from "./format";

describe("formatRupees", () => {
  it("uses Indian digit grouping", () => {
    expect(formatRupees(14090000)).toBe("₹1,40,900.00");
    expect(formatRupees(86200)).toBe("₹862.00");
  });

  it("shows losses with a true minus sign", () => {
    expect(formatRupees(-140900)).toBe("−₹1,409.00");
    expect(formatRupees(-3009)).toBe("−₹30.09");
  });

  it("formats zero without a sign", () => {
    expect(formatRupees(0)).toBe("₹0.00");
  });
});

describe("formatCompact", () => {
  it("uses lakh and crore", () => {
    expect(formatCompact(648000000)).toBe("₹64.8 L");
    expect(formatCompact(3000000000)).toBe("₹3 Cr");
    expect(formatCompact(4320000000)).toBe("₹4.32 Cr");
    expect(formatCompact(10000000)).toBe("₹1 L");
  });

  it("shows whole rupees below one lakh", () => {
    expect(formatCompact(4512300)).toBe("₹45,123");
  });

  it("keeps the sign", () => {
    expect(formatCompact(-648000000)).toBe("−₹64.8 L");
  });
});

describe("counts and sizes", () => {
  it("groups counts the Indian way", () => {
    expect(formatCount(100000)).toBe("1,00,000");
  });
  it("formats file sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(15 * 1024 * 1024)).toBe("15.0 MB");
  });
  it("formats durations", () => {
    expect(formatSeconds(1.84)).toBe("1.8 s");
    expect(formatSeconds(34.4)).toBe("34 s");
  });
});

describe("formatSigned", () => {
  it("marks money in our favour with a plus", () => {
    expect(formatSigned(1200)).toBe("+₹12.00");
    expect(formatSigned(-3009)).toBe("−₹30.09");
    expect(formatSigned(0)).toBe("₹0.00");
  });
});

describe("formatDate", () => {
  it("reads API dates as calendar dates, never shifting a day", () => {
    expect(formatDate("2025-11-03")).toBe("3 Nov 2025");
    expect(formatDate("2026-01-01T00:00:00Z")).toBe("1 Jan 2026");
    expect(formatMonth("2026-10-01")).toBe("Oct 2026");
  });
});
