"use client";

import { AlertTriangle, CheckCircle2, ChevronDown, CircleDashed, CircleHelp, Info, Loader2, RotateCw, Send, X, XCircle } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import { cls, titleCase } from "@/lib/format";

// ------------------------------------------------------------------ status vocabulary
type Tone = "green" | "amber" | "red" | "blue" | "teal" | "slate" | "violet";

const TONES: Record<Tone, string> = {
  green: "border-emerald-600/20 bg-emerald-500/[0.08] text-emerald-200",
  amber: "border-amber-500/25 bg-amber-500/[0.08] text-amber-100",
  red: "border-rose-500/25 bg-rose-500/[0.07] text-rose-200",
  blue: "border-teal-400/20 bg-teal-400/[0.07] text-teal-100",
  teal: "border-teal-400/20 bg-teal-400/[0.07] text-teal-100",
  slate: "border-ink-600 bg-ink-800 text-mist-300",
  violet: "border-violet-500/25 bg-violet-500/[0.07] text-violet-100",
};

export function Badge({ tone = "slate", children, className, title }: { tone?: Tone; children: React.ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cls("inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4", TONES[tone], className)}>
      {children}
    </span>
  );
}

/** Semantic families used across the product:
 *  verified (green) · unverified / requires human review (amber) · blocked / rejected (red) · approved (green)
 *  · not submitted to supplier (indigo) · neutral (slate). */
const STATUS_TONE: Record<string, Tone> = {
  ALLOWED: "green", VERIFIED: "green", AVAILABLE: "green", VERIFIED_SAFE: "green", FREE_FROM: "green", APPROVED: "green",
  PROPOSAL_READY: "teal", PROPOSAL_READY_NOT_SUBMITTED: "teal", OK: "green", PASSED: "green", FULLY_VERIFIED: "green",
  REQUIRES_APPROVAL: "amber", PENDING: "amber", PENDING_APPROVAL: "amber", UNVERIFIED: "amber", UNKNOWN: "amber",
  AVAILABLE_QTY_UNCONFIRMED: "amber", ESTIMATED: "amber", NAME_ONLY: "amber", NOT_DECLARED: "amber", MAY_CONTAIN: "amber",
  NEEDS_VERIFICATION: "amber", LATE: "amber", CHANGES_REQUESTED: "violet", NOT_CONFIGURED: "amber", ANALYZED: "slate",
  BLOCKED: "red", REJECTED: "red", UNAVAILABLE: "red", PARTIAL: "red", API_ERROR: "red", TIMEOUT: "red", ERROR: "red",
  CONFLICT: "red", CONTAINS: "red", VIOLATION: "red", DUPLICATE: "red", REVALIDATION_FAILED: "red",
  APPROVED_REVALIDATION_FAILED: "red", NOT_MET: "red", AUTH_ERROR: "red", SUPERSEDED: "slate", NOT_REQUIRED: "slate",
  DECLARED: "green",
  CONNECTED: "green", DONE: "green", VALID: "green", ACTIVE: "green",
  CONFIGURED_UNVERIFIED: "amber", WAITING: "amber", NOT_REACHED: "slate", RATE_LIMITED: "red",
  DEGRADED: "red", AUTH_FAILED: "red", FAILED: "red", REVOKED: "slate", BROKEN: "red", DENIED: "red",
  NOT_SUPPORTED: "red", NOT_CREATED: "slate", NOT_APPLICABLE: "slate", NOT_STARTED: "slate",
};

const STATUS_LABEL: Record<string, string> = {
  PROPOSAL_READY_NOT_SUBMITTED: "Not submitted to supplier",
  PROPOSAL_READY: "Proposal ready",
  REQUIRES_APPROVAL: "Requires approval",
  PENDING_APPROVAL: "Awaiting approval",
  AVAILABLE_QTY_UNCONFIRMED: "Available Qty Unconfirmed",
  APPROVED_REVALIDATION_FAILED: "Revalidation failed",
  API_ERROR: "API error",
  OK: "OK",
};

