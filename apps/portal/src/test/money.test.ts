import { describe, expect, it } from "vitest";
import {
  formatAmount,
  formatMoney,
  formatPercent,
  formatRate,
  isDecimalString,
} from "../lib/money";

describe("money formatting", () => {
  it("groups decimal strings without converting them to binary floats", () => {
    expect(formatMoney("1234.5", "CNY")).toBe("¥1,234.50");
    expect(formatMoney("9007199254740993.10", "USD")).toBe("$9,007,199,254,740,993.10");
    expect(formatMoney("0.0000", "CNY")).toBe("¥0.00");
    expect(formatMoney("12.345678", "EUR")).toBe("€12.3456");
  });

  it("keeps the sign and uses an explicit currency code when there is no symbol", () => {
    expect(formatMoney("-12.5", "CNY")).toBe("-¥12.50");
    expect(formatMoney("10.00", "SGD")).toBe("10.00 SGD");
    expect(formatPercent("12.500")).toBe("12.5%");
    expect(formatRate("0.0123456", "USD")).toBe("$0.012345");
  });

  it("never fabricates a value for invalid or missing amounts", () => {
    expect(formatMoney("not-a-price", "CNY")).toBe("—");
    expect(formatMoney(null, "CNY")).toBe("—");
    expect(formatMoney(undefined, "CNY")).toBe("—");
    expect(formatAmount("1e3")).toBe("—");
    expect(isDecimalString("10.25")).toBe(true);
    expect(isDecimalString("1,000.00")).toBe(false);
  });
});
