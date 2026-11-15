"use client";

import { ArrowLeft, ArrowRight, CheckCircle2, Loader2, Pencil, Play, Plus, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ComparisonView, DecisionBanner, ExcludedList, IntentPanel, RankingWhy, RecommendationCard } from "@/components/Decision";
import { Card, Disclosure, ErrorBox, JsonView, Notice, PageHeader, Segmented, Skeleton, Steps, Tabs, errMsg } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cls, day } from "@/lib/format";

const PLANNED = ["Request received", "Intent extracted", "Catalogue searched", "Units, lot sizes & allergens checked",
  "Products shortlisted", "Prices verified (Valio API)", "Availability verified (Valio API)", "Ranked", "Policy evaluated"];

// Only vocabulary the backend parser understands is offered (verified against app/services/allergens.py).
const UNITS = [
  { value: "kg", label: "Kilograms (kg)", word: "kg" },
  { value: "g", label: "Grams (g)", word: "g" },
  { value: "L", label: "Litres (L)", word: "litres" },
  { value: "packs", label: "Packs (the product's own sales unit)", word: "packs" },
];
const DIETARY = [
  { value: "lactose-free", label: "Lactose-free" }, { value: "vegan", label: "Vegan" }, { value: "vegetarian", label: "Vegetarian" },
];
const ALLERGENS = ["milk", "egg", "gluten", "soy", "peanut", "nut", "fish", "shellfish", "sesame", "mustard", "celery", "lupin", "sulphite"];
const ALLERGEN_LABEL: Record<string, string> = { nut: "tree nuts", sulphite: "sulphites", egg: "eggs", peanut: "peanuts" };
const PRIORITIES = [
  { value: "", label: "Balanced (default weights)" }, { value: "price", label: "Lowest price" },
  { value: "reliability", label: "Delivery reliability" }, { value: "sustainability", label: "Lower carbon" },
];
const STEPS = ["Product", "Quantity & unit", "Delivery date", "Requirements", "Review"];

type Guided = { product: string; qty: string; unit: string; date: string; dietary: string[]; allergens: string[]; priority: string };

function tomorrow(offset = 2) {
  const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10);
}

function composeText(g: Guided): string {
  const u = UNITS.find((x) => x.value === g.unit)?.word || g.unit;
  const diet = g.dietary.length ? `${g.dietary.join(" ")} ` : "";
  let t = `We need ${g.qty} ${u} of ${diet}${g.product.trim()}, delivered by ${g.date}.`;
  if (g.allergens.length) {
    const parts = g.allergens.map((a) => `${a}-free`);
    t += ` It must be ${parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0]}.`;
  }
  if (g.priority) t += ` Prioritise ${g.priority}.`;
  return t;
}

