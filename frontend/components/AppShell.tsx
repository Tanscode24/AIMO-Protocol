"use client";

import {
  BadgeCheck, ChevronRight, Info, ChevronsLeft, ChevronsRight, ClipboardList, FileCheck2, KeyRound, LayoutDashboard, LogOut, Menu, Package,
  Plus, ScrollText, ShieldCheck, SlidersHorizontal, Users, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cls } from "@/lib/format";
import { Spinner } from "./ui";

type NavItem = { href: string; label: string; icon: any; permission?: string; badge?: "approvals"; match?: (p: string, q: URLSearchParams) => boolean };

// Only routes that exist. "System Settings" is intentionally absent: there is no such page.
const GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Workspace",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard, match: (p) => p === "/" },
      { href: "/catalogue", label: "Product Discovery", icon: Package },
      { href: "/procurement?tab=requests", label: "Procurement Requests", icon: ClipboardList,
        match: (p, q) => p.startsWith("/workspace") || (p.startsWith("/procurement") && q.get("tab") !== "proposals") },
      { href: "/procurement?tab=proposals", label: "Proposals", icon: FileCheck2, match: (p, q) => p.startsWith("/procurement") && q.get("tab") === "proposals" },
      { href: "/approvals", label: "Approvals", icon: ShieldCheck, badge: "approvals" },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/admin?tab=agents", label: "Agent Gateway", icon: KeyRound, permission: "manage_identities", match: (p, q) => p.startsWith("/admin") && q.get("tab") === "agents" },
      { href: "/trust", label: "Trust Center", icon: BadgeCheck },
      { href: "/audit", label: "Audit Trail", icon: ScrollText },
    ],
  },
  {
    label: "Administration",
    items: [
      { href: "/admin?tab=users", label: "Users & Roles", icon: Users, permission: "manage_identities", match: (p, q) => p.startsWith("/admin") && q.get("tab") !== "agents" },
      { href: "/policies", label: "Policies", icon: SlidersHorizontal },
      { href: "/about", label: "About", icon: Info },
    ],
  },
];

const CRUMB: Record<string, [string, string]> = {
  "/": ["Workspace", "Overview"], "/catalogue": ["Workspace", "Product Discovery"], "/workspace": ["Workspace", "New request"],
  "/procurement": ["Workspace", "Procurement"], "/approvals": ["Workspace", "Approvals"], "/trust": ["Intelligence", "Trust Center"],
  "/audit": ["Intelligence", "Audit Trail"], "/admin": ["Administration", "Users & Agents"], "/policies": ["Administration", "Policies"], "/about": ["AIMO", "About"],
};

const CONNECTIVITY: Record<string, { label: string; tone: "green" | "amber" | "red" }> = {
  CONNECTED: { label: "Valio API live", tone: "green" },
  CONFIGURED_UNVERIFIED: { label: "Valio API not yet verified", tone: "amber" },
  DEGRADED: { label: "Valio API degraded", tone: "red" },
  AUTH_FAILED: { label: "Valio API key rejected", tone: "red" },
  NOT_CONFIGURED: { label: "Demo mode · live API off", tone: "amber" },
};

export const ROLE_LABEL: Record<string, string> = {
  ADMIN: "Administrator", PROCUREMENT_MANAGER: "Approver", BUYER: "Buyer", VIEWER: "Viewer", PROCUREMENT_AGENT: "Agent",
};

const COLLAPSE_KEY = "aimo.sidebar.collapsed";

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((s) => s[0]?.toUpperCase()).join("");
}

