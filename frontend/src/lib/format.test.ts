import { describe, expect, it } from "vitest";
import { formatAgo, formatBps, formatBytes, formatCount, formatSpeed, formatTick, fromLocalInput, setClock, toLocalInput } from "./format";

describe("format", () => {
  it("uses decimal network units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1550336)).toBe("1.6 MB");
    expect(formatBytes(5_551_747_072)).toBe("5.6 GB");
    expect(formatBps(1_000_000_000)).toBe("1.0 Gb/s");
    expect(formatBps(345_600)).toBe("346 kb/s");
    expect(formatSpeed(10_000_000_000)).toBe("10Gb/s");
    expect(formatSpeed(null)).toBe("–");
  });
  it("formats counts and ages", () => {
    expect(formatCount(1284223)).toBe("1,284,223");
    expect(formatCount(null)).toBe("–");
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(formatAgo("2026-01-01T11:59:58Z", now)).toBe("2 s ago");
    expect(formatAgo("2026-01-01T09:00:00Z", now)).toBe("3 h ago");
    expect(formatAgo(null, now)).toBe("never");
  });
  it("round-trips datetime-local values", () => {
    const iso = "2026-01-01T10:30:00Z";
    expect(fromLocalInput(toLocalInput(iso))).toBe(iso);
    expect(fromLocalInput("")).toBeUndefined();
  });
});

describe("clock preference", () => {
  it("formats hours in 24 h or 12 h", () => {
    const iso = "2026-09-30T13:05:00Z";
    setClock("24h");
    expect(formatTick(iso, 3600)).not.toMatch(/AM|PM/i);
    setClock("12h");
    expect(formatTick(iso, 3600)).toMatch(/AM|PM/i);
    setClock("24h");
  });
});