function Workspace() {
  const { can } = useAuth();
  const params = useSearchParams();
  const router = useRouter();
  const [mode, setMode] = useState<"guided" | "text">("guided");
  const [step, setStep] = useState(0);
  const [g, setG] = useState<Guided>({ product: "", qty: "", unit: "kg", date: tomorrow(), dietary: [], allergens: [], priority: "" });
  const [preview, setPreview] = useState<any>(null);
  const [scenarios, setScenarios] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [simulate, setSimulate] = useState<"none" | "timeout">("none");
  const [busy, setBusy] = useState(false);
  const [doc, setDoc] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"recs" | "compare" | "method" | "excluded" | "json">("recs");
  const [showAll, setShowAll] = useState(false);
  const requestId = params.get("request");

  useEffect(() => {
    api<any[]>("/api/procurement/scenarios").then((s) => {
      setScenarios(s);
      if (!requestId) setText((t) => t || s[0]?.text || "");
    }).catch((e) => setError(errMsg(e)));
  }, [requestId]);

  const load = useCallback((id: string) => {
    setBusy(true);
    api(`/api/procurement/requests/${id}`).then((d) => { setDoc(d); setText(d.request_text); })
      .catch((e) => setError(errMsg(e))).finally(() => setBusy(false));
  }, []);

  useEffect(() => { if (requestId) load(requestId); else setDoc(null); }, [requestId, load]);

  // Review step: show how the backend parser actually interprets the composed request.
  useEffect(() => {
    if (mode !== "guided" || step !== 4 || !can("procure")) return;
    setPreview(null);
    api("/api/procurement/parse", { method: "POST", json: { text: composeText(g) } }).then(setPreview).catch((e) => setError(errMsg(e)));
  }, [mode, step, g, can]);

  async function run(confirm = false, body?: string) {
    setBusy(true); setError(null); setDoc(null); setTab("recs");
    try {
      const d = await api("/api/procurement/requests", {
        method: "POST", headers: { "Idempotency-Key": newIdempotencyKey() },
        json: { text: body ?? text, simulate: simulate === "none" ? null : simulate, auto_decide: true, confirm_assumptions: confirm },
      });
      setDoc(d);
      setText(d.request_text);
      router.replace(`/workspace?request=${d.request_id}`, { scroll: false });
    } catch (e: any) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  async function choose(productId: string) {
    if (!doc) return;
    setBusy(true); setError(null);
    try {
      setDoc(await api(`/api/procurement/requests/${doc.request_id}/decide`, {
        method: "POST", headers: { "Idempotency-Key": newIdempotencyKey() }, json: { product_id: productId },
      }));
    } catch (e: any) { setError(errMsg(e)); } finally { setBusy(false); }
  }

  const stepValid = [g.product.trim().length >= 2, Number(g.qty) > 0, !!g.date, true, true][step];
  const finished = doc && ["PROPOSAL_READY", "REJECTED"].includes(doc.status);
  const s = doc?.summary || {};
  const showForm = !doc && !requestId;
  const pipeline = doc?.pipeline?.length ? doc.pipeline : PLANNED.map((label) => ({ stage: label, label, status: "PENDING" }));
  const done = pipeline.filter((p: any) => p.status === "DONE").length;

  return (
    <div>
      <PageHeader title={showForm ? "New procurement request" : "Procurement request"}
        subtitle={showForm ? "Describe what you need. The agent searches the catalogue, verifies live price and availability with Valio, ranks options and submits its recommendation to the policy engine. Nothing is ordered from the supplier."
          : "The agent's recommendation, the policy decision and the evidence behind it."}
        actions={!showForm ? <Link href="/workspace" className="btn-ghost" onClick={() => { setDoc(null); setError(null); }}><Plus className="h-4 w-4" aria-hidden />New request</Link> : undefined} />

      {!can("procure") && showForm && (
        <Notice tone="neutral" className="mb-4" title="Read-only access">Your role can view requests but cannot create them. Sign in as a Buyer, Approver or Administrator.</Notice>
      )}

      {showForm && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
          <Card pad={false}>
            <div className="px-5 pt-4">
              <Tabs label="Request input mode" value={mode} onChange={setMode} tabs={[{ value: "guided", label: "Guided request" }, { value: "text", label: "Describe in your own words" }]} />
            </div>
            {mode === "guided" ? (
              <form className="p-5" onSubmit={(e) => {
                e.preventDefault();
                if (step < 4) { if (stepValid) setStep(step + 1); } else run(preview?.intent?.confirmation_required?.length > 0, composeText(g));
              }}>
                <Steps steps={STEPS} current={step} onSelect={setStep} />
                <div className="mt-6 min-h-[180px]">
                  {step === 0 && (
                    <div className="max-w-lg">
                      <label htmlFor="g-product" className="section-title">What do you need?</label>
                      <p className="mt-1 text-sm text-mist-500">A product type in plain words. The agent searches English, Finnish and Swedish names.</p>
                      <input id="g-product" autoFocus className="input mt-3 h-11" placeholder="e.g. unsalted butter, semi-skimmed milk, pasta" value={g.product}
                        onChange={(e) => setG({ ...g, product: e.target.value })} maxLength={120} />
                      <p className="mt-2 text-xs text-mist-500">Not sure? <Link className="link" href="/catalogue">Browse Product Discovery</Link>.</p>
                    </div>
                  )}
                  {step === 1 && (
                    <div className="grid max-w-lg gap-4 sm:grid-cols-[160px_1fr]">
                      <div>
                        <label htmlFor="g-qty" className="section-title">Quantity</label>
                        <input id="g-qty" autoFocus inputMode="decimal" className="input mt-2 h-11" placeholder="e.g. 120" value={g.qty}
                          onChange={(e) => setG({ ...g, qty: e.target.value.replace(",", ".").replace(/[^0-9.]/g, "") })} aria-describedby="g-qty-help" />
                      </div>
                      <div>
                        <label htmlFor="g-unit" className="section-title">Unit</label>
                        <select id="g-unit" className="input mt-2 h-11" value={g.unit} onChange={(e) => setG({ ...g, unit: e.target.value })}>
                          {UNITS.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
                        </select>
                      </div>
                      <p id="g-qty-help" className="text-xs text-mist-500 sm:col-span-2">
                        {g.unit === "packs" ? "Pack sizes differ per product, so you will be asked to confirm this assumption before any autonomous decision."
                          : "The agent converts this into each product's sales unit and lot size, and shows the conversion as evidence."}
                      </p>
                    </div>
                  )}
                  {step === 2 && (
                    <div className="max-w-xs">
                      <label htmlFor="g-date" className="section-title">Delivery date</label>
                      <p className="mt-1 text-sm text-mist-500">Availability is checked against this date.</p>
                      <input id="g-date" autoFocus type="date" min={tomorrow(1)} className="input mt-3 h-11" value={g.date} onChange={(e) => setG({ ...g, date: e.target.value })} />
                      {g.date && <p className="mt-2 text-xs text-mist-500">{day(g.date)}</p>}
                    </div>
                  )}
                  {step === 3 && (
                    <div className="space-y-5">
                      <fieldset>
                        <legend className="section-title">Dietary requirements</legend>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {DIETARY.map((d) => <Chip key={d.value} checked={g.dietary.includes(d.value)} label={d.label}
                            onChange={(on) => setG({ ...g, dietary: on ? [...g.dietary, d.value] : g.dietary.filter((x) => x !== d.value) })} />)}
                        </div>
                      </fieldset>
                      <fieldset>
                        <legend className="section-title">Must be free from</legend>
                        <p className="mt-0.5 text-xs text-mist-500">Products with unknown allergen data are excluded by policy — never assumed safe.</p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {ALLERGENS.map((a) => <Chip key={a} checked={g.allergens.includes(a)} label={ALLERGEN_LABEL[a] || a}
                            onChange={(on) => setG({ ...g, allergens: on ? [...g.allergens, a] : g.allergens.filter((x) => x !== a) })} />)}
                        </div>
                      </fieldset>
                      <div className="max-w-xs">
                        <label htmlFor="g-prio" className="section-title">Ranking priority</label>
                        <select id="g-prio" className="input mt-2" value={g.priority} onChange={(e) => setG({ ...g, priority: e.target.value })}>
                          {PRIORITIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                        </select>
                      </div>
                    </div>
                  )}
                  {step === 4 && (
                    <div className="space-y-4">
                      <div>
                        <div className="label">Your request</div>
                        <p className="mt-1 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm text-mist-200">{composeText(g)}</p>
                      </div>
                      <div>
                        <div className="label mb-2">How AIMO understood it</div>
                        {!preview ? <div className="space-y-2"><Skeleton /><Skeleton className="w-2/3" /></div> : <IntentPanel intent={preview.intent} parser={preview.parser} />}
                      </div>
                      {preview?.intent?.confirmation_required?.length > 0 && (
                        <Notice tone="warn" title="Submitting confirms these assumptions">
                          <ul className="list-disc pl-5">{preview.intent.confirmation_required.map((c: string) => <li key={c}>{c}</li>)}</ul>
                        </Notice>
                      )}
                      {preview?.intent?.warnings?.length > 0 && <Notice tone="warn">{preview.intent.warnings.join(" ")}</Notice>}
                    </div>
                  )}
                </div>
                <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-ink-700 pt-4">
                  <button type="button" className="btn-quiet" disabled={step === 0} onClick={() => setStep(step - 1)}><ArrowLeft className="h-4 w-4" aria-hidden />Back</button>
                  {step < 4 ? (
                    <button type="submit" className="btn-primary" disabled={!stepValid}>Continue<ArrowRight className="h-4 w-4" aria-hidden /></button>
                  ) : (
                    <button type="submit" className="btn-primary px-5" disabled={busy || !can("procure") || !preview}>
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />} Run procurement agent
                    </button>
                  )}
                </div>
              </form>
            ) : (
              <div className="p-5">
                <label htmlFor="free-text" className="section-title">Request</label>
                <textarea id="free-text" className="input mt-2 min-h-[110px] resize-y text-[15px] leading-relaxed" value={text} onChange={(e) => setText(e.target.value)}
                  placeholder="e.g. I need 1,000 litres of lactose-free milk by Monday…" maxLength={2000} />
                <div className="mt-3">
                  <div className="label mb-2">Demo scenarios</div>
                  <div className="flex flex-wrap gap-2">
                    {scenarios.map((sc) => (
                      <button key={sc.id} title={sc.demonstrates} onClick={() => { setText(sc.text); setSimulate(sc.simulate === "timeout" ? "timeout" : "none"); }}
                        aria-pressed={text === sc.text}
                        className={cls("rounded-full border px-3 py-1 text-xs transition", text === sc.text ? "border-teal-400 bg-teal-400/[0.07] text-teal-300" : "border-ink-600 bg-white text-mist-400 hover:border-ink-500 hover:text-mist-100")}>
                        {sc.title}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-ink-700 pt-4">
                  <div className="flex items-center gap-2 text-xs text-mist-500">
                    Live API
                    <Segmented value={simulate} onChange={setSimulate} options={[{ value: "none", label: "Normal" }, { value: "timeout", label: "Simulate timeout" }]} />
                  </div>
                  <button className="btn-primary px-5" onClick={() => run(false)} disabled={busy || text.trim().length < 3 || !can("procure")}>
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />} Run procurement agent
                  </button>
                </div>
              </div>
            )}
          </Card>

          <aside className="space-y-4">
            <div className="card p-5 text-sm">
              <div className="section-title">What happens next</div>
              <ol className="mt-3 space-y-3 text-mist-400">
                {[["Search & verify", "Catalogue search, unit and allergen checks, live Valio price and availability."],
                  ["Policy decision", "Within limits: proposal created. Otherwise: human approval or blocked."],
                  ["Proposal", "An internal proposal — never submitted to the supplier."]].map(([t, d], i) => (
                  <li key={t} className="flex gap-3"><span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ink-800 text-[11px] font-semibold text-mist-300">{i + 1}</span>
                    <span><span className="font-medium text-mist-200">{t}.</span> {d}</span></li>
                ))}
              </ol>
            </div>
            {busy && <PipelineCard pipeline={pipeline} busy />}
          </aside>
        </div>
      )}

      <ErrorBox error={error} />

      {!showForm && !doc && busy && <div className="card space-y-3 p-5" aria-busy="true"><Skeleton className="h-6 w-1/2" /><Skeleton /><Skeleton className="w-2/3" /></div>}

      {doc && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-start gap-3 rounded-xl border border-ink-700 bg-white px-4 py-3">
            <Pencil className="mt-0.5 h-4 w-4 shrink-0 text-mist-500" aria-hidden />
            <p className="min-w-0 flex-1 text-sm text-mist-300">“{doc.request_text}”</p>
          </div>
          {s.api_error && (
            <Notice tone="danger" title="Live information could not be verified">{s.api_error}. Prices and availability are shown as unverified and are never treated as confirmed.</Notice>
          )}
          {doc.intent?.confirmation_required?.length > 0 && !doc.overrides?.confirm_assumptions && (
            <Notice tone="warn" role="alert" title="Please confirm the agent's assumptions before it can buy autonomously"
              action={<button className="btn-warn" disabled={busy || !can("procure")} onClick={() => run(true, doc.request_text)}>Confirm assumptions &amp; re-run</button>}>
              <ul className="mt-1 list-disc pl-5">{doc.intent.confirmation_required.map((c: string) => <li key={c}>{c}</li>)}</ul>
            </Notice>
          )}
          <DecisionBanner doc={doc} />

          <Disclosure title="Agent pipeline" hint={`${done} of ${pipeline.length} stages confirmed by the audit trail${s.shortlisted != null ? ` · ${s.shortlisted} shortlisted · ${s.eligible} eligible · ${s.excluded} excluded` : ""}`}>
            <PipelineList pipeline={pipeline} />
          </Disclosure>

          <Tabs label="Decision details" value={tab} onChange={setTab} tabs={[
            { value: "recs", label: "Recommendations", count: doc.recommendations.length },
            ...(doc.recommendations.length > 0 ? [{ value: "compare" as const, label: "Comparison" }] : []),
            { value: "method", label: "Constraints & method" },
            { value: "excluded", label: "Excluded", count: doc.excluded?.length || 0 },
            { value: "json", label: "Decision JSON" },
          ]} />

          {tab === "recs" && (
            <div className="space-y-4">
              <h2 className="text-base font-semibold">Ranked recommendations <span className="text-sm font-normal text-mist-500">({doc.recommendations.length})</span></h2>
              {doc.recommendations.length === 0 && <div className="card card-pad text-sm text-mist-400">No catalogue product satisfies every hard constraint. See the Excluded tab for the exact reasons.</div>}
              {(showAll ? doc.recommendations : doc.recommendations.slice(0, 5)).map((c: any) => (
                <RecommendationCard key={c.id} c={c} selected={c.id === doc.selected?.product_id}
                  onChoose={can("procure") ? choose : undefined} chooseDisabled={busy || finished || c.id === doc.selected?.product_id} />
              ))}
              {doc.recommendations.length > 5 && (
                <button className="btn-ghost w-full" onClick={() => setShowAll(!showAll)}>{showAll ? "Show top 5 only" : `Show all ${doc.recommendations.length} eligible options`}</button>
              )}
            </div>
          )}
          {tab === "compare" && (
            <div className="space-y-5">
              <Card title="Top eligible options"><ComparisonView comparison={doc.comparison} recs={doc.recommendations} /></Card>
              {doc.ranking_explanation && <Card title="Why #1 ranks above #2"><RankingWhy ex={doc.ranking_explanation} recs={doc.recommendations} /></Card>}
            </div>
          )}
          {tab === "method" && (
            <div className="grid gap-5 lg:grid-cols-2">
              <Card title="Extracted constraints"><IntentPanel intent={doc.intent} parser={doc.parser} /></Card>
              <Card title="Ranking method">
                <div className="space-y-3 text-sm text-mist-400">
                  <p>Hard constraints first (requirements, allergens, unit conversion, availability, category), then a deterministic score:</p>
                  <div className="grid grid-cols-2 gap-1.5">
                    {Object.entries(s.weights || {}).map(([k, v]: any) => (
                      <div key={k} className="flex justify-between rounded-md border border-ink-700 px-2.5 py-1.5 text-xs"><span className="capitalize">{k}</span><span className="tabular-nums text-mist-200">{(v * 100).toFixed(0)}%</span></div>
                    ))}
                  </div>
                  <p className="text-xs text-mist-500">Missing factors are removed and the remaining weights renormalised — never scored as zero. Scores are relative to this request&apos;s eligible options.</p>
                </div>
              </Card>
            </div>
          )}
          {tab === "excluded" && (
            doc.excluded?.length ? <Card title={`Excluded candidates (${doc.excluded.length})`}><ExcludedList items={doc.excluded} /></Card>
              : <div className="card card-pad text-sm text-mist-400">No candidates were excluded.</div>
          )}
          {tab === "json" && <Card title="Decision document" subtitle="aimo-protocol/procurement-decision@1"><JsonView data={doc} /></Card>}
        </div>
      )}
    </div>
  );
}

function Chip({ checked, label, onChange }: { checked: boolean; label: string; onChange: (on: boolean) => void }) {
  return (
    <label className={cls("inline-flex cursor-pointer select-none items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition focus-within:ring-2 focus-within:ring-teal-400/40",
      checked ? "border-teal-400 bg-teal-400/[0.07] text-teal-300" : "border-ink-600 bg-white text-mist-300 hover:border-ink-500")}>
      <input type="checkbox" className="sr-only" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {checked && <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />}{label}
    </label>
  );
}

function PipelineList({ pipeline }: { pipeline: any[] }) {
  return (
    <ol className="grid gap-1 sm:grid-cols-2" aria-live="polite">
      {pipeline.map((p: any) => (
        <li key={p.stage + (p.event_id || "")} className="flex items-start gap-2.5 py-1 text-sm" title={p.detail || ""}>
          {p.status === "DONE" ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-label="done" />
            : p.status === "FAILED" ? <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-200" aria-label="failed" />
            : p.status === "WAITING" ? <Loader2 className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-label="waiting" />
            : <span className="mt-0.5 h-4 w-4 shrink-0 rounded-full border border-ink-600" aria-label="not reached" />}
          <span className={p.status === "PENDING" || p.status === "NOT_REACHED" ? "text-mist-500" : "text-mist-200"}>{p.label}</span>
        </li>
      ))}
    </ol>
  );
}

function PipelineCard({ pipeline, busy }: { pipeline: any[]; busy: boolean }) {
  return (
    <div className="card p-5">
      <div className="section-title">Agent pipeline</div>
      {busy && <div className="mt-2 flex items-center gap-2 text-sm text-mist-400"><Loader2 className="h-4 w-4 animate-spin text-teal-400" aria-hidden />Running on the backend — waiting for confirmed results…</div>}
      <div className="mt-3"><PipelineList pipeline={pipeline} /></div>
    </div>
  );
}

export default function WorkspacePage() {
  return (
    <Suspense fallback={null}>
      <Workspace />
    </Suspense>
  );
}