function NavLinks({ collapsed, pending, onNavigate }: { collapsed: boolean; pending: number; onNavigate?: () => void }) {
  const { can } = useAuth();
  const pathname = usePathname();
  const params = useSearchParams();
  return (
    <div className="flex-1 space-y-5 overflow-y-auto px-3 py-2">
      {GROUPS.map((g) => {
        const items = g.items.filter((n) => !n.permission || can(n.permission));
        if (items.length === 0) return null;
        return (
          <div key={g.label}>
            {!collapsed && <div className="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-navy-300/70">{g.label}</div>}
            {collapsed && <div className="mx-auto mb-2 h-px w-6 bg-navy-700" aria-hidden />}
            <ul className="space-y-0.5">
              {items.map((n) => {
                const active = n.match ? n.match(pathname, params) : pathname.startsWith(n.href.split("?")[0]);
                const Icon = n.icon;
                return (
                  <li key={n.href}>
                    <Link href={n.href} onClick={onNavigate} aria-current={active ? "page" : undefined} aria-label={collapsed ? n.label : undefined}
                      className={cls("has-tip relative flex items-center gap-3 rounded-lg px-2.5 py-2 text-[13px] font-medium transition",
                        collapsed && "justify-center",
                        active ? "bg-white/10 text-white" : "text-navy-200 hover:bg-white/5 hover:text-white")}>
                      {active && <span className="absolute left-0 top-1.5 h-5 w-0.5 rounded-full bg-indigo-400" aria-hidden />}
                      <Icon className={cls("h-4 w-4 shrink-0", active ? "text-white" : "text-navy-300")} aria-hidden />
                      {!collapsed && <span className="flex-1 truncate">{n.label}</span>}
                      {n.badge === "approvals" && pending > 0 && (
                        <span className={cls("rounded-full bg-amber-400 px-1.5 text-[11px] font-semibold leading-5 text-navy-950", collapsed && "absolute -right-0.5 -top-0.5 px-1 text-[10px] leading-4")}
                          aria-label={`${pending} pending`}>{pending}</span>
                      )}
                      {collapsed && <span className="tip">{n.label}</span>}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, ready, logout, can } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [health, setHealth] = useState<any>(null);
  const [pending, setPending] = useState<number>(0);
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const isLogin = pathname.startsWith("/login");

  useEffect(() => {
    try { setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1"); } catch { /* storage unavailable */ }
  }, []);
  const toggleCollapsed = () => {
    setCollapsed((c) => {
      try { localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1"); } catch { /* ignore */ }
      return !c;
    });
  };

  useEffect(() => {
    if (ready && !user && !isLogin) router.replace("/login");
  }, [ready, user, isLogin, router]);

  useEffect(() => {
    if (!user) return;
    const load = () => {
      api("/health").then(setHealth).catch(() => setHealth({ status: "down" }));
      api<any[]>("/api/approvals?status=PENDING").then((a) => setPending(a.length)).catch(() => setPending(0));
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [user, pathname]);

  useEffect(() => { setOpen(false); setMenu(false); }, [pathname]);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [menu]);

  if (isLogin) return <>{children}</>;
  if (!ready || !user) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner label="Loading AIMO PROTOCOL…" />
      </div>
    );
  }

  const down = health?.status === "down";
  const conn = CONNECTIVITY[health?.connectivity] || CONNECTIVITY.NOT_CONFIGURED;
  const tone = down ? "red" : conn.tone;
  const crumb = CRUMB["/" + (pathname.split("/")[1] || "")] || ["AIMO", ""];
  const role = ROLE_LABEL[user.role] || user.role;
  const scope = user.read_scope === "ORG" ? "org-wide" : "own records";

  const sidebar = (mobile: boolean) => {
    const c = collapsed && !mobile;
    return (
      <nav className="flex h-full flex-col" aria-label="Primary">
        <div className={cls("flex items-center gap-2.5 px-4 pb-4 pt-5", c && "justify-center px-2")}>
          <Link href="/" className="flex min-w-0 items-center gap-2.5" aria-label="AIMO PROTOCOL — Overview">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-500 text-[11px] font-bold tracking-tight text-white">AP</span>
            {!c && (
              <span className="min-w-0">
                <span className="block text-[13px] font-semibold tracking-[0.14em] text-white">AIMO PROTOCOL</span>
                <span className="block truncate text-[10.5px] text-navy-300">Trusted operating layer</span>
              </span>
            )}
          </Link>
          {mobile && <button className="ml-auto rounded-md p-1.5 text-navy-200 hover:bg-white/10" onClick={() => setOpen(false)} aria-label="Close menu"><X className="h-4 w-4" /></button>}
        </div>
        {can("procure") && (
          <div className={cls("px-3 pb-3", c && "px-2")}>
            <Link href="/workspace" className={cls("has-tip relative flex items-center justify-center gap-2 rounded-lg bg-indigo-500 py-2 text-[13px] font-medium text-white transition hover:bg-indigo-400", c ? "px-2" : "px-3")}
              aria-label={c ? "New procurement request" : undefined}>
              <Plus className="h-4 w-4" aria-hidden />{!c && "New request"}
              {c && <span className="tip">New procurement request</span>}
            </Link>
          </div>
        )}
        <Suspense fallback={<div className="flex-1" />}>
          <NavLinks collapsed={c} pending={pending} onNavigate={mobile ? () => setOpen(false) : undefined} />
        </Suspense>
        <div className={cls("border-t border-navy-700 p-3", c && "px-2")}>
          <div className={cls("flex items-center gap-2.5", c && "flex-col")}>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-navy-700 text-[11px] font-semibold text-navy-100" aria-hidden>{initials(user.name)}</span>
            {!c && (
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-white">{user.name}</div>
                <div className="truncate text-[11px] text-navy-300">{role} · {scope}</div>
              </div>
            )}
            <button className="has-tip relative rounded-md p-1.5 text-navy-200 transition hover:bg-white/10 hover:text-white" onClick={logout} aria-label="Sign out">
              <LogOut className="h-4 w-4" aria-hidden />
              <span className="tip">Sign out</span>
            </button>
          </div>
          {!c && <div className="mt-2 truncate text-[11px] text-navy-300">{user.organization_name || user.organization_id}</div>}
          {!mobile && (
            <button onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}
              className={cls("mt-3 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-navy-300 transition hover:bg-white/5 hover:text-white", c && "justify-center")}>
              {collapsed ? <ChevronsRight className="h-4 w-4" aria-hidden /> : <><ChevronsLeft className="h-4 w-4" aria-hidden />Collapse</>}
            </button>
          )}
        </div>
      </nav>
    );
  };

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="skip-link">Skip to main content</a>
      <aside aria-label="Primary navigation"
        className={cls("sticky top-0 hidden h-screen shrink-0 bg-navy-900 transition-[width] duration-200 lg:block", collapsed ? "w-[68px]" : "w-60")}>
        {sidebar(false)}
      </aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-navy-950/50" onClick={() => setOpen(false)} aria-hidden />
          <aside className="relative h-full w-72 max-w-[85vw] bg-navy-900 shadow-pop animate-fadein">{sidebar(true)}</aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-ink-700 bg-white/90 px-4 backdrop-blur lg:px-8">
          <button className="btn-quiet min-h-0 px-2 py-1.5 lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          <nav aria-label="Breadcrumb" className="flex min-w-0 flex-1 items-center gap-1.5 text-sm">
            <span className="hidden text-mist-500 sm:inline">{crumb[0]}</span>
            <ChevronRight className="hidden h-3.5 w-3.5 text-mist-500 sm:inline" aria-hidden />
            <span className="truncate font-medium text-mist-100">{crumb[1]}</span>
          </nav>
          <Link href="/trust" title={health?.connectivity_detail || ""} aria-label={`API status: ${down ? "backend unreachable" : conn.label}`}
            className="hidden items-center gap-2 rounded-full border border-ink-700 bg-white px-2.5 py-1 text-xs font-medium text-mist-300 hover:border-ink-600 md:inline-flex">
            <span aria-hidden className={cls("h-2 w-2 rounded-full", tone === "red" ? "bg-rose-500" : tone === "green" ? "bg-emerald-500 animate-pulseline" : "bg-amber-500")} />
            {down ? "Backend unreachable" : conn.label}
          </Link>
          <div className="relative" ref={menuRef}>
            <button className="flex items-center gap-2 rounded-full border border-ink-700 bg-white py-1 pl-1 pr-2.5 text-sm hover:border-ink-600"
              aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-navy-900 text-[11px] font-semibold text-white" aria-hidden>{initials(user.name)}</span>
              <span className="hidden max-w-[160px] truncate text-[13px] font-medium text-mist-200 sm:inline">{role}</span>
              <span className="sr-only">Account menu for {user.name}</span>
            </button>
            {menu && (
              <div role="menu" className="absolute right-0 mt-2 w-72 rounded-xl border border-ink-700 bg-white p-2 shadow-pop animate-fadein">
                <div className="px-2.5 py-2">
                  <div className="text-sm font-semibold text-mist-100">{user.name}</div>
                  <div className="mono truncate text-mist-500">{user.email}</div>
                  <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                    <span className="rounded-md bg-ink-800 px-1.5 py-0.5 text-mist-300">{role}</span>
                    <span className="rounded-md bg-ink-800 px-1.5 py-0.5 text-mist-300">{user.organization_name || user.organization_id}</span>
                    <span className="rounded-md bg-ink-800 px-1.5 py-0.5 text-mist-300">Visibility: {scope}</span>
                  </div>
                </div>
                <div className="my-1 h-px bg-ink-700" />
                <button role="menuitem" onClick={logout} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-mist-300 hover:bg-ink-800">
                  <LogOut className="h-4 w-4" aria-hidden /> Sign out of AIMO
                </button>
              </div>
            )}
          </div>
        </header>
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1320px] flex-1 px-4 py-6 outline-none sm:px-6 lg:px-8 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
