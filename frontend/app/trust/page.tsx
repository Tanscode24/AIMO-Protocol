"use client";

import { BadgeCheck, Bot, Fingerprint, Lock, PlugZap, RefreshCw, ScrollText, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge, Card, Disclosure, ErrorBox, KV, PageHeader, Skeleton, StatusBadge, TrustDot, errMsg } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cls, dt, money, num, pct, titleCase } from "@/lib/format";

// Mirrors backend/app/security.py PERMISSIONS (enforced server-side; shown here for transparency only).
const ROLE_MATRIX: [string, string[]][] = [
  ["View records", ["Admin", "Approver", "Buyer", "Viewer", "Agent*"]],
  ["Create procurement requests", ["Admin", "Approver", "Buyer", "Agent*"]],
  ["Approve or reject", ["Admin", "Approver"]],
  ["Edit policy", ["Admin"]],
  ["Manage users & agent keys", ["Admin"]],
  ["Verify audit chain", ["Admin"]],
];
const ROLE_COLS = ["Admin", "Approver", "Buyer", "Viewer", "Agent*"];

function Pillar({ icon: Icon, title, children, href }: { icon: any; title: string; children: React.ReactNode; href?: string }) {
  return (
    <div className="card flex flex-col p-5">
      <div className="flex items-center gap-2.5">
        <span className="rounded-lg bg-ink-800 p-1.5 text-mist-400" aria-hidden><Icon className="h-4 w-4" /></span>
        <h2 className="section-title">{title}</h2>
      </div>
      <div className="mt-3 flex-1 space-y-1 text-sm text-mist-400">{children}</div>
      {href && <Link href={href} className="link mt-3 text-xs">Details →</Link>}
    </div>
  );
}

