import { describe, expect, it } from "vitest";
import { activeFilters, fromSearchParams, isLive, timeOnly, toQuery, withFilter } from "./filters";

describe("filters", () => {
  it("reads known keys from the URL and defaults to 1h", () => {
    const f = fromSearchParams(new URLSearchParams("src_ip=10.0.0.0/8&foo=bar&vlan="));
    expect(f).toEqual({ src_ip: "10.0.0.0/8", range: "1h" });
  });
  it("serializes in a stable order", () => {
    expect(toQuery({ vlan: "10", range: "24h", src_ip: "10.1.1.1", limit: "5" })).toBe("range=24h&src_ip=10.1.1.1&vlan=10&limit=5");
  });
  it("switches between relative and absolute windows", () => {
    const abs = withFilter({ range: "1h", vlan: "10" }, "from", "2026-01-01T10:00:00Z");
    expect(abs).toEqual({ vlan: "10", from: "2026-01-01T10:00:00Z" });
    expect(isLive(abs)).toBe(false);
    const rel = withFilter({ ...abs, to: "2026-01-01T11:00:00Z" }, "range", "6h");
    expect(rel).toEqual({ vlan: "10", range: "6h" });
    expect(isLive(rel)).toBe(true);
  });
  it("removes empty filters and separates time from the rest", () => {
    expect(withFilter({ vlan: "10", range: "1h" }, "vlan", "")).toEqual({ range: "1h" });
    expect(timeOnly({ range: "1h", vlan: "10" })).toEqual({ range: "1h" });
    expect(activeFilters({ range: "1h", vlan: "10", ip: "10.1.1.1" })).toEqual([["vlan", "10"], ["ip", "10.1.1.1"]]);
  });
});
