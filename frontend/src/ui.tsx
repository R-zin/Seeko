import { useState, type ReactNode } from 'react'

export const inputCls = 'w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800'
export const btnCls = 'rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50'
export const btn2Cls = 'rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-800'

export const Card = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
    <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
    <div className="space-y-3">{children}</div>
  </section>
)
export const Field = ({ label, children }: { label: string; children: ReactNode }) => (
  <label className="block text-sm"><span className="mb-1 block text-slate-600 dark:text-slate-400">{label}</span>{children}</label>
)
export function useAction() {
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true); setMsg(null)
    try { setMsg({ ok: true, text: (await fn()) || 'Done' }) } catch (e: any) { setMsg({ ok: false, text: e.message }) } finally { setBusy(false) }
  }
  return { busy, msg, run }
}
export const Msg = ({ m }: { m: { ok: boolean; text: string } | null }) =>
  m ? <pre className={`whitespace-pre-wrap rounded p-2 text-xs ${m.ok ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300' : 'bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-300'}`}>{m.text}</pre> : null
export const statusColor: Record<string, string> = {
  scraped: 'bg-slate-200 text-slate-700', scored: 'bg-sky-100 text-sky-800', tailoring: 'bg-sky-100 text-sky-800', tailored: 'bg-violet-100 text-violet-800',
  applying: 'bg-amber-100 text-amber-800', needs_review: 'bg-orange-200 text-orange-900', applied: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-800', skipped: 'bg-slate-100 text-slate-500',
}
export const Badge = ({ s }: { s: string }) => <span className={`rounded px-2 py-0.5 text-xs font-medium ${statusColor[s] ?? 'bg-slate-200'}`}>{s.replace('_', ' ')}</span>
