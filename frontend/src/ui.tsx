import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'

/* ── Primitives for the monochrome system (prototyped in Stitch) ──────────
   White / near-black only, 1px hairlines, 6px radius, short eased motion. */

export const inputCls =
  'w-full rounded-md border border-hair bg-paper px-3 py-2 text-[14.5px] text-ink placeholder:text-mute/70 ' +
  'transition-[border-color,box-shadow] duration-200 hover:border-mute focus:border-ink focus:shadow-[0_0_0_3px_var(--paper-3)] ' +
  'focus:outline-none focus-visible:outline-none'
export const areaCls =
  'w-full rounded-md border border-hair bg-paper p-3 text-[14.5px] leading-relaxed text-ink placeholder:text-mute/70 ' +
  'transition-[border-color,box-shadow] duration-200 hover:border-mute focus:border-ink focus:shadow-[0_0_0_3px_var(--paper-3)] ' +
  'focus:outline-none focus-visible:outline-none'
export const monoAreaCls = areaCls + ' font-mono text-[12.5px] leading-[1.55]'

const btnBase =
  'inline-flex h-9 select-none items-center gap-2 rounded-md px-3.5 text-[13px] font-semibold tracking-[0.01em] ' +
  'transition-[background-color,color,border-color,transform,box-shadow] duration-150 ease-out active:scale-[.97] ' +
  'disabled:cursor-not-allowed disabled:opacity-40 disabled:active:scale-100'
export const btnCls = btnBase + ' bg-ink text-paper hover:bg-ink-2 hover:shadow-[var(--shadow)]'
export const btn2Cls = btnBase + ' border border-hair bg-paper text-ink hover:border-ink hover:bg-paper-2'
export const btnAccentCls = btnBase + ' bg-ink text-paper ring-2 ring-ink ring-offset-2 ring-offset-paper hover:bg-ink-2'
export const linkBtnCls =
  'text-[13px] font-medium text-ink underline decoration-hair underline-offset-4 transition-colors hover:decoration-ink'

/** Small uppercase label used for kickers, captions and column heads. */
export const Kicker = ({ children, className = '' }: { children: ReactNode; className?: string }) => (
  <span className={`text-[11px] font-semibold uppercase tracking-[0.12em] text-mute ${className}`}>{children}</span>
)

/** Sections render full width: number + title (+ aside on the right), then the content. Pages that want each section's
 *  content on a grey panel (Setup) wrap themselves in <SectionPanels>. Each section gets id="s-<n>" for in-page links. */
const PanelCtx = createContext(false)
export const SectionPanels = ({ children }: { children: ReactNode }) => <PanelCtx.Provider value={true}>{children}</PanelCtx.Provider>

