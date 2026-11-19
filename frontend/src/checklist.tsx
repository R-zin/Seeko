import { useEffect, useState } from 'react'
import { api, type Check, type Readiness } from './api'
import { Section, linkBtnCls } from './ui'

const MARK: Record<Check['level'], [string, string]> = {
  ok: ['bg-ok', 'text-ink-2'], info: ['border border-mute', 'text-mute'], warn: ['bg-warn', 'text-warn'], error: ['bg-accent', 'text-accent'],
}

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
            <li key={c.key} className="grid grid-cols-[1rem_11rem_1fr] items-baseline gap-x-4 border-b border-hair py-3 text-[13.5px] sm:grid-cols-[1rem_13rem_1fr]">
              <i className={`inline-block h-2.5 w-2.5 ${MARK[c.level][0]}`} aria-label={c.level} />
              <span className="font-semibold">{c.title}</span>
              <span className="min-w-0">
                <span className={`block break-words ${MARK[c.level][1]}`}>{c.detail}</span>
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
    <a href="#setup" className="mb-8 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-l-2 border-accent bg-paper-2 px-4 py-3 text-[13.5px] hover:bg-paper">
      <span className="font-semibold text-accent">{errs.length} setup step{errs.length === 1 ? '' : 's'} left</span>
      <span className="text-ink-2">{errs.map(c => c.title).join(' · ')}</span>
      <span className="ml-auto text-[12px] font-semibold uppercase tracking-[0.1em]">Open checklist →</span>
    </a>
  )
}
