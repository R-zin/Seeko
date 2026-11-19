import { useEffect, useState, type ReactNode } from 'react'

/* ── Primitives for the Swiss editorial system ───────────────────────── */

export const inputCls =
  'w-full border-0 border-b border-hair bg-transparent px-0 py-2 text-[15px] text-ink placeholder:text-mute/70 ' +
  'transition-colors focus:border-ink focus:outline-none focus-visible:outline-none'
export const areaCls =
  'w-full border border-hair bg-transparent p-3 text-[15px] leading-relaxed text-ink placeholder:text-mute/70 ' +
  'transition-colors focus:border-ink focus:outline-none focus-visible:outline-none'
export const monoAreaCls = areaCls + ' font-mono text-[12.5px] leading-[1.55]'

const btnBase =
  'inline-flex h-10 items-center gap-2 px-4 text-[13px] font-semibold uppercase tracking-[0.08em] transition-colors ' +
  'disabled:cursor-not-allowed disabled:opacity-40'
export const btnCls = btnBase + ' bg-ink text-paper hover:bg-accent hover:text-accent-ink'
export const btn2Cls = btnBase + ' border border-ink text-ink hover:bg-ink hover:text-paper'
export const btnAccentCls = btnBase + ' bg-accent text-accent-ink hover:bg-ink hover:text-paper'
export const linkBtnCls = 'text-[13px] font-medium text-ink underline decoration-hair underline-offset-4 hover:decoration-accent'

/** Small uppercase label used for kickers, captions and column heads. */
export const Kicker = ({ children, className = '' }: { children: ReactNode; className?: string }) => (
  <span className={`text-[11px] font-semibold uppercase tracking-[0.14em] text-mute ${className}`}>{children}</span>
)

/** Numbered editorial section: number + title + note on the left, content on the right. */
export function Section({ n, title, note, children, aside }: { n: string; title: string; note?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="grid grid-cols-12 gap-x-6 gap-y-6 border-t-2 border-rule pt-6 pb-14">
      <header className="col-span-12 lg:col-span-3">
        <div className="flex items-baseline gap-3 lg:block">
          <span className="font-mono text-[12px] text-accent">{n}</span>
          <h2 className="text-[26px] font-bold leading-[1.05] tracking-[-0.02em] lg:mt-2">{title}</h2>
        </div>
        {note && <p className="mt-3 max-w-[34ch] text-[13.5px] leading-relaxed text-mute">{note}</p>}
        {aside}
      </header>
      <div className="col-span-12 lg:col-span-9">{children}</div>
    </section>
  )
}

export const Field = ({ label, hint, children, className = '' }: { label: string; hint?: string; children: ReactNode; className?: string }) => (
  <label className={`block ${className}`}>
    <Kicker>{label}</Kicker>
    <div className="mt-1">{children}</div>
    {hint && <span className="mt-1 block text-[12px] text-mute">{hint}</span>}
  </label>
)

export const Check = ({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode }) => (
  <label className="inline-flex cursor-pointer select-none items-center gap-2.5 text-[14px]">
    <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
    {children}
  </label>
)

export function useAction() {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true); setMsg(null)
    try { setMsg({ ok: true, text: (await fn()) || 'Done' }) }
    catch (e: any) { setMsg({ ok: false, text: e.message }) }
    finally { setBusy(false) }
  }
  return { busy, msg, run }
}

export const Msg = ({ m }: { m: { ok: boolean; text: string } | null }) =>
  m ? (
    <p role={m.ok ? 'status' : 'alert'}
      className={`mt-3 whitespace-pre-wrap border-l-2 py-1 pl-3 font-mono text-[12px] leading-relaxed ${m.ok ? 'border-ok text-ink-2' : 'border-accent text-accent'}`}>
      {m.text}
    </p>
  ) : null

/* Status: a square swatch + lowercase word. Only "needs review" and "failed" use the accent. */
const STATUS: Record<string, { label: string; sw: string; txt?: string }> = {
  scraped:      { label: 'Scraped',      sw: 'border border-mute' },
  scored:       { label: 'Scored',       sw: 'bg-mute' },
  tailoring:    { label: 'Tailoring',    sw: 'bg-ink pulse' },
  tailored:     { label: 'Tailored',     sw: 'bg-ink' },
  applying:     { label: 'Filling',      sw: 'bg-ink pulse' },
  needs_review: { label: 'Needs review', sw: 'bg-accent', txt: 'text-accent' },
  applied:      { label: 'Applied',      sw: 'bg-ok', txt: 'text-ok' },
  failed:       { label: 'Failed',       sw: 'bg-accent', txt: 'text-accent' },
  skipped:      { label: 'Skipped',      sw: 'border border-hair', txt: 'text-mute' },
  tracked:      { label: 'Tracked',      sw: 'border border-ok', txt: 'text-ink-2' },
}
export const Badge = ({ s }: { s: string }) => {
  const x = STATUS[s] ?? { label: s, sw: 'bg-mute' }
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-semibold uppercase tracking-[0.1em] ${x.txt ?? 'text-ink-2'}`}>
      <i className={`inline-block h-2 w-2 ${x.sw}`} aria-hidden />{x.label}
    </span>
  )
}

/** Big editorial figure: oversized tabular numeral with a caption. */
export const Figure = ({ value, label, accent }: { value: ReactNode; label: string; accent?: boolean }) => (
  <div className="border-t border-hair pt-3">
    <div className={`text-[56px] font-bold leading-none tracking-[-0.04em] tabular-nums sm:text-[72px] ${accent ? 'text-accent' : ''}`}>{value}</div>
    <Kicker className="mt-3 block">{label}</Kicker>
  </div>
)

export const pad = (n: number, w = 2) => String(n).padStart(w, '0')

export function useDark() {
  const q = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null
  const [dark, setDark] = useState(!!q?.matches)
  useEffect(() => {
    if (!q) return
    const h = (e: MediaQueryListEvent) => setDark(e.matches)
    q.addEventListener('change', h); return () => q.removeEventListener('change', h)
  }, [q])
  return dark
}
