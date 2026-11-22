import { useEffect, useState } from 'react'
import { api, type Check, type Readiness } from './api'
import { Section, linkBtnCls } from './ui'

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

/** Setup 00: everything the automation needs, in order, with the fix next to each problem. */
export function Checklist() {
  const r = useReadiness()
  return (
    <Section n="00" title="Checklist" note={r ? (r.ready ? 'Everything the automation needs is in place.' : `${r.errors} thing${r.errors === 1 ? '' : 's'} to fix before Seeko can run on its own.`) : 'Checking…'}>
      {!r ? <div className="h-40 bg-paper-2" /> : (
        <ul className="border-t border-ink">
          {r.checks.map(c => (
            <li key={c.key} className="grid grid-cols-[1rem_11rem_1fr] items-center gap-x-4 border-b border-hair py-3 text-[13.5px] sm:grid-cols-[1rem_13rem_1fr]">
              <Mark level={c.level} />
              <span className="font-semibold">{c.title}</span>
              <span className="min-w-0">
                <span className={`block break-words ${TEXT[c.level]}`}>{c.detail}</span>
                {c.fix && !c.ok && <span className="mt-0.5 block text-[12.5px] text-ink-2">→ {c.where ? <a className={linkBtnCls} href={`#${c.where}`}>{c.fix}</a> : <span className="font-mono text-[12px]">{c.fix}</span>}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
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
