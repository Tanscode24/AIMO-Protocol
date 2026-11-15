export function money(v: number | null | undefined, currency?: string | null): string {
  if (v === null || v === undefined) return "—";
  try {
    return new Intl.NumberFormat("en-FI", { style: "currency", currency: currency || "EUR", maximumFractionDigits: 2 }).format(v);
  } catch {
    return `${v.toFixed(2)} ${currency || ""}`.trim();
  }
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString("en-FI", { maximumFractionDigits: digits });
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "UNKNOWN";
  return `${(v * 100).toFixed(digits)}%`;
}

export function dt(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v.endsWith("Z") || v.includes("+") ? v : `${v}Z`);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function day(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v.length <= 10 ? `${v}T00:00:00` : v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return "—";
  return s
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function cls(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(" ");
}