export function StatusBadge({ status, label }: { status?: string | null; label?: string }) {
  if (!status) return <Badge tone="slate">—</Badge>;
  const tone = STATUS_TONE[status] || "slate";
  const Icon = tone === "green" ? CheckCircle2 : tone === "teal" ? Send : tone === "red" ? XCircle : tone === "amber" ? AlertTriangle : tone === "slate" ? CircleDashed : CircleHelp;
  return (
    <Badge tone={tone}>
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
      <span className="truncate">{label || STATUS_LABEL[status] || titleCase(status)}</span>
    </Badge>
  );
}

export const STOCK_SENTINEL_LABEL = "NOT CONFIRMED — Human review required";

/** Availability status that never presents a supplier placeholder as confirmed stock. */
export function AvailabilityBadge({ av }: { av?: any }) {
  if (av?.quantity_basis === "UNCONFIRMED_STOCK_SENTINEL") {
    return (
      <Badge tone="amber" title="Valio reported a placeholder Stock quantity (9999999999). AIMO routes this for human review.">
        <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden />
        {STOCK_SENTINEL_LABEL}
      </Badge>
    );
  }
  return <StatusBadge status={av?.status} />;
}

export function TrustDot({ state }: { state: string }) {
  const c = state === "VERIFIED" ? "bg-emerald-500" : state === "UNKNOWN" ? "bg-amber-500" : "bg-rose-500";
  return <span className={cls("inline-block h-2 w-2 shrink-0 rounded-full", c)} title={state} aria-label={state} role="img" />;
}

// ------------------------------------------------------------------ layout primitives
export function Card({ title, icon, actions, children, className, pad = true, subtitle }: {
  title?: React.ReactNode; icon?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string; pad?: boolean;
  subtitle?: React.ReactNode;
}) {
  return (
    <section className={cls("card animate-fadein", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-5 py-3.5">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-semibold text-mist-100">
              {icon && <span className="text-mist-500" aria-hidden>{icon}</span>}
              {title}
            </div>
            {subtitle && <div className="mt-0.5 text-xs text-mist-500">{subtitle}</div>}
          </div>
          {actions}
        </header>
      )}
      <div className={pad ? "card-pad" : ""}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, icon, tone = "teal", href }: {
  label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: React.ReactNode; tone?: "teal" | "amber" | "red" | "blue" | "green"; href?: string;
}) {
  const dot = { teal: "bg-teal-400", amber: "bg-amber-500", red: "bg-rose-500", blue: "bg-teal-400", green: "bg-emerald-500" }[tone];
  const body = (
    <>
      <div className="flex items-center gap-2 text-xs font-medium text-mist-400">
        <span className={cls("h-1.5 w-1.5 rounded-full", dot)} aria-hidden />
        {label}
        {icon && <span className="ml-auto text-mist-500" aria-hidden>{icon}</span>}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight text-mist-100 tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs text-mist-500">{hint}</div>}
    </>
  );
  return href ? (
    <a href={href} className="card block px-4 py-4 transition hover:border-ink-600 hover:shadow-pop">{body}</a>
  ) : (
    <div className="card px-4 py-4">{body}</div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-mist-400" role="status" aria-live="polite">
      <Loader2 className="h-4 w-4 animate-spin text-teal-400" aria-hidden />
      {label}
    </div>
  );
}

/** Loading placeholder. Marked aria-busy; it is not a role=status element so it never blocks "settled" checks. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cls("skeleton h-4", className)} aria-hidden />;
}

export function SkeletonRows({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div aria-busy="true" aria-label={label} className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-4 w-16" />
        </div>
      ))}
    </div>
  );
}

export function Empty({ title, children, icon, action }: { title: string; children?: React.ReactNode; icon?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-ink-600 bg-white px-6 py-10 text-center">
      {icon && <div className="mb-3 rounded-full bg-ink-800 p-2.5 text-mist-500" aria-hidden>{icon}</div>}
      <div className="text-sm font-medium text-mist-200">{title}</div>
      {children && <div className="mt-1 max-w-md text-sm text-mist-500">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string | null; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="mb-4 flex flex-wrap items-start gap-2 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2.5 text-sm text-rose-100">
      <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <span className="flex-1">{error}</span>
      {onRetry && (
        <button className="btn-danger min-h-0 py-1 text-xs" onClick={onRetry}>
          <RotateCw className="h-3.5 w-3.5" aria-hidden /> Retry
        </button>
      )}
    </div>
  );
}

/** Message for an ApiError including its correlation ID (useful when reporting problems). */
export function errMsg(e: any): string {
  return `${e?.message || "Request failed"}${e?.correlationId ? ` (ref ${e.correlationId})` : ""}`;
}

