"use client";

import {
  AlertTriangle, ArrowRight, Ban, CalendarClock, CheckCircle2, ChevronDown, ChevronUp, FileCheck2, Gauge, Info, Leaf,
  Package2, Scale, ShieldAlert, ShieldCheck, Truck, Wheat,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { cls, day, dt, money, num, pct, titleCase } from "@/lib/format";
import { AvailabilityBadge, Badge, ScoreBar, StatusBadge, TrustDot } from "./ui";

const CLAIM_LABEL: Record<string, string> = {
  FREE_FROM_LACTOSE: "Lactose-free", LOW_LACTOSE: "Low lactose", VEGAN: "Vegan", VEGETARIAN: "Vegetarian",
  NO_ADDED_SUGARS: "No added sugars", LOW_FAT: "Low fat", FREE_FROM_FAT: "Fat-free", HIGH_PROTEIN: "High protein",
  ORGANIC: "Organic", FREE_FROM_GLUTEN: "Gluten-free", HIGH_FIBRE: "High fibre",
};

// Never present a supplier placeholder as stock: only CONFIRMED_STOCK quantities are shown as numbers.
export function availabilityQuantity(av: any): string {
  if (av?.available_amount != null && (av.quantity_basis ?? "CONFIRMED_STOCK") === "CONFIRMED_STOCK") return `${num(av.available_amount)} ${av.sales_unit}`;
  if (av?.quantity_basis === "UNCONFIRMED_STOCK_SENTINEL") return "NOT CONFIRMED — supplier availability requires human review.";
  if (av?.quantity_basis === "TERMINAL_PLACEHOLDER" || av?.raw_available_amount === 999999) return "unknown (Terminal placeholder)";
  return "UNKNOWN";
}

export function claimLabel(c: string) {
  return CLAIM_LABEL[c] || titleCase(c);
}

const FACTOR_LABEL: Record<string, string> = {
  price: "Price (verified total)", reliability: "Delivery reliability (historical)",
  sustainability: "Sustainability (group-avg CO₂e)", match: "Request match",
};

// ------------------------------------------------------------------ lifecycle
const STAGE_LABEL: Record<string, string> = {
  RECOMMENDATION: "Recommendation", POLICY_DECISION: "Policy decision", HUMAN_APPROVAL: "Human approval",
  PROCUREMENT_PROPOSAL: "Procurement proposal", SUPPLIER_ORDER: "Supplier order",
};

export function Lifecycle({ stages, compact }: { stages?: any[]; compact?: boolean }) {
  if (!stages?.length) return null;
  return (
    <div className={cls("grid gap-2", compact ? "grid-cols-1 sm:grid-cols-5" : "grid-cols-1 md:grid-cols-5")}>
      {stages.map((s, i) => {
        const unsupported = s.state === "NOT_SUPPORTED";
        return (
          <div key={s.stage} className={cls("relative rounded-lg border px-3 py-2.5 text-xs",
            unsupported ? "border-dashed border-rose-400/40 bg-rose-500/5" : "border-ink-700 bg-ink-950/40")}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-mist-100"><span className="mono mr-1 text-teal-300">0{i + 1}</span>{STAGE_LABEL[s.stage]}</span>
            </div>
            <div className="mt-1.5"><StatusBadge status={s.state} label={unsupported ? "Not supported by API" : undefined} /></div>
            {!compact && <div className="mt-1.5 text-[11px] leading-snug text-mist-400">{s.detail}</div>}
          </div>
        );
      })}
    </div>
  );
}

export function NotAnOrderNote() {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-ink-600 bg-ink-950/50 px-3 py-2 text-xs text-mist-300">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal-300" />
      <span>
        Recommendations are <b>not orders</b>. Policy approval only authorises an <b>internal procurement proposal</b>, which stays
        <b> NOT SUBMITTED TO SUPPLIER</b>. Supplier order placement is <b>not supported</b> by the available Valio Aimo API (no order endpoint).
      </span>
    </div>
  );
}

