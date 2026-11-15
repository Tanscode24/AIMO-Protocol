"use client";

import { ClipboardList, FileCheck2, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Lifecycle, NotAnOrderNote } from "@/components/Decision";
import {
  Badge, Card, Drawer, Empty, ErrorBox, KV, Notice, PageHeader, SkeletonRows, Spinner, StatusBadge, Tabs, errMsg,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { day, dt, money, num, pct } from "@/lib/format";

const STATUSES = ["PROPOSAL_READY", "PENDING_APPROVAL", "BLOCKED", "DUPLICATE", "REJECTED", "CHANGES_REQUESTED", "REVALIDATION_FAILED"];

function verification(o: any): { status: string; label: string } {
  const ok = o.lines.every((l: any) => l.price_status === "VERIFIED" && l.availability_status === "AVAILABLE");
  return ok ? { status: "VERIFIED", label: "Price & availability verified" } : { status: "UNVERIFIED", label: "Needs human review" };
}

function Procurement() {
  const { can } = useAuth();
  const params = useSearchParams();
  const router = useRouter();
  const tab = (params.get("tab") === "proposals" ? "proposals" : "requests") as "requests" | "proposals";
  const [orders, setOrders] = useState<any>(null);
  const [requests, setRequests] = useState<any>(null);
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadOrders = () => api("/api/orders").then((o) => { setOrders(o); setError(null); }).catch((e) => setError(errMsg(e)));
  const loadRequests = () => api(`/api/procurement/requests?page_size=100${status ? `&status=${status}` : ""}`)
    .then((r) => { setRequests(r); setError(null); }).catch((e) => setError(errMsg(e)));
  useEffect(() => { loadOrders(); }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when the status filter changes
  useEffect(() => { loadRequests(); }, [status]);

  return (
    <div>
      <PageHeader title={tab === "proposals" ? "Proposals" : "Procurement Requests"}
        subtitle={tab === "proposals"
          ? "Internal procurement proposals created after policy approval. They are never submitted to the supplier — the Valio API has no order endpoint."
          : "Every request you may see, with the agent's recommendation and the policy decision."}
        actions={can("procure") ? <Link href="/workspace" className="btn-primary"><Plus className="h-4 w-4" aria-hidden />New request</Link> : undefined} />
      <ErrorBox error={error} onRetry={() => { loadOrders(); loadRequests(); }} />
      <Tabs className="mb-5" label="Procurement views" value={tab} onChange={(v) => router.replace(`/procurement?tab=${v}`, { scroll: false })} tabs={[
        { value: "requests", label: "Requests", count: requests?.total },
        { value: "proposals", label: "Proposals", count: orders?.proposals?.length },
      ]} />

      {tab === "proposals" && (
        !orders ? (error ? null : <div className="card p-5"><SkeletonRows /></div>) : (
          <div className="space-y-4">
            {orders.proposals.length === 0 ? (
              <Empty title="No proposals yet" icon={<FileCheck2 className="h-5 w-5" />}>Proposals appear after an allowed decision, or after an approved request passes live revalidation.</Empty>
            ) : (
              <div className="card overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[820px] text-sm">
                    <thead><tr className="table-head">
                      <th className="px-5 py-3">Proposal</th><th className="px-3">Product</th><th className="px-3 text-right">Quantity</th><th className="px-3 text-right">Est. cost (excl. VAT)</th>
                      <th className="px-3">Verification</th><th className="px-3">Approval</th><th className="px-3">Created</th>
                    </tr></thead>
                    <tbody>
                      {orders.proposals.map((o: any) => {
                        const l = o.lines[0] || {};
                        const v = verification(o);
                        return (
                          <tr key={o.id} className="table-row cursor-pointer" onClick={() => setOpen(o.id)}>
                            <td className="px-5 py-3">
                              <button className="mono text-left text-teal-300 hover:underline" onClick={(e) => { e.stopPropagation(); setOpen(o.id); }}>{o.id}</button>
                              <div className="mt-0.5"><StatusBadge status={o.status} /></div>
                            </td>
                            <td className="max-w-[260px] px-3"><div className="truncate font-medium text-mist-100">{l.name}</div><div className="mono text-mist-500">{l.product_id}{o.lines.length > 1 ? ` +${o.lines.length - 1}` : ""}</div></td>
                            <td className="px-3 text-right tabular-nums">{num(l.quantity)} × {l.sales_unit}</td>
                            <td className="px-3 text-right font-medium tabular-nums">{o.total_excl_tax != null ? money(o.total_excl_tax, o.currency) : <span className="text-amber-200">Not verified</span>}</td>
                            <td className="px-3"><StatusBadge status={v.status} label={v.label} /></td>
                            <td className="px-3"><StatusBadge status={o.approval_status} label={o.approval_status === "NOT_REQUIRED" ? "Not required" : undefined} /></td>
                            <td className="mono whitespace-nowrap px-3 text-mist-500">{dt(o.created_at)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            <Notice tone="neutral" title={`Supplier orders: ${orders.supplier_orders.length}`}>{orders.note}</Notice>
          </div>
        )
      )}

      {tab === "requests" && (
        <Card pad={false} title="All requests" icon={<ClipboardList className="h-4 w-4" />}
          actions={<select aria-label="Filter by status" className="input w-full py-1.5 sm:w-56" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s.replaceAll("_", " ").toLowerCase()}</option>)}
          </select>}>
          {!requests ? <div className="p-5">{error ? null : <SkeletonRows />}</div> : requests.items.length === 0 ? <div className="p-5"><Empty title="No requests" icon={<ClipboardList className="h-5 w-5" />}>Nothing matches this filter yet.</Empty></div> : (
            <ul className="divide-y divide-ink-700">
              {requests.items.map((r: any) => (
                <li key={r.id}>
                  <Link href={`/workspace?request=${r.id}`} className="grid gap-2 px-5 py-3.5 transition hover:bg-ink-900 md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-center md:gap-4">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-mist-100">{r.text}</div>
                      <div className="mt-0.5 truncate text-xs text-mist-500">{dt(r.created_at)} · {r.created_by} · {r.channel === "AGENT_API" ? "via agent API" : "workspace"}{r.selected_product ? ` · ${r.selected_product.name}` : ""}</div>
                      {r.simulate && <Badge tone="red" className="mt-1">simulated {r.simulate}</Badge>}
                    </div>
                    <div className="text-sm tabular-nums text-mist-300 md:text-right">{r.estimated_total_excl_tax != null ? money(r.estimated_total_excl_tax, r.currency) : "—"}</div>
                    <div className="flex flex-wrap gap-1.5 md:justify-end"><StatusBadge status={r.decision} /><StatusBadge status={r.status} /></div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
      <ProposalDrawer id={open} onClose={() => setOpen(null)} />
    </div>
  );
}

function ProposalDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [o, setO] = useState<any>(null);
  useEffect(() => { setO(null); if (id) api(`/api/orders/${id}`).then(setO).catch(() => {}); }, [id]);
  const approvals = o?.audit?.filter((e: any) => ["APPROVAL_REQUESTED", "APPROVAL_GRANTED", "APPROVAL_REJECTED", "REVALIDATION_PERFORMED", "REVALIDATION_FAILED"].includes(e.action)) || [];
  return (
    <Drawer open={!!id} onClose={onClose} wide title={<span>Proposal <span className="mono font-normal text-mist-500">{id}</span></span>}>
      {!o ? <Spinner label="Loading proposal…" /> : (
        <div className="space-y-5">
          <Notice tone="info" title="Not submitted to supplier">{o.disclaimer}</Notice>

          <section className="grid gap-4 md:grid-cols-3">
            {[["Total excl. VAT", o.total_excl_tax != null ? money(o.total_excl_tax, o.currency) : "Not verified"],
              ["Total incl. VAT", o.total_incl_tax != null ? money(o.total_incl_tax, o.currency) : "Not verified"],
              ["Delivery", day(o.delivery_date)]].map(([k, v]) => (
              <div key={k} className="card px-4 py-3"><div className="text-xs text-mist-500">{k}</div><div className="mt-1 text-lg font-semibold tabular-nums">{v}</div></div>
            ))}
          </section>

          <Card title="Commercial summary">
            <KV k="Status" v={<StatusBadge status={o.status} />} />
            <KV k="Submitted to supplier" v={<span className="font-medium">No</span>} />
            <KV k="Policy decision" v={<StatusBadge status={o.policy_decision} />} />
            <KV k="Approval" v={<StatusBadge status={o.approval_status} label={o.approval_status === "NOT_REQUIRED" ? "Not required" : undefined} />} />
          </Card>

          {o.lines.map((l: any) => (
            <Card key={l.product_id} title={l.name} subtitle={<span className="mono">{l.product_id}{l.is_substitute ? ` · substitute for ${l.substitute_for}` : ""}</span>}>
              <div className="grid gap-5 md:grid-cols-2">
                <div>
                  <div className="label mb-1">Price evidence</div>
                  <KV k="Status" v={<StatusBadge status={l.price_status} />} />
                  <KV k="Unit price excl. VAT" v={l.unit_price_excl_tax != null ? money(l.unit_price_excl_tax, l.currency) : "Not verified"} />
                  <KV k="Quantity" v={`${num(l.quantity)} × ${l.sales_unit}`} />
                  <KV k="Line total excl. VAT" v={l.line_total_excl_tax != null ? money(l.line_total_excl_tax, l.currency) : "Not verified"} />
                  <KV k="VAT" v={l.tax_rate != null ? `${l.tax_rate}%` : "—"} />
                  <KV k="Price validity" v={l.price_valid_from ? `${String(l.price_valid_from).slice(0, 10)} → ${String(l.price_valid_to).slice(0, 10)}` : "—"} />
                </div>
                <div>
                  <div className="label mb-1">Availability evidence</div>
                  <KV k="Status" v={<StatusBadge status={l.availability_status} />} />
                  <KV k="Earliest available" v={l.available_date ? `${day(l.available_date)}${l.on_time ? " · on time" : l.on_time === false ? " · late" : ""}` : "Unknown"} />
                  <KV k="Delivery reliability" v={l.reliability != null ? `${pct(l.reliability)} (historical)` : <span className="text-amber-200">Unknown</span>} />
                  <KV k="Carbon (order)" v={l.carbon_total_kg != null ? `${num(l.carbon_total_kg, 1)} kg CO₂e` : <span className="text-amber-200">Unknown</span>} />
                </div>
              </div>
              {l.evidence_refs?.length > 0 && <div className="mt-3 text-[11px] text-mist-500">Evidence references: <span className="mono">{l.evidence_refs.join(", ")}</span></div>}
            </Card>
          ))}

          <Card title="Lifecycle & policy"><Lifecycle stages={o.lifecycle} /><div className="mt-3"><NotAnOrderNote /></div></Card>

          {approvals.length > 0 && (
            <Card title="Approval history">
              <ul className="space-y-2">
                {approvals.map((e: any) => (
                  <li key={e.id} className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="mono text-mist-500">{dt(e.ts)}</span><span className="font-medium">{e.action_label}</span>{e.decision && <StatusBadge status={e.decision} />}
                    <span className="text-mist-400">— {e.actor_name}{e.reason ? `: ${e.reason}` : ""}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card title="Audit timeline">
            <ol className="relative space-y-3 border-l border-ink-600 pl-5">
              {o.audit.map((e: any) => (
                <li key={e.id} className="text-sm">
                  <span className="absolute -left-[4.5px] mt-1.5 h-2 w-2 rounded-full bg-teal-400" aria-hidden />
                  <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{e.action_label}</span>{e.decision && <StatusBadge status={e.decision} />}<span className="mono text-mist-500">{dt(e.ts)}</span></div>
                  <div className="text-xs text-mist-500">{e.actor_name}{e.reason ? ` — ${e.reason}` : ""}</div>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      )}
    </Drawer>
  );
}

export default function ProcurementPage() {
  return <Suspense fallback={null}><Procurement /></Suspense>;
}
