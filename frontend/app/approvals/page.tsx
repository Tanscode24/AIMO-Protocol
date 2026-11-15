"use client";

import { Check, MessageSquareWarning, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { EvidenceList, IntentPanel, Lifecycle, NotAnOrderNote, ScoreBreakdown, availabilityQuantity } from "@/components/Decision";
import {
  AvailabilityBadge, Badge, Card, ConfirmDialog, Empty, ErrorBox, KV, Notice, PageHeader, Segmented, SkeletonRows, StatusBadge, Tabs, errMsg,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cls, day, dt, money, num, pct } from "@/lib/format";

type DetailTab = "summary" | "evidence" | "constraints" | "history";

export default function ApprovalsPage() {
  const { can, user } = useAuth();
  const [filter, setFilter] = useState<"PENDING" | "ALL">("PENDING");
  const [list, setList] = useState<any[] | null>(null);
  const [sel, setSel] = useState<any>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "warn" | "neutral"; text: string } | null>(null);
  const [confirmReject, setConfirmReject] = useState(false);
  const [tab, setTab] = useState<DetailTab>("summary");

  const load = useCallback(() => {
    api<any[]>(`/api/approvals${filter === "PENDING" ? "?status=PENDING" : ""}`)
      .then((l) => { setList(l); setSel((s: any) => (s ? l.find((x) => x.id === s.id) || l[0] || null : l[0] || null)); })
      .catch((e) => setError(errMsg(e)));
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  async function act(action: "approve" | "reject" | "request-changes") {
    if (!sel) return;
    setBusy(true); setError(null); setFlash(null);
    try {
      const r = await api(`/api/approvals/${sel.id}/${action}`, { method: "POST", json: { reason } });
      setSel(r);
      setReason("");
      setFlash(r.status === "APPROVED"
        ? { tone: "success", text: "Policy approval recorded. Price and availability were revalidated and an internal procurement proposal was created — NOT SUBMITTED TO SUPPLIER. No supplier order was placed." }
        : r.status === "APPROVED_REVALIDATION_FAILED"
          ? { tone: "warn", text: "Approval recorded, but revalidation found new problems — no proposal was created." }
          : { tone: "neutral", text: `Decision recorded: ${r.status.replaceAll("_", " ")}.` });
      api<any[]>(`/api/approvals${filter === "PENDING" ? "?status=PENDING" : ""}`).then(setList);
    } catch (e: any) { setError(errMsg(e)); } finally { setBusy(false); setConfirmReject(false); }
  }

  const c = sel?.candidate;
  const isPending = sel?.status === "PENDING";
  const ownRequest = sel && user && sel.request.created_by === user.name; // UI hint only; enforced by the backend
  const canDecide = can("approve") && isPending;

  return (
    <div>
      <PageHeader title="Approval Center"
        subtitle="Requests the policy engine could not decide autonomously. Approvals are single-use, require the Approver role, enforce separation of duties and re-check live price and availability before any proposal is created."
        actions={<Segmented value={filter} onChange={setFilter} options={[{ value: "PENDING", label: "Pending" }, { value: "ALL", label: "History" }]} />} />
      <ErrorBox error={error} onRetry={load} />
      {flash && <Notice tone={flash.tone} role="status" className="mb-4">{flash.text}</Notice>}

      {!list ? (error ? null : <div className="card p-5"><SkeletonRows /></div>) : list.length === 0 && !sel ? (
        <Empty title={filter === "PENDING" ? "No pending approvals" : "No approvals yet"} icon={<ShieldCheck className="h-5 w-5" />}>
          {filter === "PENDING" ? "Nothing is waiting for a human decision." : "Approvals appear when a request exceeds autonomous policy limits."}
        </Empty>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
          <nav aria-label="Approval queue" className="space-y-2">
            {list.map((a) => (
              <button key={a.id} onClick={() => { setSel(a); setFlash(null); setTab("summary"); }} aria-current={sel?.id === a.id ? "true" : undefined}
                className={cls("card w-full p-3.5 text-left transition", sel?.id === a.id ? "border-teal-400 ring-1 ring-teal-400/20" : "hover:border-ink-600")}>
                <div className="flex items-center justify-between gap-2"><span className="mono truncate text-mist-500">{a.id}</span><StatusBadge status={a.status} /></div>
                <div className="mt-1.5 line-clamp-2 text-sm font-medium text-mist-100">{a.request.text}</div>
                <div className="mt-1.5 flex flex-wrap gap-1">{a.reasons.map((r: any) => <Badge key={r.code} tone="amber">{r.code.replaceAll("_", " ").toLowerCase()}</Badge>)}</div>
                <div className="mt-1.5 text-[11px] text-mist-500">{a.request.created_by} · {dt(a.requested_at)}</div>
              </button>
            ))}
          </nav>

          {sel && c && (
            <div className="min-w-0 space-y-5">
              {/* Decision first: what is being decided, why, and the action. */}
              <section aria-labelledby="decision-title" className="card overflow-hidden">
                <div className="flex flex-wrap items-start justify-between gap-4 px-5 pt-5">
                  <div className="min-w-0">
                    <div className="mono text-mist-500">{sel.id}</div>
                    <h2 id="decision-title" className="mt-1 text-base font-semibold text-mist-100">{c.product.name}</h2>
                    <div className="mt-0.5 text-sm text-mist-400">
                      {c.quantity_sales_units != null ? `${num(c.quantity_sales_units)} × ${c.product.sales_unit}` : "Quantity unknown"} · {c.total_excl_tax != null ? `${money(c.total_excl_tax, c.currency)} excl. VAT` : "price not verified"} · delivery {day(sel.request.delivery_date)}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5"><StatusBadge status={c.price?.status} label={`Price ${c.price?.status === "VERIFIED" ? "verified" : (c.price?.status || "unknown").toLowerCase()}`} /><AvailabilityBadge av={c.availability} /></div>
                </div>
                <div className="mt-4 space-y-1.5 px-5">
                  {sel.reasons.map((r: any) => (
                    <div key={r.code} className="flex items-start gap-2 text-sm"><StatusBadge status={r.outcome} label={r.code.replaceAll("_", " ").toLowerCase()} /><span className="text-mist-300">{r.message}</span></div>
                  ))}
                </div>
                <div className="mt-4 border-t border-ink-700 bg-ink-900 px-5 py-4">
                  {isPending ? (
                    <>
                      <label htmlFor="decision-reason" className="label">Decision reason <span className="normal-case tracking-normal text-mist-500">(recorded in the audit trail)</span></label>
                      <textarea id="decision-reason" aria-label="Decision reason" className="input mt-1.5 min-h-[64px]" placeholder="Optional comment" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} disabled={!canDecide} />
                      {!can("approve") && <p className="mt-2 text-xs text-mist-500">Your role ({user?.role}) can view this approval but cannot decide it.</p>}
                      {ownRequest && <p className="mt-2 text-xs text-amber-200">Separation of duties: you created this request, so another approver must decide.</p>}
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <button className="btn-ok px-4" disabled={busy || !canDecide} onClick={() => act("approve")}><Check className="h-4 w-4" aria-hidden />Approve & revalidate</button>
                        <button className="btn-warn" disabled={busy || !canDecide} onClick={() => act("request-changes")}><MessageSquareWarning className="h-4 w-4" aria-hidden />Request changes</button>
                        <button className="btn-danger" disabled={busy || !canDecide} onClick={() => setConfirmReject(true)}><X className="h-4 w-4" aria-hidden />Reject</button>
                      </div>
                      <p className="mt-3 text-xs text-mist-500">Approving re-checks live price and availability first. If nothing changed, AIMO creates an <b className="font-medium text-mist-300">internal proposal that is not submitted to the supplier</b>. No order is placed.</p>
                    </>
                  ) : (
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
                      <span className="flex items-center gap-2">Decision <StatusBadge status={sel.status} /></span>
                      <span className="text-mist-400">by <span className="text-mist-200">{sel.decided_by_name || sel.decided_by || "—"}</span> · {dt(sel.decided_at)}</span>
                      {sel.decision_reason && <span className="text-mist-400">“{sel.decision_reason}”</span>}
                    </div>
                  )}
                </div>
              </section>

              <Tabs label="Approval details" value={tab} onChange={setTab} tabs={[
                { value: "summary", label: "Summary" }, { value: "evidence", label: "Evidence & score" },
                { value: "constraints", label: "Request & lifecycle" }, { value: "history", label: "Decision history" },
              ]} />

              {tab === "summary" && (
                <div className="grid gap-5 xl:grid-cols-2">
                  <Card title="Commercial summary">
                    <KV k="Packaging" v={c.packaging?.description || c.product.sales_unit} />
                    <KV k="Requested" v={c.quantity?.requested_amount != null ? `${num(c.quantity.requested_amount)} ${c.quantity.requested_unit || ""}` : "Unknown"} />
                    <KV k="Order quantity" v={c.quantity_sales_units != null ? `${num(c.quantity_sales_units)} × ${c.product.sales_unit}` : "Unknown"} />
                    <KV k="Unit conversion" v={<StatusBadge status={c.conversion.status} />} />
                    <KV k="Unit price excl. VAT" v={c.price?.status === "VERIFIED" ? money(c.price.unit_price_excl_tax, c.currency) : <StatusBadge status={c.price?.status} />} />
                    <KV k="Total excl. VAT" v={<span className="font-semibold">{c.total_excl_tax != null ? money(c.total_excl_tax, c.currency) : "Not verified"}</span>} />
                    <KV k="Total incl. VAT" v={c.total_incl_tax != null ? money(c.total_incl_tax, c.currency) : "Not verified"} />
                    <KV k="Price validity" v={c.price?.valid_from ? `${String(c.price.valid_from).slice(0, 10)} → ${String(c.price.valid_to).slice(0, 10)}` : "—"} />
                    {c.source === "DATASET_SUBSTITUTION" && <Badge tone="violet" className="mt-2">Substitute for {c.substitute_for}</Badge>}
                  </Card>
                  <Card title="Availability & risk">
                    <KV k="Availability" v={<AvailabilityBadge av={c.availability} />} />
                    <KV k="Quantity" v={availabilityQuantity(c.availability)} />
                    <KV k="Requested delivery" v={day(sel.request.delivery_date)} />
                    <KV k="Earliest available" v={c.availability?.available_date ? day(c.availability.available_date) : "Unknown"} />
                    <KV k="Delivery reliability" v={c.reliability != null ? `${pct(c.reliability)} (historical)` : <span className="text-amber-200">Unknown</span>} />
                    <KV k="Carbon" v={c.carbon_kg_per_kg != null ? `${num(c.carbon_kg_per_kg, 3)} kg CO₂e/kg (group avg.)` : <span className="text-amber-200">Unknown</span>} />
                    {c.availability?.reason && <p className="mt-2 text-xs text-mist-500">{c.availability.reason}</p>}
                  </Card>
                </div>
              )}

              {tab === "evidence" && (
                <Card title="Why this option, and the evidence behind it">
                  <ul className="space-y-1 text-sm text-mist-300">{(c.reasons || []).map((r: string, i: number) => <li key={i}>• {r}</li>)}</ul>
                  {c.tradeoffs?.length > 0 && <ul className="mt-2 space-y-1 text-sm text-amber-100">{c.tradeoffs.map((r: string, i: number) => <li key={i}>• {r}</li>)}</ul>}
                  <div className="mt-4"><ScoreBreakdown c={c} /></div>
                  <div className="label mb-2 mt-5">Evidence</div>
                  <EvidenceList evidence={c.evidence} />
                </Card>
              )}

              {tab === "constraints" && (
                <div className="space-y-5">
                  <Card title="Original request" actions={<Link className="link text-xs" href={`/workspace?request=${sel.request_id}`}>Open full analysis</Link>}>
                    <p className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm text-mist-200">“{sel.request.text}”</p>
                    <div className="mt-2 text-xs text-mist-500">Requested by {sel.request.created_by} via {sel.request.channel} · policy v{sel.request.policy_version}</div>
                    <div className="mt-4"><IntentPanel intent={sel.request.intent} /></div>
                  </Card>
                  <Card title="Lifecycle"><Lifecycle stages={sel.lifecycle} /><div className="mt-3"><NotAnOrderNote /></div></Card>
                </div>
              )}

              {tab === "history" && (
                <Card title="Decision history">
                  <KV k="Requested" v={dt(sel.requested_at)} />
                  <KV k="Decision" v={<StatusBadge status={sel.status} />} />
                  <KV k="Decided by" v={sel.decided_by_name || sel.decided_by || "—"} />
                  <KV k="Decided at" v={dt(sel.decided_at)} />
                  <KV k="Reason" v={sel.decision_reason || "—"} />
                  <KV k="State change" v={`${sel.previous_state || "—"} → ${sel.new_state || "—"}`} />
                  <KV k="Single-use approval consumed" v={sel.consumed ? "Yes" : "No"} />
                  {sel.revalidation?.at && (
                    <div className="mt-3 rounded-lg border border-ink-700 p-3 text-sm">
                      <div className="label mb-1">Live revalidation · {dt(sel.revalidation.at)}</div>
                      <div className="flex flex-wrap gap-2"><StatusBadge status={sel.revalidation.price?.status} label={`Price ${sel.revalidation.price?.status?.toLowerCase()}`} /><AvailabilityBadge av={sel.revalidation.availability} /></div>
                      {sel.revalidation.problems?.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-rose-200">{sel.revalidation.problems.map((p: string, i: number) => <li key={i}>{p}</li>)}</ul>}
                    </div>
                  )}
                </Card>
              )}
            </div>
          )}
        </div>
      )}

      <ConfirmDialog open={confirmReject} danger busy={busy} title="Reject this request?" confirmLabel="Confirm rejection"
        onCancel={() => setConfirmReject(false)} onConfirm={() => act("reject")}>
        The request will be closed without a proposal. The decision and your reason are recorded in the audit trail and cannot be undone.
      </ConfirmDialog>
    </div>
  );
}
