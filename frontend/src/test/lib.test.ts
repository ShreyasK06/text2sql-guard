import { describe, expect, it } from "vitest";
import demo from "../../public/data/demo.json";
import { buildTrace, ruleTitle } from "../lib/trace";
import { apiUrl, assetUrl } from "../lib/url";
import { tokenizeSQL } from "../lib/sql";
import { demoRequested } from "../context/AppContext";
import type { DemoData } from "../lib/types";

const cases = (demo as unknown as DemoData).cases;
const byId = (id: string) => cases.find((c) => c.id === id)!;

describe("buildTrace", () => {
  it("marks the stage that refused a request and leaves later stages unreached", () => {
    const c = byId("delete");
    const tr = buildTrace(c.response, c.request);
    expect(tr).toHaveLength(8);
    const input = tr.find((t) => t.key === "input_guard")!;
    expect(input.status).toBe("block");
    expect(input.g?.rule_id).toBe("input.destructive_intent");
    expect(tr.find((t) => t.key === "sandbox")!.status).toBe("idle");
  });

  it("passes every gate on an allowed request and reports masking", () => {
    const c = byId("masked");
    const tr = buildTrace(c.response, c.request);
    expect(tr.filter((t) => t.guard).every((t) => t.status === "pass")).toBe(true);
    expect(tr.find((t) => t.key === "output_guard")!.note).toMatch(/masked/);
  });

  it("skips the model stages when SQL is supplied directly", () => {
    const c = byId("passwords");
    const tr = buildTrace(c.response, c.request);
    expect(tr.find((t) => t.key === "generation")!.status).toBe("skip");
    expect(tr.find((t) => t.key === "input_guard")!.status).toBe("skip");
  });

  it("explains known rules in plain language", () => {
    expect(ruleTitle("ast.cartesian_join")).toMatch(/no condition/);
    expect(ruleTitle("sandbox.permission_denied")).toMatch(/database refused/);
  });
});

describe("URL building", () => {
  it("never produces a root-absolute path: everything hangs off the base", () => {
    expect(assetUrl("data/demo.json", "/text2sql-guard/")).toBe("/text2sql-guard/data/demo.json");
    expect(assetUrl("/data/demo.json", "./")).toBe("./data/demo.json");
    expect(apiUrl("health", undefined, "/text2sql-guard/")).toBe("/text2sql-guard/health");
  });

  it("uses VITE_API_BASE when provided", () => {
    expect(apiUrl("/query", "http://localhost:8000/")).toBe("http://localhost:8000/query");
  });
});

describe("misc helpers", () => {
  it("tokenizes SQL into keywords, functions and strings", () => {
    const kinds = tokenizeSQL("SELECT count(*) FROM t WHERE a = 'x'").map((t) => t.kind);
    expect(kinds).toContain("kw");
    expect(kinds).toContain("fn");
    expect(kinds).toContain("str");
  });

  it("detects ?demo=1 before or inside the hash", () => {
    expect(demoRequested("?demo=1", "#/console")).toBe(true);
    expect(demoRequested("", "#/console?demo=1")).toBe(true);
    expect(demoRequested("", "#/console")).toBe(false);
  });
});