// ------------------------------------------------------------------ decision banner
export function DecisionBanner({ doc }: { doc: any }) {
  const d = doc.decision;
  const tone = d === "ALLOWED" ? "border-l-emerald-500" : d === "REQUIRES_APPROVAL" ? "border-l-amber-500" : "border-l-rose-500";
  const Icon = d === "ALLOWED" ? CheckCircle2 : d === "REQUIRES_APPROVAL" ? ShieldAlert : Ban;
  const headline = {
    ALLOWED: "Policy decision: within autonomous limits",
    REQUIRES_APPROVAL: "Policy decision: human approval required",
    BLOCKED: doc.status === "DUPLICATE" ? "Policy decision: blocked — duplicate request" : "Policy decision: blocked",
  }[d as string] || "Analysed";
  const reasons = (doc.reasons || []).filter((r: any) => r.code !== "WITHIN_POLICY" || d === "ALLOWED");
  return (
    <section aria-label="Policy decision" className={cls("card border-l-4 p-5 animate-fadein", tone)}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <Icon aria-hidden className={cls("mt-0.5 h-5 w-5", d === "ALLOWED" ? "text-emerald-600" : d === "REQUIRES_APPROVAL" ? "text-amber-500" : "text-rose-500")} />
          <div>
            <div className="text-lg font-semibold">{headline}</div>
            <div className="mt-0.5 text-xs text-mist-400">
              Decision <span className="mono text-mist-300">{doc.decision_id || "—"}</span> · Request <span className="mono text-mist-300">{doc.request_id}</span> ·
              Policy “{doc.policy?.name}” v{doc.policy?.version} · Parser {doc.parser}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={d} />
          <StatusBadge status={doc.status} />
        </div>
      </div>
      {reasons.length > 0 && (
        <ul className="mt-4 grid gap-1.5 md:grid-cols-2">
          {reasons.map((r: any, i: number) => (
            <li key={i} className="flex items-start gap-2 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm">
              <StatusBadge status={r.outcome} label={r.code.replaceAll("_", " ")} />
              <span className="text-mist-200">{r.message}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4"><Lifecycle stages={doc.lifecycle} /></div>
      <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
        {doc.proposal && (
          <Link href="/procurement?tab=proposals" className="btn-ghost border-teal-400/40 text-teal-300">
            <FileCheck2 className="h-4 w-4" aria-hidden /> Proposal {doc.proposal.id} — NOT SUBMITTED TO SUPPLIER
          </Link>
        )}
        {doc.human_approval?.approval_id && (
          <Link href="/approvals" className="btn-warn">
            <ShieldCheck className="h-4 w-4" /> Approval {doc.human_approval.approval_id}: {titleCase(doc.human_approval.status)}
          </Link>
        )}
        <Link href={`/audit?request_id=${doc.request_id}`} className="btn-ghost">View audit trail</Link>
      </div>
      <div className="mt-3"><NotAnOrderNote /></div>
    </section>
  );
}

// ------------------------------------------------------------------ intent
export function IntentPanel({ intent, parser }: { intent: any; parser?: string }) {
  if (!intent) return null;
  const unit = { L: "litres", KG: "kg", SALES_UNIT: "sales units" }[intent.unit as string] || intent.unit_text || "";
  return (
    <div className="space-y-3 text-sm">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Product intent" value={<span className="font-medium">{intent.display_query}</span>} hint={`search: “${intent.product_query}”`} />
        <Field label="Quantity" value={intent.quantity ? `${num(intent.quantity)} ${unit}` : "UNKNOWN"} />
        <Field label="Delivery date" value={day(intent.delivery_date)} hint={intent.delivery_date_text ? `from “${intent.delivery_date_text}”` : "assumed"} />
        <Field label="Budget" value={intent.budget_limit ? money(intent.budget_limit) : "Not specified"} />
      </div>
      <div>
        <div className="label mb-1.5">Hard requirements</div>
        <div className="flex flex-wrap gap-1.5">
          {intent.required_attributes?.map((c: string) => <Badge key={c} tone="teal">{claimLabel(c)} required</Badge>)}
          {intent.excluded_allergens?.map((c: string) => <Badge key={c} tone="red">No {c}</Badge>)}
          {!intent.required_attributes?.length && !intent.excluded_allergens?.length && <span className="text-xs text-mist-500">None stated</span>}
        </div>
        {intent.excluded_allergen_terms?.length > 0 && (
          <div className="mt-1 text-[11px] text-mist-500">From: {intent.excluded_allergen_terms.map((t: string) => `“${t}”`).join(", ")}</div>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {intent.priorities?.map((p: string) => <Badge key={p} tone="blue">Priority: {p}</Badge>)}
        {intent.delivery_preferences?.map((p: string) => <Badge key={p}>{p}</Badge>)}
      </div>
      {(intent.assumptions?.length > 0 || intent.warnings?.length > 0) && (
        <div className="space-y-1 rounded-lg border border-amber-400/20 bg-amber-400/5 p-2.5 text-xs text-amber-100">
          {[...(intent.assumptions || []), ...(intent.warnings || [])].map((a: string, i: number) => (
            <div key={i} className="flex gap-1.5"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{a}</div>
          ))}
        </div>
      )}
      {parser && <div className="text-[11px] text-mist-500">Extracted by: {parser === "RULES" ? "deterministic parser (no LLM required)" : parser}</div>}
    </div>
  );
}

function Field({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-950/40 p-2.5">
      <div className="label">{label}</div>
      <div className="mt-1 text-mist-100">{value}</div>
      {hint && <div className="text-[11px] text-mist-500">{hint}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ evidence
export function EvidenceList({ evidence }: { evidence: any[] }) {
  if (!evidence?.length) return <div className="text-xs text-mist-500">No evidence recorded.</div>;
  return (
    <ul className="divide-y divide-ink-700 rounded-lg border border-ink-700 bg-white">
      {evidence.map((e) => (
        <li key={e.id} className="px-3.5 py-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium text-mist-100">{titleCase(e.claim)}</span>
            <StatusBadge status={e.state} />
          </div>
          <div className="mono mt-1 break-all text-mist-200">{e.value === null || e.value === undefined ? <span className="font-sans font-medium text-amber-200">Unknown</span> : String(e.value)}</div>
          {e.detail && <div className="mt-1 break-words text-mist-400">{e.detail}</div>}
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-mist-500">
            <span className="break-all">Source: {e.source || "—"}</span>
            <span>{e.timestamp ? dt(e.timestamp) : "dataset file"}</span>
            <span className="mono">product {e.product_id}</span>
            <span className="mono break-all">{e.id}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function EvRef({ id, evidence }: { id?: string; evidence?: any[] }) {
  if (!id) return null;
  const e = evidence?.find((x) => x.id === id);
  const title = e ? `${e.state} · source: ${e.source || "—"} · ${e.timestamp ? dt(e.timestamp) : "dataset file"} · product ${e.product_id}` : id;
  return (
    <span title={title} className="mono mt-1 inline-flex max-w-full items-center gap-1 truncate rounded border border-ink-700 bg-ink-900 px-1 py-px text-[10px] text-mist-500">
      {e && <TrustDot state={e.state} />}{id}
    </span>
  );
}

// ------------------------------------------------------------------ recommendation card
export function RecommendationCard({ c, onChoose, chooseDisabled, selected }: {
  c: any; onChoose?: (id: string) => void; chooseDisabled?: boolean; selected?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const price = c.price || {};
  const av = c.availability || {};
  const q = c.quantity || {};
  const pk = c.packaging || {};
  const rel = c.reliability_info || {};
  const carb = c.carbon_info || {};
  const ev = c.evidence || [];
  const labelTone = c.label === "BEST" ? "teal" : c.label === "BETTER" ? "blue" : "slate";
  const coverage = c.evidence_coverage ?? 1;
  return (
    <div className={cls("card overflow-hidden animate-fadein", selected && "ring-1 ring-teal-400/50")}>
      <div className="flex flex-wrap items-start gap-4 p-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-ink-700 bg-ink-900 text-sm font-semibold text-mist-300">#{c.rank}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {c.label && <span title="Ranking label relative to the eligible options for this request"><Badge tone={labelTone as any}>{c.label}</Badge></span>}
            {selected && <Badge tone="teal">SELECTED BY AGENT</Badge>}
            {c.source === "DATASET_SUBSTITUTION" && <Badge tone="violet">Dataset substitute for {c.substitute_for}</Badge>}
            <StatusBadge status={c.tier} />
            <StatusBadge status={c.policy?.decision} label={`Policy: ${(c.policy?.decision || "").replaceAll("_", " ")}`} />
          </div>
          <div className="mt-1.5 text-base font-semibold leading-snug text-mist-100">{c.product.name}</div>
          <div className="mt-0.5 text-xs text-mist-400">
            ID <span className="mono text-mist-300">{c.id}</span> · Brand: {c.product.brand || <span className="text-amber-200">UNKNOWN</span>} · {c.product.vendor_name} · category {c.product.category}
          </div>
          <div className="mt-0.5 text-xs text-mist-400">Packaging: <span className="text-mist-200">{pk.description || c.product.sales_unit}</span> · lot size {num(c.product.allowed_lot_size)}</div>
        </div>
        <div className="w-48 text-right">
          <div className="label">Ranking score</div>
          <div className="text-2xl font-semibold">{(c.score * 100).toFixed(0)}<span className="text-sm text-mist-500">/100</span></div>
          <ScoreBar value={c.score} className="mt-1" />
          <div className={cls("mt-1 text-[11px]", coverage < 1 ? "text-amber-200" : "text-mist-500")}>
            Evidence coverage {(coverage * 100).toFixed(0)}%{coverage < 1 ? " — partial" : ""}
          </div>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-ink-700 px-4 py-3 text-sm sm:grid-cols-3 xl:grid-cols-6">
        <div><dt className="text-xs text-mist-500">Order</dt><dd className="mt-0.5 font-medium tabular-nums">{q.order_quantity != null ? `${num(q.order_quantity)} × ${q.sales_unit}` : <span className="text-amber-200">Unknown</span>}</dd></div>
        <div><dt className="text-xs text-mist-500">Total excl. VAT</dt><dd className="mt-0.5 font-medium tabular-nums">{price.status === "VERIFIED" && c.total_excl_tax != null ? money(c.total_excl_tax, c.currency) : <span className="text-amber-200">Not verified</span>}</dd></div>
        <div><dt className="text-xs text-mist-500">Price</dt><dd className="mt-0.5"><StatusBadge status={price.status} /></dd></div>
        <div><dt className="text-xs text-mist-500">Availability</dt><dd className="mt-0.5"><AvailabilityBadge av={av} />
          {av.quantity_basis === "UNCONFIRMED_STOCK_SENTINEL" && <div className="mt-1 text-[11px] leading-snug text-amber-100">{availabilityQuantity(av)}</div>}</dd></div>
        <div><dt className="text-xs text-mist-500">Reliability · CO₂e</dt><dd className="mt-0.5 tabular-nums">{rel.value != null ? pct(rel.value) : <span className="text-amber-200">Unknown</span>} · {carb.value_per_kg != null ? `${num(carb.value_per_kg, 2)} kg/kg` : <span className="text-amber-200">Unknown</span>}</dd></div>
        <div><dt className="text-xs text-mist-500">Allergens</dt><dd className="mt-0.5">{(c.allergens?.checks || []).length > 0 ? <StatusBadge status={c.allergens.overall} /> : <span className="text-mist-500">No constraint</span>}</dd></div>
      </dl>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-ink-700 px-4 py-2.5">
        <button className="btn-quiet min-h-0 py-1.5 text-xs" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? <ChevronUp className="h-3.5 w-3.5" aria-hidden /> : <ChevronDown className="h-3.5 w-3.5" aria-hidden />} {open ? "Hide" : "Show"} evidence, score & policy reasoning
        </button>
        {onChoose && (
          <button className="btn-primary py-1.5 text-xs" disabled={chooseDisabled} onClick={() => onChoose(c.id)}
            title="Submits this option to the policy engine. This is not an order.">
            <CalendarClock className="h-3.5 w-3.5" /> Submit this option for policy decision
          </button>
        )}
      </div>
      {open && (
        <div className="border-t border-ink-700">
      <div className="grid gap-px border-t border-ink-700 bg-ink-700 sm:grid-cols-2 xl:grid-cols-3">
          <Cell icon={<Package2 className="h-3.5 w-3.5" />} label="Quantity">
            <div className="flex flex-wrap items-center gap-1.5 text-sm">
              <span className="text-mist-400">Requested</span><b>{q.requested_amount != null ? `${num(q.requested_amount)} ${q.requested_unit || ""}` : "UNKNOWN"}</b>
              <ArrowRight className="h-3 w-3 text-mist-500" />
              <span className="text-mist-400">Order</span><b>{q.order_quantity != null ? `${num(q.order_quantity)} × ${q.sales_unit}` : "UNKNOWN"}</b>
            </div>
            <div className="mt-1 text-xs text-mist-300">
              Deliverable: <b>{q.deliverable_amount != null ? `${num(q.deliverable_amount, 3)} ${q.deliverable_unit || ""}` : "UNKNOWN"}</b>
              {q.surplus != null && q.surplus > 0 && <span className="text-mist-500"> (+{num(q.surplus, 3)} above request: pack/lot size)</span>}
            </div>
            <div className="mt-1"><StatusBadge status={q.status || c.conversion?.status} label={`Conversion ${q.status || c.conversion?.status}`} /></div>
            <EvRef id={q.evidence_ref} evidence={ev} />
          </Cell>
          <Cell icon={<Scale className="h-3.5 w-3.5" />} label="Price (Valio API)">
            {price.status === "VERIFIED" ? (
              <div className="space-y-0.5 text-xs">
                <Row k={`Unit price / ${c.product.sales_unit}`} v={`${money(price.unit_price_excl_tax, price.currency)} excl. VAT`} />
                <Row k="Total excl. VAT" v={<b className="text-sm">{money(c.total_excl_tax, c.currency)}</b>} />
                <Row k="Total incl. VAT" v={c.total_incl_tax != null ? money(c.total_incl_tax, c.currency) : "not provided"} />
                <Row k="VAT rate" v={price.vat_rate != null ? `${price.vat_rate}% (tax.taxRate)` : "UNKNOWN"} />
                <Row k="Valid" v={`${String(price.valid_from || "").slice(0, 10)} → ${String(price.valid_to || "").slice(0, 10)}`} />
              </div>
            ) : (
              <div className="text-xs text-mist-400">No verified price — totals not computed (not zero). {price.reason}</div>
            )}
            <div className="mt-1"><StatusBadge status={price.status} /></div>
            <EvRef id={price.evidence_ref || `ev:${c.id}:price`} evidence={ev} />
          </Cell>
          <Cell icon={<Truck className="h-3.5 w-3.5" />} label="Availability (Valio API)">
            <div className="space-y-0.5 text-xs">
              <Row k="Requested date" v={day(av.requested_date)} />
              <Row k="Earliest available" v={av.available_date ? day(av.available_date) : "UNKNOWN"} />
              <Row k="On time" v={av.on_time === true ? <span className="text-emerald-300">Yes</span> : av.on_time === false ? <span className="text-rose-300">No — later</span> : "UNKNOWN"} />
              <Row k="Quantity" v={availabilityQuantity(av)} />
              {av.closing_time && <Row k="Order cut-off" v={dt(av.closing_time)} />}
            </div>
            <div className="mt-1"><AvailabilityBadge av={av} /></div>
            <EvRef id={av.evidence_ref || `ev:${c.id}:availability`} evidence={ev} />
          </Cell>
          <Cell icon={<Gauge className="h-3.5 w-3.5" />} label="Delivery reliability">
            <div className="font-medium">{rel.value != null ? pct(rel.value) : <span className="text-amber-200">UNKNOWN</span>}</div>
            <div className="text-[11px] text-mist-400">{rel.label || "Historical"}</div>
            <Badge tone="slate" className="mt-1">Historical · not a delivery guarantee</Badge>
            <div><EvRef id={rel.evidence_ref} evidence={ev} /></div>
          </Cell>
          <Cell icon={<Leaf className="h-3.5 w-3.5" />} label="Carbon footprint">
            <div className="font-medium">{carb.value_per_kg != null ? `${num(carb.value_per_kg, 3)} kg CO₂e/kg` : <span className="text-amber-200">UNKNOWN</span>}</div>
            <div className="text-[11px] text-mist-400">{carb.label}</div>
            <Badge tone="slate" className="mt-1">Basis: product-group average</Badge>
            <div className="mt-1 text-[11px] text-mist-400">
              Order estimate: {carb.order_total_kg != null ? `≈ ${num(carb.order_total_kg, 1)} kg CO₂e (verified net weight)` : `not computed — ${carb.order_total_reason || "unknown"}`}
            </div>
            <EvRef id={carb.evidence_ref} evidence={ev} />
          </Cell>
          <Cell icon={<Wheat className="h-3.5 w-3.5" />} label="Allergen & requirement verification">
            <div className="space-y-1">
              {(c.allergens?.checks || []).map((a: any) => (
                <div key={a.code} className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate text-mist-300" title={`${a.label} — ${a.detail || ""}`}><span className="mono text-mist-100">{a.code}</span> {a.label}</span>
                  <StatusBadge status={a.status} label={a.status.replace("_", " ")} />
                </div>
              ))}
              {(c.claims || []).map((cl: any) => (
                <div key={cl.claim} className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-mist-300" title={cl.detail}>{claimLabel(cl.claim)}</span>
                  <StatusBadge status={cl.status} label={cl.status.replace("_", " ")} />
                </div>
              ))}
              {!(c.allergens?.checks || []).length && !(c.claims || []).length && <span className="text-xs text-mist-500">No allergen or dietary constraints in request</span>}
            </div>
            {(c.allergens?.checks || []).length > 0 && <div className="mt-1"><StatusBadge status={c.allergens.overall} /></div>}
            <div className="flex flex-wrap gap-1">{(c.allergens?.checks || []).map((a: any) => <EvRef key={a.code} id={`ev:${c.id}:allergen:${a.code}`} evidence={ev} />)}</div>
          </Cell>
        </div>
  
        <div className="grid gap-5 p-4 lg:grid-cols-[1fr_1.15fr]">
          <div>
            <div className="label mb-2">Why this product</div>
            <ul className="space-y-1 text-sm text-mist-200">
              {(c.reasons || []).map((r: string, i: number) => <li key={i} className="flex gap-2"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal-300" />{r}</li>)}
              {(c.tradeoffs || []).map((r: string, i: number) => <li key={`t${i}`} className="flex gap-2 text-mist-400"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300" />{r}</li>)}
            </ul>
          </div>
          <ScoreBreakdown c={c} />
        </div>
        <div className="space-y-4 border-t border-ink-700 bg-ink-900 p-4">
          <div>
            <div className="label mb-2">Policy evaluation (backend, deterministic)</div>
            <div className="space-y-1">
              {(c.policy?.reasons || []).map((r: any, i: number) => (
                <div key={i} className="flex items-start gap-2 text-sm"><StatusBadge status={r.outcome} label={r.code.replaceAll("_", " ")} /><span className="text-mist-300">{r.message}</span></div>
              ))}
            </div>
          </div>
          {(q.notes || c.conversion?.notes)?.length > 0 && (
            <div>
              <div className="label mb-1">Unit & lot-size reasoning</div>
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-mist-300">{(q.notes || c.conversion.notes).map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>
              {pk.per_sales_unit_source && <div className="mt-1 text-[11px] text-mist-500">Packaging source: {pk.per_sales_unit_source}</div>}
            </div>
          )}
          <div>
            <div className="label mb-2">Trust layer evidence</div>
            <EvidenceList evidence={ev} />
          </div>
        </div>
        </div>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-2"><span className="text-mist-400">{k}</span><span className="text-right text-mist-100">{v}</span></div>;
}

function fmtRaw(f: string, v: any, currency?: string) {
  if (v === null || v === undefined) return "missing";
  if (f === "price") return money(v, currency);
  if (f === "reliability" || f === "match") return pct(v);
  return `${num(v, 3)} kg/kg`;
}

export function ScoreBreakdown({ c }: { c: any }) {
  const rows = c.score_breakdown || [];
  if (!rows.length) return null;
  return (
    <div>
      <div className="label mb-2">Score breakdown — contribution to {(c.score * 100).toFixed(1)} points</div>
      <div className="overflow-hidden rounded-lg border border-ink-700">
        <table className="w-full text-xs">
          <thead><tr className="table-head"><th className="px-2.5 py-1.5">Factor</th><th className="px-2">Value</th><th className="px-2">Normalised</th><th className="px-2">Weight</th><th className="px-2 text-right">Points</th></tr></thead>
          <tbody>
            {rows.map((r: any) => (
              <tr key={r.factor} className="border-t border-ink-700/70">
                <td className="px-2.5 py-1.5 text-mist-200">{FACTOR_LABEL[r.factor]}</td>
                <td className="mono px-2">{fmtRaw(r.factor, r.raw_value, c.currency)}</td>
                <td className="px-2">{r.normalised != null ? <div className="flex items-center gap-1.5"><ScoreBar value={r.normalised} className="w-14" /><span className="mono">{r.normalised.toFixed(2)}</span></div> : <span className="text-amber-200">—</span>}</td>
                <td className="mono px-2 text-mist-400">
                  {r.status === "PRESENT" ? `${(r.base_weight * 100).toFixed(0)}% → ${(r.effective_weight * 100).toFixed(0)}%` : <span className="text-amber-200">{(r.base_weight * 100).toFixed(0)}% redistributed</span>}
                </td>
                <td className="mono px-2 text-right font-semibold">{r.contribution != null ? r.contribution.toFixed(1) : <span className="font-normal text-amber-200">not scored</span>}</td>
              </tr>
            ))}
            <tr className="border-t border-ink-600 bg-ink-950/40"><td className="px-2.5 py-1.5 font-semibold" colSpan={4}>Total</td><td className="mono px-2 text-right font-semibold">{(c.score * 100).toFixed(1)}</td></tr>
          </tbody>
        </table>
      </div>
      {c.score_note && <p className={cls("mt-2 text-[11px]", (c.evidence_coverage ?? 1) < 1 ? "text-amber-100/90" : "text-mist-500")}>{c.score_note}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ ranking explanation (#1 vs #2)
export function RankingWhy({ ex, recs }: { ex: any; recs: any[] }) {
  if (!ex) return null;
  const a = recs.find((r) => r.id === ex.first);
  const b = recs.find((r) => r.id === ex.second);
  return (
    <div className="space-y-3">
      <div className="text-sm text-mist-300">
        <b className="text-mist-100">#1 {a?.product.name}</b> <span className="mono text-mist-500">{ex.first}</span> vs{" "}
        <b className="text-mist-100">#2 {b?.product.name}</b> <span className="mono text-mist-500">{ex.second}</span>
      </div>
      <ul className="space-y-1.5 text-sm">
        {ex.sentences.map((s: string, i: number) => <li key={i} className="flex gap-2 text-mist-200"><ArrowRight className="mt-1 h-3.5 w-3.5 shrink-0 text-teal-300" />{s}</li>)}
      </ul>
      <div className="overflow-x-auto rounded-lg border border-ink-700">
        <table className="w-full text-xs">
          <thead><tr className="table-head"><th className="px-3 py-1.5">Factor</th><th className="px-2">#1 value</th><th className="px-2">#1 points</th><th className="px-2">#2 value</th><th className="px-2">#2 points</th><th className="px-2 text-right">Δ points</th></tr></thead>
          <tbody>
            {ex.factors.map((f: any) => (
              <tr key={f.factor} className="border-t border-ink-700/70">
                <td className="px-3 py-1.5">{FACTOR_LABEL[f.factor]}</td>
                <td className="mono px-2">{fmtRaw(f.factor, f.first_value, a?.currency)}</td>
                <td className="mono px-2">{f.first_contribution != null ? f.first_contribution.toFixed(1) : <span className="text-amber-200">missing</span>}</td>
                <td className="mono px-2">{fmtRaw(f.factor, f.second_value, b?.currency)}</td>
                <td className="mono px-2">{f.second_contribution != null ? f.second_contribution.toFixed(1) : <span className="text-amber-200">missing</span>}</td>
                <td className={cls("mono px-2 text-right", f.difference > 0 ? "text-emerald-300" : f.difference < 0 ? "text-rose-300" : "text-mist-400")}>
                  {f.difference != null ? `${f.difference > 0 ? "+" : ""}${f.difference.toFixed(1)}` : "n/a"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ comparison view (top 3)
const CRIT_BADGE: Record<string, string> = {
  "Best verified price": "Lowest verified price",
  "Best delivery reliability": "Highest historical reliability",
  "Best sustainability evidence": "Lowest group-average CO₂e",
};

export function ComparisonView({ comparison, recs }: { comparison: any; recs: any[] }) {
  if (!comparison?.products?.length) return null;
  const cols = comparison.products.map((id: string) => recs.find((r) => r.id === id)).filter(Boolean);
  const rowsById: Record<string, any> = Object.fromEntries((comparison.rows || []).map((r: any) => [r.product_id, r]));
  const [priceC, relC, carbC] = comparison.criteria;
  const isBest = (crit: any, id: string) => crit?.best?.includes(id);
  const winner = (crit: any, id: string) => isBest(crit, id) && (
    <Badge tone="green" className="mt-1">{CRIT_BADGE[crit.criterion]}{crit.tie ? " (tie)" : ""}</Badge>
  );
  const critNote = (crit: any) => (
    <div className="mt-0.5 text-[10px] font-normal normal-case tracking-normal text-mist-500">
      {crit.status === "COMPARED" ? crit.basis : <span className="text-amber-200">{crit.status.replaceAll("_", " ")} — {crit.note}</span>}
      {(crit.caveats || []).map((cv: string) => <div key={cv} className="mt-1 text-amber-200">⚠ {cv}</div>)}
    </div>
  );
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-lg border border-ink-700">
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-ink-700 bg-ink-950/40">
              <th className="w-52 px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-mist-400">Criterion</th>
              {cols.map((c: any) => (
                <th key={c.id} className="px-3 py-2 text-left align-top">
                  <div className="text-xs text-mist-500">#{c.rank} · <span className="mono">{c.id}</span></div>
                  <div className="text-sm font-semibold text-mist-100">{c.product.name}</div>
                  <div className="mt-1"><StatusBadge status={c.policy?.decision} /></div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <CRow label="Total excl. VAT" note={critNote(priceC)} cells={cols.map((c: any) => (
              <div key={c.id} className={cls(isBest(priceC, c.id) && "text-emerald-200")}>
                {c.price?.status === "VERIFIED" ? <b>{money(c.total_excl_tax, c.currency)}</b> : <StatusBadge status={c.price?.status} />}
                {c.total_incl_tax != null && <div className="text-[11px] text-mist-500">incl. VAT {money(c.total_incl_tax, c.currency)} · VAT {c.price?.vat_rate}%</div>}
                {winner(priceC, c.id)}
              </div>
            ))} />
            <CRow label="Unit price" cells={cols.map((c: any) => <div key={c.id} className="text-xs">{c.price?.status === "VERIFIED" ? `${money(c.price.unit_price_excl_tax, c.currency)} / ${c.product.sales_unit}` : "—"}</div>)} />
            <CRow label="Availability & date" cells={cols.map((c: any) => (
              <div key={c.id} className="text-xs"><AvailabilityBadge av={c.availability} />
                <div className="mt-0.5 text-mist-400">{c.availability?.available_date ? `${day(c.availability.available_date)}${c.availability.on_time ? " · on time" : c.availability.on_time === false ? " · late" : ""}` : "date unknown"}</div></div>
            ))} />
            <CRow label="Delivery reliability" note={critNote(relC)} cells={cols.map((c: any) => (
              <div key={c.id} className={cls(isBest(relC, c.id) && "text-emerald-200")}>{c.reliability != null ? pct(c.reliability) : <span className="text-amber-200">UNKNOWN</span>}
                <div className="text-[11px] text-mist-500">historical</div>{winner(relC, c.id)}</div>
            ))} />
            <CRow label="Carbon footprint" note={critNote(carbC)} cells={cols.map((c: any) => (
              <div key={c.id} className={cls(isBest(carbC, c.id) && "text-emerald-200")}>{c.carbon_kg_per_kg != null ? `${num(c.carbon_kg_per_kg, 3)} kg/kg` : <span className="text-amber-200">UNKNOWN</span>}
                <div className="text-[11px] text-mist-500">product-group average</div>{winner(carbC, c.id)}</div>
            ))} />
            <CRow label="Quantity compatibility" cells={cols.map((c: any) => {
              const q = rowsById[c.id]?.quantity_compatibility || {};
              return (
                <div key={c.id} className="text-xs"><StatusBadge status={q.status} />
                  <div className="mt-0.5 text-mist-300">{q.order_quantity != null ? `${num(q.order_quantity)} × ${q.sales_unit}` : "—"} → {q.deliverable != null ? `${num(q.deliverable, 3)} ${q.deliverable_unit || ""}` : "UNKNOWN"}</div>
                  {q.surplus > 0 && <div className="text-mist-500">+{num(q.surplus, 3)} surplus</div>}
                </div>
              );
            })} />
            <CRow label="Allergen compliance" cells={cols.map((c: any) => {
              const r = rowsById[c.id] || {};
              return <div key={c.id} className="text-xs"><StatusBadge status={r.allergen_compliance} />
                <div className="mt-1 flex flex-wrap gap-1">{(r.allergen_checks || []).map((a: any) => <Badge key={a.code} tone={a.status === "FREE_FROM" ? "green" : "amber"}>{a.code} {a.status.replace("_", " ")}</Badge>)}</div></div>;
            })} />
            <CRow label="Uncertainty" cells={cols.map((c: any) => {
              const u = rowsById[c.id]?.uncertainties || [];
              return <div key={c.id} className="text-xs">{u.length === 0 ? <span className="text-emerald-300">None identified</span> :
                <ul className="space-y-0.5 text-amber-100/90">{u.map((x: string) => <li key={x}>• {x}</li>)}</ul>}</div>;
            })} />
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-mist-500">{comparison.note}</p>
    </div>
  );
}

function CRow({ label, note, cells }: { label: string; note?: React.ReactNode; cells: React.ReactNode[] }) {
  return (
    <tr className="border-t border-ink-700/70 align-top">
      <td className="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-mist-400">{label}{note}</td>
      {cells.map((x, i) => <td key={i} className="px-3 py-2.5">{x}</td>)}
    </tr>
  );
}

function Cell({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="bg-ink-850 px-4 py-3 text-sm">
      <div className="label mb-1.5 flex items-center gap-1.5">{icon}{label}</div>
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ excluded
export function ExcludedList({ items }: { items: any[] }) {
  const [open, setOpen] = useState(false);
  if (!items?.length) return null;
  const shown = open ? items : items.slice(0, 6);
  return (
    <div className="space-y-2">
      {shown.map((x, i) => (
        <div key={`${x.id}-${i}`} className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-ink-700 bg-ink-950/40 px-3 py-2 text-sm">
          <div className="min-w-0">
            <div className="text-mist-200">{x.product?.name} <span className="mono text-mist-500">{x.id}</span></div>
            <div className="text-xs text-rose-200/90">{(x.exclusions || []).join(" · ")}</div>
          </div>
          <Badge tone="slate">{x.stage === "STATIC" ? "Static check" : "Live check"}</Badge>
        </div>
      ))}
      {items.length > 6 && (
        <button className="btn-ghost text-xs" onClick={() => setOpen(!open)}>{open ? "Show fewer" : `Show all ${items.length}`}</button>
      )}
    </div>
  );
}
