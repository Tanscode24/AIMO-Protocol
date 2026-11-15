"use client";

import { ArrowRight, CheckCircle2, ClipboardList, FileCheck2, Plus, ShieldAlert, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { Card, Disclosure, Empty, ErrorBox, KV, PageHeader, Skeleton, Stat, StatusBadge, errMsg } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dt, money, num, pct } from "@/lib/format";

const CHART = { grid: "#EAECF0", axis: "#98A2B3", green: "#12B76A", amber: "#F79009", red: "#F04438" };
const TOOLTIP = {
  contentStyle: { background: "#FFFFFF", border: "1px solid #E4E7EC", borderRadius: 8, fontSize: 12, boxShadow: "0 8px 24px -8px rgba(16,24,40,.15)" },
  labelStyle: { color: "#344054", fontWeight: 600 },
};

const CONN_LABEL: Record<string, string> = {
  CONNECTED: "Connected", CONFIGURED_UNVERIFIED: "Configured, not yet verified", DEGRADED: "Degraded", AUTH_FAILED: "Key rejected", NOT_CONFIGURED: "Not configured (demo mode)",
};

export default function Overview() {
  const { user, can } = useAuth();
  const [a, setA] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [requests, setRequests] = useState<any[] | null>(null);
  const [approvals, setApprovals] = useState<any[] | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [policy, setPolicy] = useState<any>(null);
  const [agents, setAgents] = useState<any[] | null>(null);

  const load = () => {
    api("/api/analytics").then((x) => { setA(x); setError(null); }).catch((e) => setError(errMsg(e)));
    api("/api/procurement/requests?page_size=6").then((r) => setRequests(r.items)).catch(() => setRequests([]));
    api<any[]>("/api/approvals?status=PENDING").then(setApprovals).catch(() => setApprovals([]));
    api("/health").then(setHealth).catch(() => setHealth(null));
    api("/api/policies/active").then(setPolicy).catch(() => setPolicy(null));
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { if (can("manage_identities")) api<any[]>("/api/admin/agents").then(setAgents).catch(() => setAgents(null)); }, [can]);

  if (error && !a) return <ErrorBox error={error} onRetry={load} />;

  const first = user?.name?.split(" ")[0] || "";
  const scopeLabel = a?.scope?.label;

  return (
    <div className="space-y-6">
      <PageHeader title={`Welcome back${first ? `, ${first}` : ""}`}
        subtitle={<>Procurement activity for {user?.organization_name || "your organization"}{scopeLabel ? <> · <span className="text-mist-300">{scopeLabel}</span></> : null}. AI recommends, policy controls, humans authorize — and every step is recorded.</>}
        actions={can("procure") ? <Link href="/workspace" className="btn-primary"><Plus className="h-4 w-4" aria-hidden />New procurement request</Link> : undefined} />

      {/* KPIs — real values from /api/analytics only */}
      <section aria-label="Key figures" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {!a ? Array.from({ length: 4 }).map((_, i) => <div key={i} className="card px-4 py-4"><Skeleton className="w-24" /><Skeleton className="mt-3 h-7 w-12" /></div>) : <>
          <Stat label="Procurement requests" value={num(a.total_requests, 0)} href="/procurement?tab=requests"
            hint={`${num(a.autonomous_decisions, 0)} decided autonomously`} />
          <Stat label="Pending approvals" value={num(a.pending_approvals, 0)} tone="amber" href="/approvals"
            hint={a.approval_rate != null ? `${pct(a.approval_rate, 0)} approved of ${a.approvals_decided} decided` : "None decided yet"} />
          <Stat label="Proposals ready" value={num(a.proposals_ready, 0)} tone="blue" href="/procurement?tab=proposals" hint="Internal · not submitted to supplier" />
          <Stat label="Blocked" value={num(a.blocked_decisions, 0)} tone="red" hint="Policy, allergen, availability or duplicate" />
        </>}
      </section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card title="Recent procurement activity" icon={<ClipboardList className="h-4 w-4" />} pad={false}
          actions={<Link href="/procurement?tab=requests" className="link text-xs">View all</Link>}>
          {!requests ? <div className="p-5 space-y-3"><Skeleton /><Skeleton /><Skeleton /></div> : requests.length === 0 ? (
            <div className="p-5"><Empty title="No procurement requests yet" icon={<Sparkles className="h-5 w-5" />}
              action={can("procure") ? <Link href="/workspace" className="btn-primary">Create the first request</Link> : undefined}>
              Requests you can see will appear here with their policy decision.</Empty></div>
          ) : (
            <ul className="divide-y divide-ink-700">
              {requests.map((r) => (
                <li key={r.id}>
                  <Link href={`/workspace?request=${r.id}`} className="flex items-center gap-3 px-5 py-3 transition hover:bg-ink-900">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-mist-100">{r.text}</div>
                      <div className="mt-0.5 truncate text-xs text-mist-500">{dt(r.created_at)} · {r.created_by}{r.selected_product ? ` · ${r.selected_product.name}` : ""}</div>
                    </div>
                    <div className="hidden text-right text-sm tabular-nums text-mist-300 sm:block">{r.estimated_total_excl_tax != null ? money(r.estimated_total_excl_tax, r.currency) : "—"}</div>
                    <StatusBadge status={r.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-6">
          <Card title="Pending approvals" icon={<ShieldAlert className="h-4 w-4" />} pad={false}
            actions={<Link href="/approvals" className="link text-xs">Open queue</Link>}>
            {!approvals ? <div className="p-5"><Skeleton /></div> : approvals.length === 0 ? (
              <div className="flex items-center gap-2 px-5 py-4 text-sm text-mist-400"><CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />Nothing waiting for a human decision.</div>
            ) : (
              <ul className="divide-y divide-ink-700">
                {approvals.slice(0, 4).map((x) => (
                  <li key={x.id}>
                    <Link href="/approvals" className="block px-5 py-3 transition hover:bg-ink-900">
                      <div className="truncate text-sm text-mist-100">{x.request.text}</div>
                      <div className="mt-1 flex flex-wrap gap-1">{x.reasons.slice(0, 2).map((r: any) => <span key={r.code} className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[11px] font-medium text-amber-100">{r.code.replaceAll("_", " ").toLowerCase()}</span>)}</div>
                    </Link>
                  </li>
                ))}
                {approvals.length > 4 && <li className="px-5 py-2 text-xs text-mist-500">+{approvals.length - 4} more</li>}
              </ul>
            )}
          </Card>

          <Card title="Verification status" icon={<CheckCircle2 className="h-4 w-4" />}>
            {!health ? <Skeleton /> : <>
              <KV k="Valio price & availability API" v={<StatusBadge status={health.connectivity} label={CONN_LABEL[health.connectivity] || health.connectivity} />} />
              <KV k="Last successful live call" v={health.last_successful_call ? dt(health.last_successful_call) : "None yet"} />
              <KV k="Catalogue products" v={num(health.catalogue_products, 0)} />
              <KV k="Request parser" v={health.llm_provider} />
              <p className="mt-2 text-xs text-mist-500">Status reflects the most recent real call, not configuration. <Link className="link" href="/trust">Trust Center</Link></p>
            </>}
          </Card>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Disclosure title="Policy & trust" hint={policy ? `Policy “${policy.name}” v${policy.version} · autonomous limit ${money(policy.max_order_value)}` : "Active policy"}>
          {!policy ? <Skeleton /> : <>
            <KV k="Autonomous spend limit" v={money(policy.max_order_value)} />
            <KV k="Autonomous decisions" v={policy.auto_approval_enabled ? "Enabled within limits" : "Disabled — every request needs a human"} />
            <KV k="Unknown allergen data" v={policy.unknown_allergen_action?.replaceAll("_", " ").toLowerCase()} />
            <KV k="Unconfirmed availability quantity" v={policy.terminal_quantity_action?.replaceAll("_", " ").toLowerCase()} />
            <KV k="Last changed" v={`${dt(policy.updated_at)} by ${policy.updated_by || "—"}`} />
            <Link href="/policies" className="link mt-2 inline-flex items-center gap-1 text-xs">Policy details <ArrowRight className="h-3 w-3" aria-hidden /></Link>
          </>}
        </Disclosure>

        <Disclosure title="Recent audit activity" hint="Last recorded events visible to you">
          {!a ? <Skeleton /> : a.recent_activity.length === 0 ? <div className="text-sm text-mist-400">Nothing recorded yet.</div> : (
            <ul className="space-y-2.5">
              {a.recent_activity.slice(0, 8).map((e: any) => (
                <li key={e.id} className="flex items-start gap-3 text-sm">
                  <span className="mono w-28 shrink-0 pt-0.5 text-mist-500">{dt(e.ts)}</span>
                  <span className="min-w-0 flex-1"><span className="font-medium text-mist-200">{e.action_label}</span>
                    {e.reason && <span className="block truncate text-xs text-mist-500">{e.reason}</span>}</span>
                </li>
              ))}
            </ul>
          )}
          <Link href="/audit" className="link mt-3 inline-flex items-center gap-1 text-xs">Full audit trail <ArrowRight className="h-3 w-3" aria-hidden /></Link>
        </Disclosure>

        {agents && (
          <Disclosure title="Agent gateway" hint={`${agents.filter((x) => x.active).length} active agent identit${agents.filter((x) => x.active).length === 1 ? "y" : "ies"}`}>
            <ul className="space-y-2">
              {agents.map((ag) => (
                <li key={ag.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-mist-200">{ag.name}</span>
                  <span className="flex items-center gap-2 text-xs text-mist-500">{ag.keys.filter((k: any) => k.status === "ACTIVE").length} active key(s)
                    <StatusBadge status={ag.active ? "ACTIVE" : "REVOKED"} label={ag.active ? "Active" : "Disabled"} /></span>
                </li>
              ))}
            </ul>
            <Link href="/admin?tab=agents" className="link mt-3 inline-flex items-center gap-1 text-xs">Manage agents <ArrowRight className="h-3 w-3" aria-hidden /></Link>
          </Disclosure>
        )}

        {a && (
          <Disclosure title="Value, sustainability & data coverage" hint="Secondary metrics, computed from proposals and the catalogue">
            <KV k="Verified proposal value (excl. VAT)" v={a.proposals_with_verified_total > 0 ? money(a.verified_spend_excl_tax) : "No API-verified proposals yet"} />
            <KV k="Verifiable savings" v={a.verifiable_savings != null ? money(a.verifiable_savings) : "—"} />
            <KV k="Avg. historical reliability (selected)" v={a.avg_reliability_selected != null ? pct(a.avg_reliability_selected) : "UNKNOWN"} />
            <KV k="Avg. carbon, group average (selected)" v={a.avg_carbon_selected_kg_per_kg != null ? `${num(a.avg_carbon_selected_kg_per_kg, 2)} kg CO₂e/kg` : "UNKNOWN"} />
            <div className="mt-3 space-y-2">
              {[["Allergen data", a.catalogue.allergen_declared], ["Carbon estimate", a.catalogue.with_carbon], ["Delivery reliability", a.catalogue.with_reliability]].map(([k, v]: any) => {
                const share = a.catalogue.products ? v / a.catalogue.products : 0;
                return (
                  <div key={k}>
                    <div className="mb-1 flex justify-between text-xs"><span className="text-mist-400">{k} coverage</span><span className="tabular-nums text-mist-300">{pct(share, 1)}</span></div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-ink-750"><div className="h-full rounded-full bg-teal-400" style={{ width: `${share * 100}%` }} /></div>
                  </div>
                );
              })}
            </div>
            <p className="mt-3 text-xs text-mist-500">{a.savings_basis}. Uncovered data is shown as UNKNOWN — never as zero or safe.</p>
          </Disclosure>
        )}

        {a && a.activity.length > 1 && (
          <Disclosure title="Decisions per day" hint={`${a.activity.length} days with activity`} className="lg:col-span-2">
            <div className="h-44">
              <ResponsiveContainer>
                <BarChart data={a.activity} maxBarSize={28}>
                  <XAxis dataKey="day" stroke={CHART.axis} fontSize={11} tickLine={false} axisLine={{ stroke: CHART.grid }} />
                  <Tooltip {...TOOLTIP} cursor={{ fill: "rgba(79,70,229,0.05)" }} />
                  <Bar dataKey="allowed" name="Allowed" stackId="a" fill={CHART.green} />
                  <Bar dataKey="approval" name="Needs approval" stackId="a" fill={CHART.amber} />
                  <Bar dataKey="blocked" name="Blocked" stackId="a" fill={CHART.red} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Disclosure>
        )}
      </div>

      <p className="flex items-center gap-2 text-xs text-mist-500">
        <FileCheck2 className="h-3.5 w-3.5" aria-hidden /> Proposals are internal and are never submitted to the supplier — the Valio API has no order endpoint.
      </p>
    </div>
  );
}