type NoticeTone = "info" | "warn" | "success" | "danger" | "neutral";
const NOTICE: Record<NoticeTone, { box: string; icon: any }> = {
  info: { box: "border-teal-400/20 bg-teal-400/[0.05] text-mist-300", icon: Info },
  warn: { box: "border-amber-500/30 bg-amber-500/[0.07] text-amber-50", icon: AlertTriangle },
  success: { box: "border-emerald-600/25 bg-emerald-500/[0.07] text-emerald-50", icon: CheckCircle2 },
  danger: { box: "border-rose-500/25 bg-rose-500/[0.06] text-rose-50", icon: XCircle },
  neutral: { box: "border-ink-700 bg-ink-900 text-mist-300", icon: Info },
};

export function Notice({ tone = "info", title, children, action, className, role }: {
  tone?: NoticeTone; title?: React.ReactNode; children?: React.ReactNode; action?: React.ReactNode; className?: string; role?: string;
}) {
  const t = NOTICE[tone];
  const Icon = t.icon;
  return (
    <div role={role} className={cls("flex flex-wrap items-start gap-3 rounded-lg border px-4 py-3 text-sm", t.box, className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <div className="font-medium">{title}</div>}
        {children && <div className={cls(title ? "mt-0.5" : "", "opacity-90")}>{children}</div>}
      </div>
      {action}
    </div>
  );
}

/** Progressive disclosure: native <details>, keyboard-accessible by default. */
export function Disclosure({ title, hint, children, defaultOpen, className }: {
  title: React.ReactNode; hint?: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean; className?: string;
}) {
  return (
    <details className={cls("group card", className)} open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-3.5 [&::-webkit-details-marker]:hidden">
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-mist-100">{title}</span>
          {hint && <span className="mt-0.5 block text-xs text-mist-500">{hint}</span>}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-mist-500 transition group-open:rotate-180" aria-hidden />
      </summary>
      <div className="border-t border-ink-700 px-5 py-4">{children}</div>
    </details>
  );
}

export function KV({ k, v, mono }: { k: React.ReactNode; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 text-sm">
      <span className="shrink-0 text-mist-400">{k}</span>
      <span className={cls("min-w-0 break-words text-right text-mist-100", mono && "mono")}>{v ?? "—"}</span>
    </div>
  );
}

export function Drawer({ open, onClose, title, children, wide }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; wide?: boolean;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => prev?.focus?.();
  }, [open]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
      <div className="absolute inset-0 bg-navy-950/30 backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <aside className={cls("relative h-full w-full overflow-y-auto border-l border-ink-700 bg-ink-900 shadow-pop animate-fadein", wide ? "max-w-4xl" : "max-w-2xl")}>
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-ink-700 bg-white/95 px-6 py-4 backdrop-blur">
          <div id="drawer-title" className="min-w-0 text-base font-semibold text-mist-100">{title}</div>
          <button ref={closeRef} className="btn-quiet min-h-0 px-2 py-1.5" onClick={onClose} aria-label="Close panel">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-4 sm:p-6">{children}</div>
      </aside>
    </div>
  );
}