export function Section({ n, title, note, children, aside }: { n: string; title: string; note?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  const panel = useContext(PanelCtx)
  return (
    <section id={`s-${n}`} className="anim-in scroll-mt-24 pb-14">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 pb-4">
        <div className="min-w-0">
          <h2 className="flex items-baseline gap-2.5 text-[22px] font-semibold leading-[1.15] tracking-[-0.02em]">
            <span className="font-mono text-[11px] font-normal text-mute">{n}</span>{title}
          </h2>
          {note && <p className="mt-1.5 max-w-[72ch] text-[13.5px] leading-relaxed text-mute">{note}</p>}
        </div>
        {aside && <div className="shrink-0 text-[13px]">{aside}</div>}
      </header>
      <div className={panel ? 'rounded-xl border border-hair bg-paper-2 p-4 sm:p-6' : ''}>{children}</div>
    </section>
  )
}

export const Field = ({ label, hint, children, className = '' }: { label: string; hint?: string; children: ReactNode; className?: string }) => (
  <label className={`block ${className}`}>
    <Kicker>{label}</Kicker>
    <div className="mt-1.5">{children}</div>
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
      className={`anim-fade mt-3 whitespace-pre-wrap rounded-md border px-3 py-2 font-mono text-[12px] leading-relaxed ${m.ok ? 'border-hair bg-paper-2 text-ink-2' : 'border-ink bg-paper text-ink'}`}>
      {!m.ok && <span className="mr-2 font-semibold">!</span>}{m.text}
    </p>
  ) : null

/* Status pills. Black & white only: what needs you is solid, finished work is outlined, quiet states are grey. */
const STATUS: Record<string, { label: string; cls: string }> = {
  scraped:      { label: 'Scraped',      cls: 'border-hair text-mute' },
  scored:       { label: 'Scored',       cls: 'border-hair text-ink-2' },
  tailoring:    { label: 'Tailoring',    cls: 'border-ink-2 text-ink pulse' },
  tailored:     { label: 'Tailored',     cls: 'border-ink-2 text-ink' },
  applying:     { label: 'Filling',      cls: 'border-ink-2 text-ink pulse' },
  needs_review: { label: 'Needs review', cls: 'border-ink bg-ink text-paper' },
  applied:      { label: 'Applied',      cls: 'border-ink text-ink' },
  failed:       { label: 'Failed',       cls: 'border-ink border-dashed text-ink' },
  skipped:      { label: 'Skipped',      cls: 'border-hair text-mute line-through' },
  tracked:      { label: 'Tracked',      cls: 'border-ink-2 text-ink-2' },
}
export const Badge = ({ s }: { s: string }) => {
  const x = STATUS[s] ?? { label: s, cls: 'border-hair text-mute' }
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-[2px] text-[10.5px] font-semibold uppercase tracking-[0.08em] transition-colors ${x.cls}`}>
      {x.label}
    </span>
  )
}

/** Stat card: big tabular figure, caption, optional progress bar (0..1). */
export const Figure = ({ value, label, accent, progress }: { value: ReactNode; label: string; accent?: boolean; progress?: number }) => (
  <div className={`anim-in rounded-lg border p-4 transition-shadow duration-200 hover:shadow-[var(--shadow)] ${accent ? 'border-ink' : 'border-hair'}`}>
    <Kicker className={accent ? 'text-ink' : ''}>{accent && <i className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-ink align-middle pulse" aria-hidden />}{label}</Kicker>
    <div className="mt-2 text-[44px] font-semibold leading-none tracking-[-0.04em] tabular-nums sm:text-[52px]">{value}</div>
    {progress !== undefined && (
      <div className="mt-4 h-[3px] overflow-hidden rounded-full bg-paper-3" aria-hidden>
        <div className="h-full rounded-full bg-ink transition-[width] duration-700 ease-out" style={{ width: `${Math.max(0, Math.min(1, progress)) * 100}%` }} />
      </div>
    )}
  </div>
)

export const pad = (n: number, w = 2) => String(n).padStart(w, '0')

/* ── Theme: system, light or dark (remembered on this browser) ───────────── */
type Theme = 'system' | 'light' | 'dark'
const themeKey = 'seeko-theme'
const themeSubs = new Set<() => void>()
export function getTheme(): Theme {
  try { return (localStorage.getItem(themeKey) as Theme) || 'system' } catch { return 'system' }
}
export function applyTheme(t: Theme = getTheme()) {
  const root = document.documentElement
  if (t === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', t)
}
export function setTheme(t: Theme) {
  try { localStorage.setItem(themeKey, t) } catch {}
  const go = () => { applyTheme(t); themeSubs.forEach(f => f()) }
  const d = document as any
  if (d.startViewTransition) d.startViewTransition(go); else go()
}
export function cycleTheme() {
  const t = getTheme(); setTheme(t === 'system' ? 'dark' : t === 'dark' ? 'light' : 'system')
}
export function useTheme(): Theme {
  return useSyncExternalStore(f => { themeSubs.add(f); return () => themeSubs.delete(f) }, getTheme)
}

export function useDark() {
  const theme = useTheme()
  const q = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null
  const [sys, setSys] = useState(!!q?.matches)
  useEffect(() => {
    if (!q) return
    const h = (e: MediaQueryListEvent) => setSys(e.matches)
    q.addEventListener('change', h); return () => q.removeEventListener('change', h)
  }, [q])
  return theme === 'dark' || (theme === 'system' && sys)
}

/* ── Toasts: short confirmations for shortcuts and palette actions ──────── */
type Toast = { id: number; text: string; ok: boolean }
let toasts: Toast[] = []
const toastSubs = new Set<() => void>()
const emitToasts = () => toastSubs.forEach(f => f())
export function toast(text: string, ok = true) {
  const t = { id: Date.now() + Math.random(), text, ok }
  toasts = [...toasts.slice(-3), t]; emitToasts()
  setTimeout(() => { toasts = toasts.filter(x => x.id !== t.id); emitToasts() }, ok ? 3200 : 6000)
}
export function Toaster() {
  const list = useSyncExternalStore(f => { toastSubs.add(f); return () => toastSubs.delete(f) }, () => toasts)
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-5 left-1/2 z-50 flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-2">
      {list.map(t => (
        <div key={t.id} role={t.ok ? 'status' : 'alert'}
          className={`anim-toast pointer-events-auto rounded-lg px-4 py-3 text-[13.5px] shadow-[var(--shadow)] ${t.ok ? 'bg-ink text-paper' : 'border border-ink bg-paper text-ink'}`}>
          {!t.ok && <span className="mr-2 font-semibold">!</span>}{t.text}
        </div>
      ))}
    </div>
  )
}
