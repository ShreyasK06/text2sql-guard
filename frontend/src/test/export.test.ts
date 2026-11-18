import { describe, expect, it } from "vitest";
import { csvCell, maskNotice, toCSV, toJSONExport } from "../lib/exportData";

describe("csvCell escaping", () => {
  it("leaves plain values alone and turns null into an empty cell", () => {
    expect(csvCell("west")).toBe("west");
    expect(csvCell(42)).toBe("42");
    expect(csvCell(null)).toBe("");
    expect(csvCell(undefined)).toBe("");
  });

  it("quotes cells with commas, quotes or newlines and doubles embedded quotes", () => {
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell("line1\nline2")).toBe('"line1\nline2"');
  });
});

describe("formula-injection guard", () => {
  it.each(["=SUM(A1:A9)", "+1+1", "-2+3", "@cmd", "\tTAB", '=HYPERLINK("http://x")'])(
    "neutralises a text cell that starts with a formula character: %j",
    (s) => {
      const bare = csvCell(s).replace(/^"/, "");
      expect(bare.startsWith("'")).toBe(true);
    },
  );

  it("does not touch real numbers, including negatives", () => {
    expect(csvCell(-5)).toBe("-5");
    expect(csvCell(12.5)).toBe("12.5");
  });

  it("only guards the first character", () => {
    expect(csvCell("a=b")).toBe("a=b");
    expect(csvCell("x-y")).toBe("x-y");
  });
});

describe("toCSV", () => {
  const r = {
    columns: ["id", "email", "note"],
    rows: [[1, "d***@yahoo.com", "=1+1"], [2, "b***@yahoo.com", 'has "quote", comma']],
    masked_columns: ["email"],
  };
  const csv = toCSV(r, new Date("2026-01-02T03:04:05Z"));
  const lines = csv.trimEnd().split("\r\n");

  it("starts with comment lines that say the export is masked", () => {
    expect(lines[0]).toMatch(/^# text2sql-guard export, 2026-01-02T03:04:05\.000Z/);
    expect(lines[1]).toMatch(/masked by the output guard.*email/);
    expect(lines.slice(0, 4).every((l) => l.startsWith("#"))).toBe(true);
  });

  it("has a header row, then rows, with the formula cell neutralised and CSV escaping applied", () => {
    expect(lines[4]).toBe("id,email,note");
    expect(lines[5]).toBe("1,d***@yahoo.com,'=1+1");
    expect(lines[6]).toBe('2,b***@yahoo.com,"has ""quote"", comma"');
  });

  it("says plainly when nothing was masked", () => {
    expect(maskNotice([])).toMatch(/No columns needed masking/);
  });
});

describe("toJSONExport", () => {
  it("carries the masked notice, the request context and the rows", () => {
    const out = JSON.parse(toJSONExport(
      { columns: ["email"], rows: [["d***@yahoo.com"]], masked_columns: ["email"], row_count: 1, sql_executed: "SELECT email FROM users LIMIT 1", sql_generated: null },
      { role: "manager", region: "west", question: "emails" },
      new Date("2026-01-02T03:04:05Z"),
    ));
    expect(out.notice).toMatch(/masked.*email/);
    expect(out.masked_columns).toEqual(["email"]);
    expect(out.role).toBe("manager");
    expect(out.region).toBe("west");
    expect(out.rows).toEqual([["d***@yahoo.com"]]);
    expect(out.exported_at).toBe("2026-01-02T03:04:05.000Z");
  });
});
