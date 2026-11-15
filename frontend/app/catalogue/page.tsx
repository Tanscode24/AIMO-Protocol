"use client";

import { ChevronLeft, ChevronRight, Radar, Search, SlidersHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { availabilityQuantity, claimLabel } from "@/components/Decision";
import {
  AvailabilityBadge, Badge, Drawer, Empty, ErrorBox, KV, Notice, PageHeader, ScoreBar, Skeleton, Spinner, StatusBadge, Tabs, errMsg,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cls, dt, money, num, pct, titleCase } from "@/lib/format";

/** Unknown values are visually distinct from verified ones (amber, labelled) and never shown as zero. */
function Val({ v, children }: { v: unknown; children?: React.ReactNode }) {
  if (v === null || v === undefined || v === "") {
    return <span className="text-amber-200" title="Not present in the source data">Unknown</span>;
  }
  return <>{children ?? String(v)}</>;
}

const FILTER_KEYS = ["allergen_status", "claim", "has_reliability", "has_carbon", "sales_unit"];

export default function CataloguePage() {
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [facets, setFacets] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);

  const load = useCallback(() => {
    const p = new URLSearchParams({ page: String(page), page_size: "24" });
    if (q.trim()) p.set("q", q.trim());
    Object.entries(filters).forEach(([k, v]) => v && p.set(k, v));
    setLoading(true);
    api(`/api/products?${p}`).then((d) => { setData(d); setError(null); }).catch((e) => setError(errMsg(e))).finally(() => setLoading(false));
  }, [q, filters, page]);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  useEffect(() => { api("/api/products/facets").then(setFacets).catch(() => {}); }, []);

  const setF = (k: string, v: string) => { setPage(1); setFilters((f) => ({ ...f, [k]: v })); };
  const active = FILTER_KEYS.filter((k) => filters[k]).length;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div>
      <PageHeader title="Product Discovery"
        subtitle="Search the official Valio Aimo catalogue in English, Finnish or Swedish, by product code or brand. Live price and availability are checked on demand." />

      <div className="card p-4">
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-mist-500" aria-hidden />
            <input className="input h-11 pl-10 text-[15px]" aria-label="Search products" placeholder="Search products — e.g. lactose free milk, maito, 464201, Arla"
              value={q} onChange={(e) => { setPage(1); setQ(e.target.value); }} />
          </div>
          <button className={cls("btn-ghost h-11", active > 0 && "border-teal-400/40 text-teal-300")} aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}>
            <SlidersHorizontal className="h-4 w-4" aria-hidden />Filters{active > 0 && <span className="rounded-full bg-teal-400 px-1.5 text-[11px] text-white">{active}</span>}
          </button>
        </div>
        {showFilters && (
          <div className="mt-3 grid gap-2 border-t border-ink-700 pt-3 sm:grid-cols-2 lg:grid-cols-5">
            <select className="input" aria-label="Allergen data filter" value={filters.allergen_status || ""} onChange={(e) => setF("allergen_status", e.target.value)}>
              <option value="">Allergen data: any</option><option value="DECLARED">Declared</option><option value="UNKNOWN">Unknown</option>
            </select>
            <select className="input" aria-label="Claim filter" value={filters.claim || ""} onChange={(e) => setF("claim", e.target.value)}>
              <option value="">Dietary claim: any</option>
              {facets?.claims.map((c: string) => <option key={c} value={c}>{claimLabel(c)}</option>)}
            </select>
            <select className="input" aria-label="Reliability filter" value={filters.has_reliability || ""} onChange={(e) => setF("has_reliability", e.target.value)}>
              <option value="">Reliability: any</option><option value="true">Has data</option><option value="false">Unknown</option>
            </select>
            <select className="input" aria-label="Carbon filter" value={filters.has_carbon || ""} onChange={(e) => setF("has_carbon", e.target.value)}>
              <option value="">Carbon: any</option><option value="true">Has estimate</option><option value="false">Unknown</option>
            </select>
            <select className="input" aria-label="Sales unit filter" value={filters.sales_unit || ""} onChange={(e) => setF("sales_unit", e.target.value)}>
              <option value="">Sales unit: any</option>
              {facets?.sales_units.map((u: any) => <option key={u.value} value={u.value}>{u.value} ({u.count})</option>)}
            </select>
            {active > 0 && <button className="btn-quiet justify-start lg:col-span-5" onClick={() => { setPage(1); setFilters({}); }}><X className="h-4 w-4" aria-hidden />Clear filters</button>}
          </div>
        )}
      </div>

      <div className="mt-4"><ErrorBox error={error} onRetry={load} /></div>
      {!data ? (!error && (
        <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label="Searching">
          {Array.from({ length: 6 }).map((_, i) => <div key={i} className="card space-y-3 p-4"><Skeleton className="w-20" /><Skeleton className="h-5" /><Skeleton className="w-1/2" /></div>)}
        </div>
      )) : data.items.length === 0 ? (
        <div className="mt-6"><Empty title="No products match" icon={<Search className="h-5 w-5" />}>Try another term, a product code, or clear the filters.</Empty></div>
      ) : (
        <>
          <div className="mb-3 mt-5 flex flex-wrap items-center justify-between gap-2 text-sm text-mist-400">
            <span aria-live="polite">{num(data.total, 0)} products{loading ? " · updating…" : ""}</span>
            <div className="flex items-center gap-1">
              <button className="btn-quiet min-h-0 px-2 py-1" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></button>
              <span className="tabular-nums">Page {page} of {pages}</span>
              <button className="btn-quiet min-h-0 px-2 py-1" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></button>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {data.items.map((p: any) => (
              <button key={p.id} onClick={() => setSelected(p.id)}
                className="card group flex flex-col p-4 text-left transition hover:border-ink-600 hover:shadow-pop focus-visible:shadow-pop">
                <div className="flex items-center justify-between gap-2">
                  <span className="mono text-mist-500">{p.id}</span>
                  {p.allergen_data_status === "DECLARED"
                    ? <span className="text-[11px] text-mist-500">Allergens declared</span>
                    : <span className="text-[11px] font-medium text-amber-200">Allergens unknown</span>}
                </div>
                <div className="mt-1.5 line-clamp-2 text-sm font-medium leading-snug text-mist-100 group-hover:text-teal-300">{p.name}</div>
                <div className="mt-1 truncate text-xs text-mist-500">{p.brand || "Brand unknown"} · {p.vendor_name}</div>
                <div className="mt-3 flex flex-wrap gap-1">
                  <Badge>{p.sales_unit}{p.sales_unit !== p.base_unit ? ` / ${p.base_unit}` : ""}</Badge>
                  {p.claims.slice(0, 2).map((c: string) => <Badge key={c} tone="green">{claimLabel(c)}</Badge>)}
                  {p.claims.length > 2 && <Badge>+{p.claims.length - 2}</Badge>}
                </div>
                <div className="mt-auto flex items-center gap-4 pt-3 text-xs text-mist-500">
                  {p.reliability != null && <span>Reliability <span className="tabular-nums text-mist-300">{pct(p.reliability)}</span></span>}
                  {p.carbon_kg_per_kg != null && <span>CO₂e <span className="tabular-nums text-mist-300">{num(p.carbon_kg_per_kg, 2)} kg/kg</span></span>}
                  <span className="ml-auto text-teal-300 opacity-0 transition group-hover:opacity-100">Details →</span>
                </div>
              </button>
            ))}
          </div>
        </>
      )}
      <ProductDrawer id={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

type Tab = "overview" | "packaging" | "sustainability" | "alternatives" | "provenance";

function ProductDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { can } = useAuth();
  const [p, setP] = useState<any>(null);
  const [alts, setAlts] = useState<any[] | null>(null);
  const [check, setCheck] = useState<any>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [date, setDate] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() + 2); return d.toISOString().slice(0, 10);
  });
  const [qty, setQty] = useState("1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setP(null); setAlts(null); setCheck(null); setError(null); setTab("overview");
    if (!id) return;
    api(`/api/products/${id}`).then(setP).catch((e) => setError(e.message));
    api(`/api/products/${id}/alternatives`).then((r) => setAlts(r.alternatives)).catch(() => setAlts([]));
  }, [id]);

  async function liveCheck() {
    setBusy(true); setError(null);
    try {
      setCheck(await api(`/api/products/${id}/live-check`, { method: "POST", json: { requested_date: date, quantity: Number(qty) || null } }));
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }

  return (
    <Drawer open={!!id} onClose={onClose} wide title={p ? <span className="block truncate">{p.name} <span className="mono font-normal text-mist-500">{p.id}</span></span> : "Product"}>
      <ErrorBox error={error} />
      {!p ? <Spinner label="Loading product…" /> : (
        <div className="space-y-5">
          <Tabs label="Product details" value={tab} onChange={setTab} tabs={[
            { value: "overview", label: "Overview" }, { value: "packaging", label: "Packaging & units" },
            { value: "sustainability", label: "Delivery & sustainability" }, { value: "alternatives", label: "Alternatives", count: alts?.length },
            { value: "provenance", label: "Evidence & provenance" },
          ]} />

          {tab === "overview" && <>
            <section className="card p-5" aria-labelledby="live-check-title">
              <div className="flex items-center gap-2">
                <Radar className="h-4 w-4 text-mist-500" aria-hidden />
                <h2 id="live-check-title" className="section-title">Live price & availability check</h2>
              </div>
              <p className="mt-1 text-xs text-mist-500">Queried from the Valio API at request time. Nothing is ordered.</p>
              <div className="mt-4 flex flex-wrap items-end gap-3">
                <div><label className="label" htmlFor="lc-date">Delivery date</label><input id="lc-date" type="date" aria-label="Delivery date" className="input mt-1" value={date} onChange={(e) => setDate(e.target.value)} /></div>
                <div><label className="label" htmlFor="lc-qty">Quantity ({p.sales_unit})</label><input id="lc-qty" inputMode="decimal" aria-label="Quantity" className="input mt-1 w-28" value={qty} onChange={(e) => setQty(e.target.value)} /></div>
                <button className="btn-primary" onClick={liveCheck} disabled={busy || !can("live_check")}>{busy ? "Checking…" : "Query Valio API"}</button>
              </div>
              {!can("live_check") && <p className="mt-2 text-xs text-mist-500">Your role is read-only and cannot run live supplier checks.</p>}
              {check && (
                <div className="mt-4 grid gap-3 md:grid-cols-2" aria-live="polite">
                  <div className="rounded-lg border border-ink-700 p-4 text-sm">
                    <div className="mb-2 flex items-center justify-between gap-2"><span className="label">Price</span><StatusBadge status={check.price.status} /></div>
                    {check.price.status === "VERIFIED" ? (<>
                      <KV k="Excl. VAT" v={money(check.price.unit_price_excl_tax, check.price.currency)} /><KV k="Incl. VAT" v={money(check.price.unit_price_incl_tax, check.price.currency)} />
                      <KV k="VAT rate" v={check.price.tax_rate != null ? `${check.price.tax_rate}%` : "—"} />
                      <KV k="Comparison" v={check.price.comparison_price != null ? `${money(check.price.comparison_price, check.price.currency)} / ${check.price.comparison_unit}` : "—"} />
                      <KV k="Valid" v={`${check.price.valid_from?.slice(0, 10)} → ${check.price.valid_to?.slice(0, 10)}`} />
                    </>) : <div className="text-xs text-mist-400">{check.price.reason}</div>}
                  </div>
                  <div className={cls("rounded-lg border p-4 text-sm", check.availability.quantity_basis === "UNCONFIRMED_STOCK_SENTINEL" ? "border-amber-500/40 bg-amber-500/[0.04]" : "border-ink-700")}>
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><span className="label">Availability</span><AvailabilityBadge av={check.availability} /></div>
                    <div className="text-xs text-mist-400">{check.availability.reason}</div>
                    <KV k="Quantity" v={availabilityQuantity(check.availability)} />
                    {check.availability.logistic_model && <KV k="Logistic model" v={check.availability.logistic_model} />}
                    {check.availability.closing_time && <KV k="Order cut-off" v={dt(check.availability.closing_time)} />}
                  </div>
                </div>
              )}
            </section>

            <section className="grid gap-4 md:grid-cols-2">
              <div className="card p-5">
                <h2 className="section-title mb-2">Product</h2>
                <KV k="Brand" v={<Val v={p.brand} />} /><KV k="Vendor" v={p.vendor_name} />
                <KV k="Sales unit" v={<span className="mono">{p.sales_unit}{p.sales_unit !== p.base_unit ? ` (base ${p.base_unit})` : ""}</span>} />
                <KV k="Allowed lot size" v={<span className="mono">{p.allowed_lot_size}</span>} />
                <KV k="Origin" v={<Val v={p.country_of_origin}>{p.country_of_origin?.toUpperCase()}</Val>} />
              </div>
              <div className="card p-5">
                <h2 className="section-title mb-2">Allergens & dietary claims</h2>
                <div className="flex flex-wrap gap-1.5">
                  {p.allergens.contains.map((c: string) => <Badge key={c} tone="red">Contains {p.allergens.labels[c] || c}</Badge>)}
                  {p.allergens.may_contain.map((c: string) => <Badge key={c} tone="amber">May contain {p.allergens.labels[c] || c}</Badge>)}
                  {p.allergens.free_from.map((c: string) => <Badge key={c} tone="green">Free from {p.allergens.labels[c] || c}</Badge>)}
                  {p.allergens.claims.map((c: string) => <Badge key={c} tone="teal">{claimLabel(c)}</Badge>)}
                </div>
                {p.allergens.status === "UNKNOWN" && <Notice tone="warn" className="mt-3">No allergen information. Unknown is never treated as safe.</Notice>}
                <p className="mt-3 text-xs text-mist-500">Lactose-free (claim) is not the same as milk-free (allergen).</p>
              </div>
            </section>
            {(p.key_ingredients || p.description) && (
              <details className="card group">
                <summary className="cursor-pointer list-none px-5 py-3.5 text-sm font-semibold text-mist-100 [&::-webkit-details-marker]:hidden">Ingredients & description</summary>
                <div className="space-y-2 border-t border-ink-700 px-5 py-4 text-sm text-mist-300">
                  {p.key_ingredients && <p><span className="label mr-2">Ingredients</span>{p.key_ingredients}</p>}
                  {p.description && <p className="text-mist-400">{p.description}</p>}
                </div>
              </details>
            )}
          </>}

          {tab === "packaging" && (
            <div className="card p-5">
              <KV k="Sales unit" v={p.sales_unit} mono /><KV k="Base unit" v={p.base_unit} mono /><KV k="Allowed lot size" v={p.allowed_lot_size} mono />
              <div className="mt-3 overflow-x-auto rounded-lg border border-ink-700">
                <table className="w-full text-xs"><thead><tr className="table-head"><th className="px-3 py-2">Unit</th><th className="px-3">Base units</th><th className="px-3">GTIN</th></tr></thead>
                  <tbody>{p.units.map((u: any) => <tr key={u.unitId} className="table-row"><td className="mono px-3 py-2">{u.unitId}</td><td className="px-3 tabular-nums">{u.sizeInBaseUnits}</td><td className="mono px-3">{u.gtin || "—"}</td></tr>)}</tbody></table>
              </div>
              <div className="mt-4 space-y-1">
                {(["volume", "weight"] as const).map((k) => {
                  const v = p.per_sales_unit[k];
                  return <KV key={k} k={`Per ${p.sales_unit} (${k})`} v={<span className="inline-flex items-center gap-2">{v.value != null ? `${num(v.value, 3)} ${v.unit}` : <Val v={null} />}<StatusBadge status={v.status} /></span>} />;
                })}
              </div>
              <p className="mt-3 text-xs text-mist-500">Unit and temperature codes are the dataset&apos;s technical codes; no translations are invented.</p>
            </div>
          )}

          {tab === "sustainability" && (
            <div className="grid gap-4 md:grid-cols-2">
              <div className="card p-5">
                <h2 className="section-title mb-2">Delivery reliability</h2>
                <div className="text-2xl font-semibold tabular-nums">{p.reliability != null ? pct(p.reliability) : <Val v={null} />}</div>
                <p className="mt-1 text-xs text-mist-500">{p.reliability != null ? "Historical, product-specific — not a delivery guarantee." : "No record in the delivery reliability dataset."}</p>
              </div>
              <div className="card p-5">
                <h2 className="section-title mb-2">Carbon footprint</h2>
                <div className="text-2xl font-semibold tabular-nums">{p.carbon_kg_per_kg != null ? <>{num(p.carbon_kg_per_kg, 3)} <span className="text-sm font-normal text-mist-500">kg CO₂e / kg</span></> : <Val v={null} />}</div>
                <p className="mt-1 text-xs text-mist-500">{p.carbon_kg_per_kg != null ? "Product-group average estimate, not a product measurement." : "No record in the carbon dataset."}</p>
              </div>
            </div>
          )}

          {tab === "alternatives" && (
            !alts ? <Spinner /> : alts.length === 0 ? <Empty title="No alternatives">No dataset substitution rules or similar products in the same category.</Empty> : (
              <ul className="space-y-2">
                {alts.map((a) => (
                  <li key={a.id} className="card p-4 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0"><span className="mono mr-2 text-mist-500">{a.id}</span>{a.name}</div>
                      <Badge tone={a.source === "DATASET_SUBSTITUTION" ? "violet" : "slate"}>{a.source === "DATASET_SUBSTITUTION" ? "Official substitution rule" : "Similar product"}</Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <Badge tone={a.comparison.same_sales_unit ? "green" : "amber"}>{a.comparison.same_sales_unit ? "Same sales unit" : `Sales unit ${a.sales_unit}`}</Badge>
                      <StatusBadge status={a.comparison.allergen_data} label={`Allergen data ${titleCase(a.comparison.allergen_data).toLowerCase()}`} />
                      {a.comparison.extra_allergens.map((x: string) => <Badge key={x} tone="red">adds {x}</Badge>)}
                      <Badge tone={a.comparison.auto_substitution_safe ? "green" : "red"}>{a.comparison.auto_substitution_safe ? "Allergen-compatible" : "Not auto-substitutable"}</Badge>
                    </div>
                  </li>
                ))}
              </ul>
            )
          )}

          {tab === "provenance" && (
            <div className="card p-5">
              <KV k="Catalogue record" v="valio_aimo_junction26_product_data.json (schema-validated)" />
              <KV k="GTIN" v={p.gtin || "—"} mono /><KV k="Category code" v={p.category} mono />
              <KV k="Synkka manufacturer data" v={p.synkka_complete ? `Full · updated ${dt(p.synkka_last_updated)}` : "Limited"} />
              <KV k="Allergen labels" v={p.allergens.label_source} />
              <KV k="Reliability source" v={p.provenance.reliability || <Val v={null} />} />
              <KV k="Carbon source" v={p.provenance.carbon || <Val v={null} />} />
              <div className="mt-4">
                <div className="mb-1 flex justify-between text-xs"><span className="text-mist-400">Data completeness</span><span className="tabular-nums">{pct(p.completeness, 0)}</span></div>
                <ScoreBar value={p.completeness} />
                {p.missing_fields.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{p.missing_fields.map((m: string) => <Badge key={m} tone="amber">missing {m.replaceAll("_", " ")}</Badge>)}</div>}
              </div>
              <p className="mt-3 text-xs text-mist-500">Live price and availability evidence is recorded with source, timestamp and raw supplier values whenever a request is evaluated.</p>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}
