import { describe, expect, it } from "vitest";

import { safeFileName } from "./download";

describe("safeFileName", () => {
  it("keeps letters, digits and _ . - and falls back when empty", () => {
    expect(safeFileName("urgent failure/v2", "run")).toBe("urgent_failure_v2");
    expect(safeFileName("Эксперимент №1", "run")).toBe("Эксперимент_1");
    expect(safeFileName("  ", "run")).toBe("run");
    expect(safeFileName(null, "problem")).toBe("problem");
  });
});