export default function TrustPage() {
  const { user, can } = useAuth();
  const [t, setT] = useState<any>(null);
  const [policy, setPolicy] = useState<any>(null);
  const [me, setMe] = useState<any>(null);
  const [chain, setChain] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => api("/api/trust").then((x) => { setT(x); setError(null); }).catch((e) => setError(errMsg(e)));
  useEffect(() => {
    load();
    api("/api/policies/active").then(setPolicy).catch(() => {});
    api("/api/auth/me").then(setMe).catch(() => {});
  }, []);

  async function ping() {
    setBusy(true); setError(null);
    try { setT(await api("/api/trust/ping", { method: "POST" })); } catch (e: any) { setError(errMsg(e)); } finally { setBusy(false); }
  }
  async function verify() {
    setVerifying(true);
    try { setChain(await api("/api/admin/audit/verify")); } catch (e: any) { setError(errMsg(e)); } finally { setVerifying(false); }
  }

  const conn = t?.api?.connectivity || {};
  const ops = t?.api?.operations || {};
  const states = t?.evidence_states || {};
  const totalEvidence = (states.VERIFIED || 0) + (states.UNKNOWN || 0) + (states.ERROR || 0);

  return (
    <div className="space-y-6">
      <PageHeader title="Trust Center"
        subtitle="Why AIMO can be trusted with autonomous procurement: who can do what, what the agent may do, which rules decide, where every claim comes from, and how the record is protected."
        actions={<button className="btn-ghost" onClick={ping} disabled={busy}><RefreshCw className={cls("h-4 w-4", busy && "animate-spin")} aria-hidden />Ping Valio API</button>} />
      <ErrorBox error={error} onRetry={load} />

      {/* Evidence summary — real counts */}
      <section aria-labelledby="ev-title" className="card p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="ev-title" className="section-title">Supplier & catalogue evidence</h2>
          <span className="text-xs text-mist-500">{t ? `${num(totalEvidence, 0)} recorded claims` : ""}</span>
        </div>
        {!t ? <div className="mt-4 space-y-2"><Skeleton /><Skeleton className="w-2/3" /></div> : <>
          <div className="mt-4 flex h-2.5 overflow-hidden rounded-full bg-ink-750" role="img"
            aria-label={`Verified ${states.VERIFIED || 0}, unknown ${states.UNKNOWN || 0}, error ${states.ERROR || 0}`}>
            {totalEvidence > 0 && <>
              <div className="bg-emerald-500" style={{ width: `${((states.VERIFIED || 0) / totalEvidence) * 100}%` }} />
              <div className="bg-amber-400" style={{ width: `${((states.UNKNOWN || 0) / totalEvidence) * 100}%` }} />
              <div className="bg-rose-500" style={{ width: `${((states.ERROR || 0) / totalEvidence) * 100}%` }} />
            </>}
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            {Object.entries(t.verification_model).map(([k, v]: any) => (
              <div key={k}>
                <div className="flex items-center gap-2"><TrustDot state={k} /><span className="text-sm font-medium text-mist-100">{titleCase(k)}</span><span className="ml-auto text-lg font-semibold tabular-nums">{num(states[k] || 0, 0)}</span></div>
                <p className="mt-1 text-xs text-mist-500">{v}</p>
              </div>
            ))}
          </div>
        </>}
      </section>

      <section aria-label="Trust controls" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Pillar icon={Lock} title="Authentication">
          <p>Server-side sessions in an HttpOnly cookie; every request re-checks the session, so sign-out and deactivation take effect immediately.</p>
          <p>Repeated failed sign-ins are throttled per account and per source.</p>
          {me && <p className="pt-1 text-xs">You: <span className="text-mist-300">{me.name}</span> · {me.organization_name} · {me.read_scope === "ORG" ? "org-wide" : "own records"}</p>}
        </Pillar>
        <Pillar icon={ShieldCheck} title="Authorization & roles">
          <p>Role, organization and visibility are read from the database on every request — never from the browser or token claims.</p>
          {me && <div className="flex flex-wrap gap-1 pt-1">{me.permissions.map((p: string) => <Badge key={p} tone="green">{p.replaceAll("_", " ")}</Badge>)}</div>}
        </Pillar>
        <Pillar icon={Bot} title="Agent permissions" href={can("manage_identities") ? "/admin?tab=agents" : undefined}>
          <p>External agents use individually hashed, scoped, expiring keys and a shared rate limit.</p>
          <p>An agent can never approve, edit policy or manage identities — attempts are denied and audited.</p>
          {t && <p className="pt-1 text-xs">Request parser: {t.llm.configured ? t.llm.provider : "deterministic rules (no LLM)"}</p>}
        </Pillar>
        <Pillar icon={SlidersHorizontal} title="Policy enforcement" href="/policies">
          {!policy ? <Skeleton /> : <>
            <KV k="Active policy" v={`${policy.name} v${policy.version}`} />
            <KV k="Autonomous limit" v={money(policy.max_order_value)} />
            <KV k="Unknown allergens" v={titleCase(policy.unknown_allergen_action)} />
            <KV k="Unconfirmed quantity" v={titleCase(policy.terminal_quantity_action)} />
          </>}
        </Pillar>
        <Pillar icon={PlugZap} title="Supplier connection">
          {!t ? <Skeleton /> : <>
            <KV k="Observed status" v={<StatusBadge status={conn.state} />} />
            <KV k="Last successful call" v={conn.last_success_at ? dt(conn.last_success_at) : "Never"} />
            <KV k="Order endpoint" v={<Badge tone="slate">None — proposals only</Badge>} />
            {t.api.ping && <KV k="Last ping" v={<span className="inline-flex items-center gap-2">{t.api.ping.latency_ms != null ? `${t.api.ping.latency_ms} ms` : ""}<StatusBadge status={t.api.ping.status} /></span>} />}
          </>}
        </Pillar>
        <Pillar icon={ScrollText} title="Audit integrity" href="/audit">
          <p>Every decision is an append-only event sealed into a SHA-256 hash chain (tamper-evident, not immutable).</p>
          {can("verify_audit") ? (
            <div className="pt-2">
              <button className="btn-ghost min-h-0 py-1.5 text-xs" onClick={verify} disabled={verifying}><Fingerprint className="h-3.5 w-3.5" aria-hidden />{verifying ? "Verifying…" : "Verify chain integrity"}</button>
              {chain && <div className="mt-2"><StatusBadge status={chain.ok ? "VALID" : "BROKEN"} label={chain.ok ? `Valid · ${chain.events_checked} events` : "Chain broken"} /></div>}
            </div>
          ) : <p className="pt-1 text-xs">Chain verification is available to administrators.</p>}
        </Pillar>
      </section>

      <Disclosure title="Role permissions" hint="Who can do what — enforced by the backend">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead><tr className="table-head"><th className="py-2 pr-3">Capability</th>{ROLE_COLS.map((r) => <th key={r} className="px-2 text-center">{r}</th>)}</tr></thead>
            <tbody>
              {ROLE_MATRIX.map(([cap, roles]) => (
                <tr key={cap} className="border-t border-ink-700">
                  <td className="py-2 pr-3 text-mist-300">{cap}</td>
                  {ROLE_COLS.map((r) => <td key={r} className="px-2 text-center">{roles.includes(r) ? <span className="text-emerald-600" aria-label="allowed">●</span> : <span className="text-ink-500" aria-label="not allowed">–</span>}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-mist-500">* Agents additionally need the matching key scope. You are signed in as {user?.role}.</p>
      </Disclosure>

      <Disclosure title="Verification history" hint="Observed Valio API operations — real calls only, simulated failures excluded">
        {!t ? <Skeleton /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead><tr className="table-head"><th className="py-2 pr-3">Operation</th><th className="px-3">Last status</th><th className="px-3">Last call</th><th className="px-3">Last success</th><th className="px-3">Latency (last / avg)</th><th className="px-3">Outcomes</th></tr></thead>
              <tbody>
                {Object.entries(ops).map(([op, o]: any) => (
                  <tr key={op} className="border-t border-ink-700">
                    <td className="mono py-2.5 pr-3">{op === "ping" ? "GET /api/ping" : op === "price" ? "POST /api/price" : "POST /api/availability"}</td>
                    <td className="px-3">{o.last_status ? <StatusBadge status={o.last_status} /> : <span className="text-mist-500">No calls yet</span>}</td>
                    <td className="mono px-3 text-mist-500">{o.last_at ? dt(o.last_at) : "—"}</td>
                    <td className="mono px-3 text-mist-500">{o.last_success_at ? dt(o.last_success_at) : "never"}</td>
                    <td className="mono px-3">{o.last_latency_ms != null ? `${o.last_latency_ms} ms` : "—"} / {o.avg_latency_ms != null ? `${o.avg_latency_ms} ms` : "—"}</td>
                    <td className="px-3"><div className="flex flex-wrap gap-1">{Object.entries(o.counts).map(([k, v]: any) => <StatusBadge key={k} status={k} label={`${titleCase(k)} ${v}`} />)}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-mist-500">Base URL <span className="mono">{t.api.base_url}</span> · auth {t.api.auth} · simulated failures excluded: {t.api.simulated_events}</p>
          </div>
        )}
      </Disclosure>

      <Disclosure title="Data sources & provenance" hint="Where catalogue, reliability, carbon and live data come from">
        {!t ? <Skeleton /> : (
          <ul className="divide-y divide-ink-700">
            {t.sources.map((s: any) => (
              <li key={s.name} className="grid gap-2 py-3 text-sm md:grid-cols-[minmax(0,1fr)_200px] md:items-center">
                <div className="min-w-0">
                  <div className="font-medium text-mist-100">{s.name}</div>
                  <div className="mono truncate text-mist-500">{s.file}</div>
                  <div className="text-xs text-mist-500">{s.granularity}{s.records != null ? ` · ${num(s.records, 0)} records` : ""}{s.validation ? ` · ${s.validation}` : ""}</div>
                </div>
                {s.coverage != null ? (
                  <div className="flex items-center gap-2"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-750"><div className="h-full rounded-full bg-teal-400" style={{ width: `${s.coverage * 100}%` }} /></div><span className="w-14 text-right text-xs tabular-nums text-mist-400">{pct(s.coverage, 1)}</span></div>
                ) : <span className="text-xs text-mist-500">Live, queried per request</span>}
              </li>
            ))}
          </ul>
        )}
      </Disclosure>

      <Disclosure title="Recent unknown or failed claims" hint="Shown so that uncertainty is never hidden">
        {!t ? <Skeleton /> : t.unverified_examples.length === 0 ? <div className="text-sm text-mist-400">None recorded yet.</div> : (
          <ul className="divide-y divide-ink-700">
            {t.unverified_examples.map((e: any, i: number) => (
              <li key={i} className="flex flex-wrap items-start gap-3 py-2.5 text-sm">
                <TrustDot state={e.state} />
                <span className="w-44 font-medium">{titleCase(e.claim)}</span>
                <span className="mono text-mist-500">{e.product_id}</span>
                <span className="min-w-0 flex-1 basis-60 text-xs text-mist-500">{e.detail} · {e.source}</span>
              </li>
            ))}
          </ul>
        )}
      </Disclosure>

      <p className="flex items-center gap-2 text-xs text-mist-500"><BadgeCheck className="h-3.5 w-3.5" aria-hidden />Unknown stays unknown: missing data is never shown as zero, safe or confirmed.</p>
    </div>
  );
}
