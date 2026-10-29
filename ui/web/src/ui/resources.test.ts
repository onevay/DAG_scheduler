import { describe, expect, it } from "vitest";

import {
  amount,
  clusterResources,
  fits,
  formatResources,
  mainResource,
  sortResources,
  work,
} from "./resources";

describe("resources", () => {
  it("orders cores, memory, GPU, then the rest", () => {
    expect(sortResources(["gpu", "fpga", "ram_mb", "cpu", "gpu"])).toEqual(["cpu", "ram_mb", "gpu", "fpga"]);
  });

  it("shows memory in gigabytes and skips zeros", () => {
    expect(amount("ram_mb", 512)).toBe("512 МБ");
    expect(amount("ram_mb", 6144)).toBe("6 ГБ");
    expect(amount("ram_mb", 1536)).toBe("1.5 ГБ");
    expect(formatResources({ gpu: 1, cpu: 2, ram_mb: 4096, fpga: 0 })).toBe("2 CPU · 4 ГБ · 1 GPU");
    expect(formatResources({})).toBe("без ресурсов");
    expect(work("ram_mb", 61440, "мин")).toBe("60 ГБ·мин");
    expect(work("gpu", 4, "ч")).toBe("4 GPU·ч");
  });

  it("takes GPU as the main resource when there is one, otherwise cores", () => {
    expect(mainResource(["cpu", "ram_mb", "gpu"])).toBe("gpu");
    expect(mainResource(["ram_mb", "cpu"])).toBe("cpu");
    expect(mainResource([])).toBeNull();
    expect(clusterResources([{ resources: { cpu: 8, ram_mb: 16384, gpu: 0 } }])).toEqual(["cpu", "ram_mb"]);
  });

  it("fits a task only if every resource it asks for is there", () => {
    expect(fits({ cpu: 6, ram_mb: 6144 }, { cpu: 8, ram_mb: 4096 })).toBe(false);
    expect(fits({ cpu: 6 }, { cpu: 8, ram_mb: 4096 })).toBe(true);
    expect(fits(undefined, {})).toBe(true);
  });
});