/** Confirmation for consequential actions. Focuses the cancel button; Escape cancels. */
export function ConfirmDialog({ open, title, children, confirmLabel, onConfirm, onCancel, danger, busy }: {
  open: boolean; title: string; children?: React.ReactNode; confirmLabel: string; onConfirm: () => void; onCancel: () => void; danger?: boolean; busy?: boolean;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();
    const h = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="alertdialog" aria-modal="true" aria-labelledby={`${id}-t`} aria-describedby={`${id}-d`}>
      <div className="absolute inset-0 bg-navy-950/40" onClick={onCancel} aria-hidden />
      <div className="relative w-full max-w-md rounded-xl border border-ink-700 bg-white p-5 shadow-pop animate-fadein">
        <h2 id={`${id}-t`} className="text-base font-semibold text-mist-100">{title}</h2>
        <div id={`${id}-d`} className="mt-2 text-sm text-mist-400">{children}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button ref={cancelRef} className="btn-ghost" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className={danger ? "btn-danger-solid" : "btn-primary"} onClick={onConfirm} disabled={busy}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

export function ScoreBar({ value, className }: { value: number; className?: string }) {
  const w = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className={cls("h-1.5 w-full overflow-hidden rounded-full bg-ink-750", className)} role="img" aria-label={`${Math.round(w)}%`}>
      <div className="h-full rounded-full bg-teal-400" style={{ width: `${w}%` }} />
    </div>
  );
}

export function JsonView({ data }: { data: unknown }) {
  return (
    <pre className="mono max-h-[480px] overflow-auto rounded-lg border border-ink-700 bg-ink-900 p-3 text-mist-300">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

export function PageHeader({ title, subtitle, icon: _icon, actions }: { title: string; subtitle?: React.ReactNode; icon?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 max-w-3xl">
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="mt-1 text-sm leading-relaxed text-mist-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) {
  return (
    <div className="inline-flex rounded-lg border border-ink-600 bg-ink-800 p-0.5">
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)} aria-pressed={value === o.value}
          className={cls("rounded-md px-3 py-1.5 text-xs font-medium transition", value === o.value ? "bg-white text-mist-100 shadow-glow" : "text-mist-400 hover:text-mist-100")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Accessible tab list (roving arrow keys). Panels are rendered by the caller. */
export function Tabs<T extends string>({ value, onChange, tabs, label, className }: {
  value: T; onChange: (v: T) => void; tabs: { value: T; label: React.ReactNode; count?: number }[]; label: string; className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div role="tablist" aria-label={label} className={cls("flex gap-1 overflow-x-auto border-b border-ink-700", className)}
      onKeyDown={(e) => {
        const i = tabs.findIndex((t) => t.value === value);
        const next = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : -1;
        if (next >= 0) { e.preventDefault(); onChange(tabs[next].value); refs.current[next]?.focus(); }
      }}>
      {tabs.map((t, i) => {
        const active = t.value === value;
        return (
          <button key={t.value} ref={(el) => { refs.current[i] = el; }} role="tab" aria-selected={active} tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.value)}
            className={cls("-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition",
              active ? "border-teal-400 text-mist-100" : "border-transparent text-mist-400 hover:text-mist-100")}>
            {t.label}
            {t.count != null && <span className={cls("ml-2 rounded-full px-1.5 py-0.5 text-[11px]", active ? "bg-teal-400/10 text-teal-300" : "bg-ink-800 text-mist-400")}>{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Stepper for multi-step forms. */
export function Steps({ steps, current, onSelect }: { steps: string[]; current: number; onSelect?: (i: number) => void }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-2" aria-label="Progress">
      {steps.map((s, i) => {
        const state = i < current ? "done" : i === current ? "current" : "todo";
        return (
          <li key={s} className="flex items-center gap-2">
            <button type="button" disabled={!onSelect || i > current} onClick={() => onSelect?.(i)} aria-current={state === "current" ? "step" : undefined}
              className={cls("flex items-center gap-2 rounded-full py-1 pl-1 pr-3 text-xs font-medium transition disabled:cursor-default",
                state === "current" ? "bg-teal-400/10 text-teal-300" : state === "done" ? "text-mist-300 hover:bg-ink-800" : "text-mist-500")}>
              <span className={cls("flex h-5 w-5 items-center justify-center rounded-full text-[11px]",
                state === "current" ? "bg-teal-400 text-white" : state === "done" ? "bg-emerald-600 text-white" : "border border-ink-600 bg-white text-mist-500")}>
                {state === "done" ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> : i + 1}
              </span>
              {s}
            </button>
            {i < steps.length - 1 && <span className="h-px w-4 bg-ink-600" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}
