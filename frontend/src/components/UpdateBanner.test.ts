import { describe, expect, it } from "vitest";
import { isNewer } from "./UpdateBanner";

describe("update banner", () => {
  it("detects a different build only", () => {
    expect(isNewer({ build: "abc" }, "abc")).toBe(false);
    expect(isNewer({ build: "def" }, "abc")).toBe(true);
    expect(isNewer(null, "abc")).toBe(false);
    expect(isNewer("<!doctype html>", "abc")).toBe(false);
    expect(isNewer({ build: "" }, "abc")).toBe(false);
  });
});
