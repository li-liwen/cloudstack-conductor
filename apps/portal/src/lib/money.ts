/**
 * Money and decimal formatting.
 *
 * The API sends every amount as a decimal string. The portal never converts a
 * price to a binary float: it groups the integer part and trims the fraction as
 * text, so displayed values cannot drift from the quoted value.
 */

const DECIMAL_PATTERN = /^-?\d+(?:\.\d+)?$/;

export interface AmountOptions {
  minFractionDigits?: number;
  maxFractionDigits?: number;
}

export function isDecimalString(value: unknown): value is string {
  return typeof value === "string" && DECIMAL_PATTERN.test(value.trim());
}

export function formatAmount(value: string | null | undefined, options: AmountOptions = {}): string {
  if (!isDecimalString(value)) return "—";
  const text = value.trim();
  const min = Math.max(0, options.minFractionDigits ?? 2);
  const max = Math.max(min, options.maxFractionDigits ?? Math.max(min, 4));

  const negative = text.startsWith("-");
  const unsigned = negative ? text.slice(1) : text;
  const [whole, fraction = ""] = unsigned.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

  let displayFraction = fraction.slice(0, max);
  while (displayFraction.length > min && displayFraction.endsWith("0")) {
    displayFraction = displayFraction.slice(0, -1);
  }
  displayFraction = displayFraction.padEnd(min, "0");

  return `${negative ? "-" : ""}${grouped}${displayFraction ? `.${displayFraction}` : ""}`;
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: "¥",
  USD: "$",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
};

/** Format a money string with its currency. Unknown currencies stay explicit. */
export function formatMoney(
  value: string | null | undefined,
  currency: string | null | undefined,
): string {
  const amount = formatAmount(value, { minFractionDigits: 2, maxFractionDigits: 4 });
  if (amount === "—") return amount;
  const code = (currency ?? "").trim().toUpperCase();
  const symbol = CURRENCY_SYMBOLS[code];
  const negative = amount.startsWith("-");
  const unsigned = negative ? amount.slice(1) : amount;
  const body = symbol ? `${symbol}${unsigned}` : code ? `${unsigned} ${code}` : unsigned;
  return negative ? `-${body}` : body;
}

/** A unit rate can carry up to six decimal places. */
export function formatRate(value: string | null | undefined, currency: string | null | undefined): string {
  const amount = formatAmount(value, { minFractionDigits: 2, maxFractionDigits: 6 });
  if (amount === "—") return amount;
  const code = (currency ?? "").trim().toUpperCase();
  const symbol = CURRENCY_SYMBOLS[code];
  return symbol ? `${symbol}${amount}` : code ? `${amount} ${code}` : amount;
}

/** Format a percentage decimal string, e.g. "12.5" -> "12.5%". */
export function formatPercent(value: string | null | undefined): string {
  const amount = formatAmount(value, { minFractionDigits: 0, maxFractionDigits: 2 });
  return amount === "—" ? amount : `${amount}%`;
}
