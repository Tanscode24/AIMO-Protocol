"use client";

import { Bot, ChevronLeft, ChevronRight, Fingerprint, ScrollText, Search, Server, User, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Badge, Card, Drawer, Empty, ErrorBox, JsonView, KV, Notice, PageHeader, SkeletonRows, StatusBadge, errMsg } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cls, dt } from "@/lib/format";

const ACTOR_ICON: Record<string, any> = { USER: User, AGENT: Bot, SYSTEM: Server };

function Audit() {
  const params = useSearchParams();
  const [filters, setFilters] = useState<Record<string, string>>({ request_id: params.get("request_id") || "" });
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<any>(null);
  const [sel, setSel] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const { can } = useAuth();
  const [chain, setChain] = useState<any>(null);
  const [tick, setTick] = useState(0);
  const [verifying, setVerifying] = useState(false);
  async function verify() {
    setVerifying(true);
    try { setChain(await api("/api/admin/audit/verify")); } catch (e: any) { setError(errMsg(e)); } finally { setVerifying(false); }
  }

  useEffect(() => {
    const p = new URLSearchParams({ page: String(page), page_size: "50" });
    Object.entries(filters).forEach(([k, v]) => v && p.set(k, v));
    if (q.trim()) p.set("q", q.trim());
    const t = setTimeout(() => api(`/api/audit?${p}`).then((d) => { setData(d); setError(null); }).catch((e) => setError(errMsg(e))), 200);
    return () => clearTimeout(t);
  }, [filters, q, page, tick]);

  const setF = (k: string, v: string) => { setPage(1); setFilters((f) => ({ ...f, [k]: v })); };
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const anyFilter = q || Object.values(filters).some(Boolean);

  return (
    <div>
      <PageHeader title="Audit Trail"
        subtitle="Append-only record of every meaningful action by people, agents and the system — with decision, reason and evidence references. Secrets are redacted before anything is stored."
        actions={can("verify_audit") ? (
          <button className="btn-ghost" onClick={verify} disabled={verifying}><Fingerprint className="h-4 w-4" aria-hidden />{verifying ? "Verifying…" : "Verify chain integrity"}</button>
        ) : undefined} />
      <ErrorBox error={error} onRetry={() => setTick((x) => x + 1)} />
      {chain && (
        <Notice tone={chain.ok ? "success" : "danger"} className="mb-4" role="status"
          title={<span className="flex flex-wrap items-center gap-2"><StatusBadge status={chain.ok ? "VALID" : "BROKEN"} label={chain.ok ? `Valid · ${chain.events_checked} events` : "Chain broken"} />
            {chain.ok ? <span className="mono font-normal">head {chain.head_hash.slice(0, 16)}…</span> : <span>{chain.problem} (event {chain.first_invalid_event})</span>}</span>}>
          <span className="text-xs">{chain.guarantee}</span>
        </Notice>
      )}

      <div className="card p-4">
        <div className="grid gap-2 md:grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-mist-500" aria-hidden />
            <input className="input pl-9" aria-label="Search audit reasons" placeholder="Search reasons…" value={q} onChange={(e) => { setPage(1); setQ(e.target.value); }} />
          </div>
          <select className="input" aria-label="Action filter" value={filters.action || ""} onChange={(e) => setF("action", e.target.value)}>
            <option value="">All actions</option>
            {data && Object.entries(data.actions).map(([k, v]: any) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select className="input" aria-label="Actor filter" value={filters.actor_type || ""} onChange={(e) => setF("actor_type", e.target.value)}>
            <option value="">All actors</option><option value="USER">People</option><option value="AGENT">Agents</option><option value="SYSTEM">System</option>
          </select>
          <input className="input mono" aria-label="Request ID filter" placeholder="Request ID" value={filters.request_id || ""} onChange={(e) => setF("request_id", e.target.value.trim())} />
        </div>
        {anyFilter && <button className="btn-quiet mt-2 min-h-0 py-1 text-xs" onClick={() => { setQ(""); setPage(1); setFilters({}); }}><X className="h-3.5 w-3.5" aria-hidden />Clear filters</button>}
      </div>

      {!data ? <div className="card mt-5 p-5">{error ? null : <SkeletonRows rows={6} label="Loading audit events" />}</div> : data.items.length === 0 ? (
        <div className="mt-5"><Empty title="No matching events" icon={<ScrollText className="h-5 w-5" />}>Adjust or clear the filters.</Empty></div>
      ) : (
        <>
          <div className="mb-2 mt-5 flex items-center justify-between text-sm text-mist-400">
            <span>{data.total} events</span>
            <div className="flex items-center gap-1">
              <button className="btn-quiet min-h-0 px-2 py-1" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></button>
              <span className="tabular-nums">Page {page} of {pages}</span>
              <button className="btn-quiet min-h-0 px-2 py-1" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></button>
            </div>
          </div>
          <div className="card overflow-hidden">
            <div className="hidden grid-cols-[130px_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.6fr)_auto] gap-4 border-b border-ink-700 px-5 py-2.5 lg:grid">
              {["Time", "Action", "Actor", "Resource & reason", "Decision"].map((h) => <span key={h} className="table-head border-0">{h}</span>)}
            </div>
            <ul className="divide-y divide-ink-700">
              {data.items.map((e: any) => {
                const Icon = ACTOR_ICON[e.actor_type] || Server;
                return (
                  <li key={e.id}>
                    <button onClick={() => setSel(e)} className="grid w-full grid-cols-1 gap-1 px-5 py-3 text-left text-sm transition hover:bg-ink-900 lg:grid-cols-[130px_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.6fr)_auto] lg:items-center lg:gap-4">
                      <span className="mono text-mist-500">{dt(e.ts)}</span>
                      <span className="font-medium text-mist-100">{e.action_label}</span>
                      <span className="flex min-w-0 items-center gap-1.5 text-mist-300"><Icon className="h-3.5 w-3.5 shrink-0 text-mist-500" aria-label={e.actor_type} /><span className="truncate">{e.actor_name}</span></span>
                      <span className="min-w-0 text-mist-500">
                        {(e.request_id || e.product_id) && <span className="mono mr-1 text-mist-400">{e.request_id || ""}{e.product_id ? ` · ${e.product_id}` : ""}</span>}
                        <span className="line-clamp-1">{e.reason}</span>
                      </span>
                      <span>{e.decision && <StatusBadge status={e.decision} />}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        </>
      )}

      <Drawer open={!!sel} onClose={() => setSel(null)} title={sel?.action_label || "Event"}>
        {sel && (
          <div className="space-y-4">
            <Card>
              <KV k="Timestamp" v={dt(sel.ts)} />
              <KV k="Actor" v={<span className="inline-flex items-center gap-2">{sel.actor_name} <Badge>{sel.actor_type.toLowerCase()}</Badge></span>} />
              <KV k="Action" v={sel.action} mono />
              <KV k="Decision" v={sel.decision ? <StatusBadge status={sel.decision} /> : "—"} />
              <KV k="Request" v={sel.request_id || "—"} mono /><KV k="Product" v={sel.product_id || "—"} mono />
              <KV k="Organization" v={sel.organization_id || "—"} mono />
              <KV k="Policy version" v={sel.policy_version ?? "—"} />
              <KV k="Correlation ID" v={sel.correlation_id || "—"} mono />
              {sel.reason && <p className="mt-3 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-300">{sel.reason}</p>}
            </Card>
            <Card title="Integrity">
              <KV k="Event ID" v={sel.id} mono />
              <KV k="Chain position" v={sel.seq ? `#${sel.seq}` : "Pending seal"} />
              <KV k="Event hash" v={sel.hash ? <span className={cls("mono")}>{sel.hash.slice(0, 24)}…</span> : "—"} />
            </Card>
            <details className="card group"><summary className="cursor-pointer list-none px-5 py-3.5 text-sm font-semibold [&::-webkit-details-marker]:hidden">Evidence references</summary><div className="border-t border-ink-700 p-4"><JsonView data={sel.evidence} /></div></details>
            <details className="card group"><summary className="cursor-pointer list-none px-5 py-3.5 text-sm font-semibold [&::-webkit-details-marker]:hidden">Payload (redacted)</summary><div className="border-t border-ink-700 p-4"><JsonView data={sel.payload} /></div></details>
          </div>
        )}
      </Drawer>
    </div>
  );
}

export default function AuditPage() {
  return <Suspense fallback={null}><Audit /></Suspense>;
}
