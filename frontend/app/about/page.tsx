"use client";

import { ExternalLink, Globe, Linkedin } from "lucide-react";
import Image from "next/image";
import { PageHeader } from "@/components/ui";
import { BUILDERS, STARTUP, host } from "@/lib/team";

const PRINCIPLES = ["AI recommends.", "Policy controls.", "Humans authorize when required.", "The system records everything."];

export default function AboutPage() {
  return (
    <div className="space-y-6">
      <PageHeader title="About AIMO PROTOCOL"
        subtitle="The Trusted Operating Layer for Autonomous Commerce — built for Junction 2026 (Valio Aimo autonomous procurement challenge)." />

      <section className="card p-6" aria-labelledby="mission">
        <h2 id="mission" className="section-title">What AIMO does</h2>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-mist-400">
          AIMO lets AI agents handle procurement against the real Valio Aimo catalogue and live supplier API — within rules people control.
          Every recommendation is verified, every decision is policy-checked, humans approve when required, and every step is recorded in a
          tamper-evident audit trail. Approved requests become internal proposals that are never submitted to the supplier.
        </p>
        <ol className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {PRINCIPLES.map((p, i) => (
            <li key={p} className="flex items-center gap-3 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm text-mist-200">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-teal-400 text-[11px] font-semibold text-white">{i + 1}</span>{p}
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="builders">
        <h2 id="builders" className="section-title mb-3">Builders</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {BUILDERS.map((b) => (
            <a key={b.linkedin} href={b.linkedin} target="_blank" rel="noopener noreferrer"
              className="card group flex items-center gap-4 p-5 transition hover:border-ink-600 hover:shadow-pop">
              <Image src={b.photo} alt={`Photo of ${b.name}`} width={128} height={128}
                className="h-16 w-16 shrink-0 rounded-full object-cover ring-2 ring-white shadow-glow" style={{ objectPosition: b.focus }} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-mist-100">{b.name}</span>
                <span className="block text-xs text-mist-500">{b.role}</span>
                <span className="mt-1 inline-flex items-center gap-1.5 text-xs font-medium text-teal-400 group-hover:underline">
                  <Linkedin className="h-3.5 w-3.5" aria-hidden />LinkedIn profile
                </span>
              </span>
              <ExternalLink className="h-4 w-4 shrink-0 text-mist-500" aria-hidden />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          ))}
        </div>
      </section>

      <section aria-labelledby="startup">
        <h2 id="startup" className="section-title mb-3">{STARTUP.label}</h2>
        <a href={STARTUP.url} target="_blank" rel="noopener noreferrer"
          className="card group flex items-center gap-4 p-5 transition hover:border-ink-600 hover:shadow-pop sm:max-w-md">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-teal-400/10 text-teal-400" aria-hidden><Globe className="h-5 w-5" /></span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-mist-100">Visit our website</span>
            <span className="block truncate text-xs font-medium text-teal-400 group-hover:underline">{host(STARTUP.url)}</span>
          </span>
          <ExternalLink className="h-4 w-4 shrink-0 text-mist-500" aria-hidden />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      </section>
    </div>
  );
}
