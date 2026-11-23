import { useEffect, useState } from 'react'
import { api, type Check, type Readiness } from './api'
import { Section, btn2Cls } from './ui'

const ORDER: Record<Check['level'], number> = { error: 0, warn: 1, ok: 2, info: 3 }
const TEXT: Record<Check['level'], string> = { ok: 'text-ink-2', info: 'text-mute', warn: 'text-ink', error: 'text-ink font-medium' }
/** Black & white needs shapes, not colours: ✓ ok · solid ! error · outlined ! warning · hollow dot info. */
const Mark = ({ level }: { level: Check['level'] }) =>
  level === 'ok' ? <span aria-label="ok" className="text-[13px] font-semibold leading-none">✓</span>
  : level === 'error' ? <span aria-label="error" className="flex h-4 w-4 items-center justify-center rounded-full bg-ink text-[10px] font-bold text-paper">!</span>
  : level === 'warn' ? <span aria-label="warning" className="flex h-4 w-4 items-center justify-center rounded-full border border-ink text-[10px] font-bold">!</span>
  : <span aria-label="info" className="ml-1 inline-block h-2 w-2 rounded-full border border-mute" />

export function useReadiness(every = 15000) {
  const [r, setR] = useState<Readiness | null>(null)
  useEffect(() => {
    const load = () => api<Readiness>('/doctor').then(setR).catch(() => {})
    load(); const t = setInterval(load, every); return () => clearInterval(t)
  }, [every])
  return r
}

/** Which Setup section fixes each check (for jump buttons and the section index badges). */
export const CHECK_SECTION: Record<string, string> = { llm: '01', resume: '02', latex: '02', profile: '03', roles: '04', answers: '05',
  sources: '06', notion: '07', backup: '08', notify: '09' }
export const jump = (n: string) => document.getElementById(`s-${n}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

/** Setup 00: everything the automation needs, in order, with the fix next to each problem. */
export function Checklist() {
  const r = useReadiness()
  const sorted = r ? [...r.checks].sort((a, b) => ORDER[a.level] - ORDER[b.level]) : []
  return (
    <Section n="00" title="Checklist" note={r ? (r.ready ? 'Everything the automation needs is in place.' : `${r.errors} thing${r.errors === 1 ? '' : 's'} to fix before Seeko can run on its own.`) : 'Checking…'}>
      {!r ? <div className="skeleton h-40" /> : (
        <ul className="stagger space-y-2">
          {sorted.map(c => {
            const sec = CHECK_SECTION[c.key]
            return (
              <li key={c.key} className="flex items-center gap-4 rounded-lg border border-hair bg-paper px-4 py-3 transition-shadow duration-200 hover:shadow-[var(--shadow)]">
                <span className="flex w-4 shrink-0 justify-center"><Mark level={c.level} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[14px] font-semibold">{c.title}</span>
                  <span className={`block break-words text-[13px] ${TEXT[c.level]}`}>{c.detail}</span>
                  {c.fix && !c.ok && !(sec && c.where === 'setup') && <span className="mt-0.5 block font-mono text-[12px] text-ink-2">→ {c.fix}</span>}
                </span>
                {!c.ok && sec && c.where === 'setup'
                  ? <button className={btn2Cls + ' shrink-0'} onClick={() => jump(sec)} title={c.fix}>{c.level === 'error' ? 'Fix' : 'Review'}</button>
                  : c.ok && c.level === 'ok' ? <span className="shrink-0 font-mono text-[11px] font-semibold uppercase tracking-[0.1em] text-mute">Ready</span> : null}
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}

const SECTIONS = [['00', 'Checklist'], ['01', 'Model'], ['02', 'Résumé'], ['03', 'Candidate'], ['04', 'Search'], ['05', 'Answers'],
  ['06', 'Sources'], ['07', 'Notion'], ['08', 'Backup'], ['09', 'Notifications']]

/** Setup's sticky index: readiness ring, section links with problem badges, and the section in view highlighted. */
export function SetupNav() {
  const r = useReadiness()
  const [active, setActive] = useState('00')
  useEffect(() => {
    const io = new IntersectionObserver(es => {
      const vis = es.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
      if (vis) setActive(vis.target.id.slice(2))
    }, { rootMargin: '-80px 0px -60% 0px' })
    const t = setTimeout(() => SECTIONS.forEach(([n]) => { const el = document.getElementById(`s-${n}`); if (el) io.observe(el) }), 300)
    return () => { clearTimeout(t); io.disconnect() }
  }, [])
  const counted = r ? r.checks.filter(c => c.level !== 'info') : []
  const ready = counted.filter(c => c.level === 'ok').length
  const pct = counted.length ? ready / counted.length : 0
  const badge = (n: string) => {
    const cs = r?.checks.filter(c => CHECK_SECTION[c.key] === n && (c.level === 'error' || c.level === 'warn')) ?? []
    return cs.some(c => c.level === 'error') ? 'error' : cs.length ? 'warn' : null
  }
  return (
    <nav aria-label="Setup sections" className="sticky top-24 space-y-4">
      <div className="flex items-center gap-4 rounded-xl border border-hair bg-paper-2 p-4">
        <svg width="56" height="56" viewBox="0 0 56 56" className="shrink-0 -rotate-90" aria-hidden>
          <circle cx="28" cy="28" r="23" fill="none" stroke="var(--hair)" strokeWidth="5" />
          <circle cx="28" cy="28" r="23" fill="none" stroke="var(--ink)" strokeWidth="5" strokeLinecap="round"
            strokeDasharray={2 * Math.PI * 23} strokeDashoffset={2 * Math.PI * 23 * (1 - pct)} style={{ transition: 'stroke-dashoffset .8s cubic-bezier(.22,1,.36,1)' }} />
        </svg>
        <div>
          <div className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-mute">Readiness</div>
          <div className="text-[18px] font-semibold tracking-[-0.01em]">{r ? `${ready} of ${counted.length} ready` : '…'}</div>
          {r && !r.ready && <div className="text-[12px] text-mute">{r.errors} blocking</div>}
        </div>
      </div>
      <ul className="space-y-0.5">
        {SECTIONS.map(([n, t]) => {
          const on = active === n, b = badge(n)
          return (
            <li key={n}>
              <button onClick={() => jump(n)} aria-current={on ? 'true' : undefined}
                className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-[14px] transition-colors duration-200 ${on ? 'bg-ink text-paper' : 'text-ink-2 hover:bg-paper-2 hover:text-ink'}`}>
                <span className={`font-mono text-[11px] ${on ? 'text-paper/60' : 'text-mute'}`}>{n}</span>
                <span className="flex-1">{t}</span>
                {b && <span aria-label={b === 'error' ? 'needs fixing' : 'needs a look'} className={`flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold ${b === 'error' ? (on ? 'bg-paper text-ink' : 'bg-ink text-paper') : on ? 'border border-paper' : 'border border-ink'}`}>!</span>}
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

/** One line on the Pipeline page while something blocks the automation. */
export function ReadinessBanner() {
  const r = useReadiness(30000)
  if (!r || r.ready) return null
  const errs = r.checks.filter(c => c.level === 'error')
  return (
    <a href="#setup" className="anim-pop mb-8 flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg border border-ink bg-paper-2 px-4 py-3 transition-colors text-[13.5px] hover:bg-paper">
      <span className="font-semibold text-accent">{errs.length} setup step{errs.length === 1 ? '' : 's'} left</span>
      <span className="text-ink-2">{errs.map(c => c.title).join(' · ')}</span>
      <span className="ml-auto text-[12px] font-semibold uppercase tracking-[0.1em]">Open checklist →</span>
    </a>
  )
}
