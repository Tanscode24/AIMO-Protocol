"use client";

import { Ban, Copy, KeyRound, Plus, RefreshCw, UserPlus, Users } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ROLE_LABEL } from "@/components/AppShell";
import { Badge, Card, ConfirmDialog, Empty, ErrorBox, Notice, PageHeader, SkeletonRows, StatusBadge, Tabs, errMsg } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dt } from "@/lib/format";

const SCOPES = [
  { id: "catalogue:read", label: "Search catalogue" },
  { id: "procure:create", label: "Submit procurement requests" },
  { id: "procure:read", label: "Read own decisions" },
  { id: "live:check", label: "Live price/availability checks" },
];

type Pending = { title: string; body: string; label: string; run: () => Promise<any> } | null;

function Admin() {
  const { can, user } = useAuth();
  const params = useSearchParams();
  const router = useRouter();
  const tab = (params.get("tab") === "agents" ? "agents" : "users") as "users" | "agents";
  const [agents, setAgents] = useState<any[] | null>(null);
  const [users, setUsers] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [newAgent, setNewAgent] = useState("");
  const [scopes, setScopes] = useState<Record<string, string[]>>({});
  const [nu, setNu] = useState({ email: "", name: "", role: "BUYER", read_scope: "OWN", password: "" });
  const [pending, setPending] = useState<Pending>(null);
  const [copied, setCopied] = useState(false);

  const load = () => {
    setError(null);
    api<any[]>("/api/admin/agents").then(setAgents).catch((e) => setError(errMsg(e)));
    api<any[]>("/api/admin/users").then(setUsers).catch((e) => setError(errMsg(e)));
  };
  useEffect(() => { if (can("manage_identities")) load(); }, [can]);

  async function act(fn: () => Promise<any>) {
    setBusy(true); setError(null);
    try { await fn(); load(); } catch (e: any) { setError(errMsg(e)); } finally { setBusy(false); setPending(null); }
  }

  if (!can("manage_identities")) {
    return <Empty title="Administrators only" icon={<KeyRound className="h-5 w-5" />}>Users, agent identities and API keys can only be managed by an administrator of {user?.organization_name || "your organization"}. The backend enforces this independently of this page.</Empty>;
  }

  return (
    <div>
      <PageHeader title={tab === "agents" ? "Agent Gateway" : "Users & Roles"}
        subtitle={tab === "agents"
          ? "Machine identities for external purchasing agents. Each agent has its own revocable, expiring, least-privilege API keys; only a hash of each key is stored."
          : `People of ${user?.organization_name || "your organization"}, their role and what they may see.`} />
      <ErrorBox error={error} onRetry={load} />
      <Tabs className="mb-5" label="Administration sections" value={tab} onChange={(v) => router.replace(`/admin?tab=${v}`, { scroll: false })} tabs={[
        { value: "users", label: "Users & Roles", count: users?.length }, { value: "agents", label: "Agent Gateway", count: agents?.length },
      ]} />

      {issued && (
        <Notice tone="success" role="alert" className="mb-5" title={`New API key for ${issued.agent} — copy it now, it will not be shown again`}>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <code className="mono min-w-0 flex-1 overflow-x-auto rounded-md border border-ink-700 bg-white px-2 py-1.5 text-mist-100">{issued.api_key}</code>
            <button className="btn-ghost min-h-0 py-1.5 text-xs" onClick={() => { navigator.clipboard?.writeText(issued.api_key); setCopied(true); }} aria-label="Copy key"><Copy className="h-3.5 w-3.5" aria-hidden />{copied ? "Copied" : "Copy"}</button>
            <button className="btn-quiet min-h-0 py-1.5 text-xs" onClick={() => { setIssued(null); setCopied(false); }}>Done</button>
          </div>
          <div className="mt-1 text-xs">Scopes: {issued.scopes.join(", ")} · expires {dt(issued.expires_at)}. Send it as the <span className="mono">X-Agent-Key</span> header.</div>
        </Notice>
      )}

      {tab === "agents" && (
        <Card title="Agent identities" icon={<KeyRound className="h-4 w-4" />}
          actions={<form className="flex w-full gap-2 sm:w-auto" onSubmit={(e) => { e.preventDefault(); if (newAgent.trim()) act(async () => { await api("/api/admin/agents", { method: "POST", json: { name: newAgent.trim() } }); setNewAgent(""); }); }}>
            <input className="input py-1.5 sm:w-52" aria-label="New agent name" placeholder="New agent name" value={newAgent} onChange={(e) => setNewAgent(e.target.value)} />
            <button className="btn-primary min-h-0 py-1.5 text-xs" disabled={busy || !newAgent.trim()}><Plus className="h-3.5 w-3.5" aria-hidden />Create</button>
          </form>}>
          {!agents ? <SkeletonRows /> : agents.length === 0 ? <Empty title="No agents" /> : (
            <div className="space-y-4">
              {agents.map((a) => {
                const sel = scopes[a.id] || ["catalogue:read", "procure:create", "procure:read"];
                return (
                  <div key={a.id} className="rounded-xl border border-ink-700 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0"><div className="font-semibold">{a.name}</div><div className="mono truncate text-mist-500">{a.id} · {a.organization_id}</div></div>
                      <div className="flex items-center gap-2">
                        <StatusBadge status={a.active ? "ACTIVE" : "REVOKED"} label={a.active ? "Active" : "Disabled"} />
                        <button className="btn-ghost min-h-0 py-1 text-xs" disabled={busy}
                          onClick={() => a.active
                            ? setPending({ title: `Disable ${a.name}?`, body: "All of this agent's keys stop working immediately until it is enabled again. The change is audited.", label: "Disable agent", run: () => api(`/api/admin/agents/${a.id}`, { method: "PATCH", json: { active: false } }) })
                            : act(() => api(`/api/admin/agents/${a.id}`, { method: "PATCH", json: { active: true } }))}>
                          {a.active ? "Disable agent" : "Enable agent"}
                        </button>
                      </div>
                    </div>
                    <fieldset className="mt-4">
                      <legend className="label mb-2">Scopes for a new key</legend>
                      <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm">
                        {SCOPES.map((s) => (
                          <label key={s.id} className="flex items-center gap-2 text-mist-300">
                            <input type="checkbox" className="h-4 w-4 accent-indigo-600" checked={sel.includes(s.id)}
                              onChange={(e) => setScopes({ ...scopes, [a.id]: e.target.checked ? [...sel, s.id] : sel.filter((x) => x !== s.id) })} />
                            {s.label}
                          </label>
                        ))}
                      </div>
                    </fieldset>
                    <button className="btn-ghost mt-3 min-h-0 py-1.5 text-xs" disabled={busy || sel.length === 0}
                      onClick={() => act(async () => { const k = await api(`/api/admin/agents/${a.id}/keys`, { method: "POST", json: { scopes: sel, expires_in_days: 90 } }); setIssued({ ...k, agent: a.name }); })}>
                      <KeyRound className="h-3.5 w-3.5" aria-hidden />Issue key (90 days)
                    </button>
                    {a.keys.length > 0 && (
                      <div className="mt-4 overflow-x-auto">
                        <table className="w-full min-w-[640px] text-xs">
                          <thead><tr className="table-head"><th className="py-2">Key</th><th>Scopes</th><th>Expires</th><th>Last used</th><th>Status</th><th /></tr></thead>
                          <tbody>
                            {a.keys.map((k: any) => (
                              <tr key={k.id} className="border-t border-ink-700">
                                <td className="mono py-2">{k.prefix.startsWith("bootstrap") ? "bootstrap key" : `aimo_ak_${k.prefix}_…`}</td>
                                <td><div className="flex flex-wrap gap-1">{k.scopes.map((s: string) => <Badge key={s}>{s}</Badge>)}</div></td>
                                <td className="mono">{k.expires_at ? dt(k.expires_at) : "never"}</td>
                                <td className="mono">{k.last_used_at ? dt(k.last_used_at) : "never"}</td>
                                <td><StatusBadge status={k.status} /></td>
                                <td className="space-x-1 whitespace-nowrap text-right">
                                  {k.status === "ACTIVE" && <>
                                    <button className="btn-ghost min-h-0 px-2 py-1 text-[11px]" disabled={busy} title="Issue a replacement with the same scopes and revoke this key"
                                      onClick={() => act(async () => { const n = await api(`/api/admin/agents/${a.id}/keys/${k.id}/rotate`, { method: "POST" }); setIssued({ ...n, agent: a.name }); })}><RefreshCw className="h-3 w-3" aria-hidden />Rotate</button>
                                    <button className="btn-danger min-h-0 px-2 py-1 text-[11px]" disabled={busy}
                                      onClick={() => setPending({ title: "Revoke this API key?", body: "Requests using this key will be rejected immediately. This cannot be undone — issue a new key if the agent still needs access.", label: "Revoke key", run: () => api(`/api/admin/agents/${a.id}/keys/${k.id}/revoke`, { method: "POST" }) })}>
                                      <Ban className="h-3 w-3" aria-hidden />Revoke</button>
                                  </>}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      )}

      {tab === "users" && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Card title="Team" icon={<Users className="h-4 w-4" />} pad={false}>
            {!users ? <div className="p-5"><SkeletonRows /></div> : (
              <ul className="divide-y divide-ink-700">
                {users.map((u) => (
                  <li key={u.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                    <div className="min-w-0 flex-1 basis-56"><div className="truncate font-medium">{u.name}{!u.active && <span className="ml-2 text-xs font-normal text-mist-500">(inactive)</span>}</div><div className="mono truncate text-mist-500">{u.email}</div></div>
                    <Badge tone="teal">{ROLE_LABEL[u.role] || u.role}</Badge>
                    <select aria-label={`Visibility for ${u.email}`} className="input w-32 py-1 text-xs" value={u.read_scope} disabled={busy || u.role === "BUYER"}
                      onChange={(e) => act(() => api(`/api/admin/users/${u.id}`, { method: "PATCH", json: { read_scope: e.target.value } }))}>
                      <option value="OWN">Own records</option><option value="ORG">Org-wide</option>
                    </select>
                    {u.id !== user?.id ? (
                      <button className="btn-ghost min-h-0 px-2.5 py-1 text-xs" disabled={busy}
                        onClick={() => u.active
                          ? setPending({ title: `Deactivate ${u.name}?`, body: "All of their active sessions end immediately and they can no longer sign in. You can reactivate them later; old sessions are not restored.", label: "Deactivate user", run: () => api(`/api/admin/users/${u.id}`, { method: "PATCH", json: { active: false } }) })
                          : act(() => api(`/api/admin/users/${u.id}`, { method: "PATCH", json: { active: true } }))}>
                        {u.active ? "Deactivate" : "Activate"}
                      </button>
                    ) : <span className="w-[84px] text-right text-xs text-mist-500">You</span>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Add user" icon={<UserPlus className="h-4 w-4" />}>
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); act(async () => { await api("/api/admin/users", { method: "POST", json: nu }); setNu({ email: "", name: "", role: "BUYER", read_scope: "OWN", password: "" }); }); }}>
              <div><label className="label" htmlFor="nu-email">Email</label><input id="nu-email" type="email" className="input mt-1" aria-label="Email" value={nu.email} onChange={(e) => setNu({ ...nu, email: e.target.value })} /></div>
              <div><label className="label" htmlFor="nu-name">Full name</label><input id="nu-name" className="input mt-1" aria-label="Full name" value={nu.name} onChange={(e) => setNu({ ...nu, name: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-2">
                <div><label className="label" htmlFor="nu-role">Role</label>
                  <select id="nu-role" className="input mt-1" aria-label="Role" value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value, read_scope: e.target.value === "BUYER" ? "OWN" : nu.read_scope })}>
                    <option value="BUYER">Buyer</option><option value="PROCUREMENT_MANAGER">Approver</option><option value="VIEWER">Viewer</option><option value="ADMIN">Admin</option>
                  </select></div>
                <div><label className="label" htmlFor="nu-scope">Visibility</label>
                  <select id="nu-scope" className="input mt-1" aria-label="Visibility" value={nu.read_scope} disabled={nu.role === "BUYER"} onChange={(e) => setNu({ ...nu, read_scope: e.target.value })}>
                    <option value="OWN">Own records</option><option value="ORG">Org-wide</option>
                  </select></div>
              </div>
              <div><label className="label" htmlFor="nu-pw">Initial password</label>
                <input id="nu-pw" className="input mt-1" type="password" aria-label="Initial password" autoComplete="new-password" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} aria-describedby="nu-pw-help" />
                <p id="nu-pw-help" className="mt-1 text-xs text-mist-500">At least 10 characters. Buyers always see only their own records.</p></div>
              <button className="btn-primary w-full" disabled={busy || !nu.email || !nu.name || nu.password.length < 10}>Create user</button>
            </form>
          </Card>
        </div>
      )}

      <ConfirmDialog open={!!pending} danger busy={busy} title={pending?.title || ""} confirmLabel={pending?.label || "Confirm"}
        onCancel={() => setPending(null)} onConfirm={() => pending && act(pending.run)}>{pending?.body}</ConfirmDialog>
    </div>
  );
}

export default function AdminPage() {
  return <Suspense fallback={null}><Admin /></Suspense>;
}
