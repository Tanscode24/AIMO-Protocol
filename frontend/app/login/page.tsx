"use client";

import { ArrowRight, Eye, ShieldCheck, ShoppingCart, UserCog } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorBox } from "@/components/ui";
import { api } from "@/lib/api";
import { BUILDERS, STARTUP, host } from "@/lib/team";
import { useAuth } from "@/lib/auth";
import { cls } from "@/lib/format";

const ROLE_META: Record<string, { icon: any; label: string; desc: string }> = {
  ADMIN: { icon: UserCog, label: "Administrator", desc: "Users, agents, policy" },
  PROCUREMENT_MANAGER: { icon: ShieldCheck, label: "Approver", desc: "Approves or rejects" },
  BUYER: { icon: ShoppingCart, label: "Buyer", desc: "Creates requests" },
  VIEWER: { icon: Eye, label: "Viewer", desc: "Read-only" },
};

const PRINCIPLES = ["AI recommends.", "Policy controls.", "Humans authorize when required.", "The system records everything."];

export default function LoginPage() {
  const { login, user } = useAuth();
  const router = useRouter();
  const [users, setUsers] = useState<any[]>([]);
  const [email, setEmail] = useState("buyer@aimo.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (user) router.replace("/"); }, [user, router]);
  useEffect(() => { api<any[]>("/api/auth/demo-users").then(setUsers).catch((e) => setError(e.message)); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await login(email, password);
      router.replace("/");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <section className="relative hidden flex-col justify-between overflow-hidden bg-navy-900 p-12 text-white lg:flex">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500 text-xs font-bold">AP</span>
          <span className="text-sm font-semibold tracking-[0.16em]">AIMO PROTOCOL</span>
        </div>
        <div className="max-w-md">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight">The Trusted Operating Layer for Autonomous Commerce.</h1>
          <p className="mt-4 text-sm leading-relaxed text-navy-200">Let AI agents source and price supplies — within rules you set, with human approval where it matters, and evidence for every decision.</p>
          <ol className="mt-10 space-y-3">
            {PRINCIPLES.map((t, i) => (
              <li key={t} className="flex items-center gap-3 text-sm text-navy-100">
                <span className="flex h-6 w-6 items-center justify-center rounded-full border border-navy-600 text-[11px] text-navy-200">{i + 1}</span>{t}
              </li>
            ))}
          </ol>
        </div>
        <div className="space-y-2 text-xs text-navy-300">
          <p>
            Built by{" "}
            {BUILDERS.map((b, i) => (
              <span key={b.linkedin}>
                <a href={b.linkedin} target="_blank" rel="noopener noreferrer" className="font-medium text-navy-100 underline-offset-2 hover:underline">{b.name}</a>
                {i < BUILDERS.length - 1 ? " & " : ""}
              </span>
            ))}
            {" · "}
            <a href={STARTUP.url} target="_blank" rel="noopener noreferrer" className="font-medium text-navy-100 underline-offset-2 hover:underline">{host(STARTUP.url)}</a>
          </p>
          <p>Junction 2026 · Valio Aimo autonomous procurement. Proposals are never submitted to the supplier.</p>
        </div>
      </section>

      <main className="flex items-center justify-center px-4 py-10 sm:px-8">
        <form onSubmit={submit} className="w-full max-w-md space-y-6" aria-labelledby="signin-title">
          <div className="lg:hidden">
            <span className="text-sm font-semibold tracking-[0.16em] text-mist-100">AIMO PROTOCOL</span>
            <p className="mt-1 text-xs text-mist-500">The Trusted Operating Layer for Autonomous Commerce</p>
            <p className="mt-1 text-xs text-mist-500">
              Built by {BUILDERS.map((b, i) => (
                <span key={b.linkedin}><a href={b.linkedin} target="_blank" rel="noopener noreferrer" className="link">{b.name}</a>{i < BUILDERS.length - 1 ? " & " : ""}</span>
              ))} · <a href={STARTUP.url} target="_blank" rel="noopener noreferrer" className="link">{host(STARTUP.url)}</a>
            </p>
          </div>
          <div>
            <h2 id="signin-title" className="text-xl font-semibold tracking-tight">Sign in</h2>
            <p className="mt-1 text-sm text-mist-400">Choose a demo identity. Roles are enforced by the backend.</p>
          </div>

          {users.length > 0 && (
            <fieldset>
              <legend className="label mb-2">Demo identity</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {users.map((u) => {
                  const meta = ROLE_META[u.role] || { icon: UserCog, label: u.role, desc: "" };
                  const Icon = meta.icon;
                  const active = email === u.email;
                  return (
                    <button type="button" key={u.email} onClick={() => setEmail(u.email)} aria-pressed={active}
                      className={cls("rounded-lg border px-3 py-2.5 text-left transition", active ? "border-teal-400 bg-teal-400/[0.06] ring-1 ring-teal-400/20" : "border-ink-700 bg-white hover:border-ink-600")}>
                      <div className="flex items-center gap-2 text-sm font-medium text-mist-100"><Icon className={cls("h-4 w-4", active ? "text-teal-400" : "text-mist-500")} aria-hidden /><span className="truncate">{u.name}</span></div>
                      <div className="mt-0.5 text-[11px] text-mist-500">{meta.label} · {u.organization_id === "org_default" ? "Demo Kitchen" : "Nordic Bistro"} · {meta.desc}</div>
                    </button>
                  );
                })}
              </div>
            </fieldset>
          )}

          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-mist-200" htmlFor="email">Email</label>
              <input id="email" className="input mt-1.5 h-11" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
            </div>
            <div>
              <label className="text-sm font-medium text-mist-200" htmlFor="password">Password</label>
              <input id="password" className="input mt-1.5 h-11" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password" placeholder="DEMO_PASSWORD from backend/.env" />
              <p className="mt-1.5 text-xs text-mist-500">Local default: <span className="mono text-mist-300">aimo-demo-2026</span> (DEMO_PASSWORD in backend/.env)</p>
            </div>
          </div>
          <ErrorBox error={error} />
          <button className="btn-primary h-11 w-full" disabled={busy || !password}>
            {busy ? "Signing in…" : "Enter AIMO PROTOCOL"} <ArrowRight className="h-4 w-4" aria-hidden />
          </button>
        </form>
      </main>
    </div>
  );
}
