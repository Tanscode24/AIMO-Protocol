"use client";

import { Lock, Save, SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, ErrorBox, PageHeader, Spinner, errMsg } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dt } from "@/lib/format";

type P = Record<string, any>;

export default function PoliciesPage() {
  const { can } = useAuth();
  const [p, setP] = useState<P | null>(null);
  const [form, setForm] = useState<P>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const editable = can("edit_policy");

  useEffect(() => {
    load();
  }, []);

  function load() {
    api("/api/policies/active").then((x) => { setP(x); setForm(toForm(x)); setError(null); }).catch((e) => setError(errMsg(e)));
  }

  const set = (k: string, v: any) => setForm((f) => ({ ...f, [k]: v }));

  async function save() {
    if (!p) return;
    setBusy(true); setError(null); setSaved(null);
    const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
    const body: P = {
      name: form.name, max_order_value: Number(form.max_order_value),
      auto_approval_enabled: form.auto_approval_enabled, require_human_approval_above_limit: form.require_human_approval_above_limit,
      allowed_categories: list(form.allowed_categories), blocked_categories: list(form.blocked_categories), blocked_products: list(form.blocked_products),
      allow_substitutions: form.allow_substitutions, substitution_requires_approval: form.substitution_requires_approval,
      unknown_allergen_action: form.unknown_allergen_action, unverified_price_action: form.unverified_price_action,
      unverified_availability_action: form.unverified_availability_action, terminal_quantity_action: form.terminal_quantity_action,
      unverified_conversion_action: form.unverified_conversion_action, allow_late_delivery: form.allow_late_delivery,
      duplicate_window_minutes: Number(form.duplicate_window_minutes),
      weight_price: Number(form.weight_price), weight_reliability: Number(form.weight_reliability),
      weight_sustainability: Number(form.weight_sustainability), weight_match: Number(form.weight_match),
    };
    if (form.max_quantity === "" || form.max_quantity === null) body.clear_max_quantity = true; else body.max_quantity = Number(form.max_quantity);
    try {
      const x = await api(`/api/policies/${p.id}`, { method: "PUT", json: body });
      setP(x); setForm(toForm(x)); setSaved(`Saved as version ${x.version}. Change recorded in the audit log.`);
    } catch (e: any) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  if (!p) return error ? <ErrorBox error={error} onRetry={load} /> : <Spinner label="Loading policy…" />;
  const wsum = ["weight_price", "weight_reliability", "weight_sustainability", "weight_match"].reduce((s, k) => s + Number(form[k] || 0), 0);

  return (
    <div>
      <PageHeader icon={<SlidersHorizontal className="h-5 w-5" />} title="Policy Settings"
        subtitle="Human-defined permissions for the autonomous agent. Evaluated only by the backend policy engine; the agent can never modify its own permissions."
        actions={<button className="btn-primary" onClick={save} disabled={!editable || busy}><Save className="h-4 w-4" />{busy ? "Saving…" : "Save new version"}</button>} />
      {!editable && <div className="mb-4 flex items-center gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100"><Lock className="h-4 w-4" />Read-only: only Administrators can change policies. Attempts by other roles or by the agent are denied and audited.</div>}
      <ErrorBox error={error} />
      {saved && <div className="mb-4 rounded-lg border border-teal-400/30 bg-teal-400/10 px-3 py-2 text-sm text-teal-50">{saved}</div>}
      <div className="mb-4 text-xs text-mist-500">“{p.name}” · version {p.version} · last updated {dt(p.updated_at)} by {p.updated_by}</div>

      <fieldset disabled={!editable} className="grid gap-6 xl:grid-cols-2">
        <Card title="Spend & quantity limits">
          <Row label="Policy name"><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></Row>
          <Row label="Max autonomous order value (EUR, excl. VAT)"><input type="number" min={0} className="input" value={form.max_order_value} onChange={(e) => set("max_order_value", e.target.value)} /></Row>
          <Row label="Max autonomous quantity (sales units, empty = none)"><input type="number" min={0} className="input" value={form.max_quantity} onChange={(e) => set("max_quantity", e.target.value)} /></Row>
          <Toggle label="Autonomous purchasing enabled" hint="Off = every decision needs human approval" value={form.auto_approval_enabled} onChange={(v) => set("auto_approval_enabled", v)} />
          <Toggle label="Above limit: ask a human (instead of block)" value={form.require_human_approval_above_limit} onChange={(v) => set("require_human_approval_above_limit", v)} />
          <Row label="Duplicate window (minutes, 0 = off)"><input type="number" min={0} className="input" value={form.duplicate_window_minutes} onChange={(e) => set("duplicate_window_minutes", e.target.value)} /></Row>
        </Card>

        <Card title="Verification requirements">
          <Action label="Unknown allergen data on an allergen-restricted request" value={form.unknown_allergen_action} onChange={(v) => set("unknown_allergen_action", v)} />
          <Action label="Price not verified by the Valio API" value={form.unverified_price_action} onChange={(v) => set("unverified_price_action", v)} />
          <Action label="Availability not verified (timeout / error / unknown)" value={form.unverified_availability_action} onChange={(v) => set("unverified_availability_action", v)} />
          <Action label="Date OK, quantity unconfirmed (Terminal 999999 or Stock placeholder 9999999999)" value={form.terminal_quantity_action} onChange={(v) => set("terminal_quantity_action", v)} allowAllow />
          <Action label="Unit conversion estimated (not declared in packaging data)" value={form.unverified_conversion_action} onChange={(v) => set("unverified_conversion_action", v)} />
          <Toggle label="Allow late delivery (with approval)" hint="Off = later availableDate is blocked" value={form.allow_late_delivery} onChange={(v) => set("allow_late_delivery", v)} />
          <p className="mt-2 text-[11px] text-mist-500">Unverified prices can never be ALLOWED automatically — only “require approval” or “block”.</p>
        </Card>

        <Card title="Categories, products & substitutions">
          <Row label="Allowed category codes (comma separated, empty = all)"><input className="input mono" value={form.allowed_categories} onChange={(e) => set("allowed_categories", e.target.value)} /></Row>
          <Row label="Blocked category codes"><input className="input mono" value={form.blocked_categories} onChange={(e) => set("blocked_categories", e.target.value)} /></Row>
          <Row label="Blocked product codes"><input className="input mono" value={form.blocked_products} onChange={(e) => set("blocked_products", e.target.value)} /></Row>
          <Toggle label="Allow substitutions" value={form.allow_substitutions} onChange={(v) => set("allow_substitutions", v)} />
          <Toggle label="Substitutions require human approval" value={form.substitution_requires_approval} onChange={(v) => set("substitution_requires_approval", v)} />
        </Card>

        <Card title="Ranking weights">
          {[["weight_price", "Price"], ["weight_reliability", "Delivery reliability"], ["weight_sustainability", "Sustainability"], ["weight_match", "Request match"]].map(([k, l]) => (
            <div key={k} className="mb-3">
              <div className="mb-1 flex justify-between text-sm"><span className="text-mist-300">{l}</span><span className="mono">{wsum > 0 ? ((Number(form[k]) / wsum) * 100).toFixed(0) : 0}%</span></div>
              <input type="range" min={0} max={1} step={0.05} value={form[k]} onChange={(e) => set(k, e.target.value)} className="w-full accent-teal-400" />
            </div>
          ))}
          <p className="text-[11px] text-mist-500">Weights are normalised to 100%. Hard constraints are always applied before ranking and cannot be outweighed.</p>
        </Card>
      </fieldset>
    </div>
  );
}

function toForm(p: P): P {
  return { ...p, max_quantity: p.max_quantity ?? "", allowed_categories: p.allowed_categories.join(", "), blocked_categories: p.blocked_categories.join(", "),
    blocked_products: p.blocked_products.join(", "), weight_price: p.weights.price, weight_reliability: p.weights.reliability,
    weight_sustainability: p.weights.sustainability, weight_match: p.weights.match };
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="mb-3 block"><span className="label">{label}</span><div className="mt-1">{children}</div></label>;
}

function Toggle({ label, hint, value, onChange }: { label: string; hint?: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="mb-3 flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-ink-700 bg-ink-950/40 px-3 py-2">
      <span className="text-sm text-mist-200">{label}{hint && <span className="block text-[11px] text-mist-500">{hint}</span>}</span>
      <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4 accent-teal-400" />
    </label>
  );
}

function Action({ label, value, onChange, allowAllow }: { label: string; value: string; onChange: (v: string) => void; allowAllow?: boolean }) {
  return (
    <Row label={label}>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {allowAllow && <option value="ALLOW">Allow automatically</option>}
        <option value="REQUIRE_APPROVAL">Require human approval</option>
        <option value="BLOCK">Block</option>
      </select>
    </Row>
  );
}
