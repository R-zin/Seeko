import { useEffect, useMemo, useState } from 'react'
import { api, type Job } from '../api'
import { Badge, Field, Kicker, Msg, Section, btnCls, inputCls, pad, useAction } from '../ui'

const STATUSES = ['scraped', 'scored', 'tailoring', 'tailored', 'applying', 'needs_review', 'applied', 'failed', 'skipped']
const SOURCES = ['greenhouse', 'lever', 'ashby', 'workday', 'linkedin', 'indeed', 'glassdoor']
type SortKey = 'discovered_at' | 'score' | 'company' | 'title'

const selectCls = inputCls + ' cursor-pointer pr-6 capitalize'

/* 01 — Controls ---------------------------------------------------------- */
function Controls() {
  const [s, setS] = useState<any>(null)
  const a = useAction()
  useEffect(() => { api('/settings').then(setS) }, [])

  const setMode = (approval: boolean) => {
    if (!approval && !confirm('Full-auto submits applications without your review, up to the daily cap. Continue?')) return
    setS({ ...s, approval_mode: approval })
  }

  return (
    <Section n="01" title="Controls" note="How much the assistant may do on its own.">
      {!s ? <div className="h-24 bg-paper-2" /> : <>
        <Kicker>Submission mode</Kicker>
        <div role="radiogroup" aria-label="Submission mode" className="mt-3 grid grid-cols-1 border border-ink sm:grid-cols-2">
          {[
            { v: true, t: 'Approval gate', d: 'Fills every form, then waits for you to approve each submission.' },
            { v: false, t: 'Full-auto', d: 'Submits on its own up to the daily cap. Still pauses on CAPTCHA, 2FA or unknown required fields.' },
          ].map(o => {
            const on = s.approval_mode === o.v
            return (
              <button key={o.t} role="radio" aria-checked={on} onClick={() => setMode(o.v)}
                className={`p-5 text-left transition-colors first:border-b sm:first:border-r sm:first:border-b-0 border-ink ${on ? (o.v ? 'bg-ink text-paper' : 'bg-accent text-accent-ink') : 'hover:bg-paper-2'}`}>
                <span className="flex items-center gap-2 text-[20px] font-bold tracking-[-0.02em]">
                  <i className={`inline-block h-3 w-3 border ${on ? 'border-current bg-current' : 'border-ink'}`} aria-hidden />{o.t}
                </span>
                <span className={`mt-2 block text-[13.5px] leading-snug ${on ? 'opacity-80' : 'text-mute'}`}>{o.d}</span>
              </button>
            )
          })}
        </div>

        <div className="mt-10 grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-3">
          <Field label="Daily submission cap" hint="Hard limit for full-auto.">
            <input type="number" min={0} className={inputCls + ' text-[28px] font-bold tracking-[-0.03em] tabular-nums'} value={s.daily_apply_cap} onChange={e => setS({ ...s, daily_apply_cap: +e.target.value })} />
          </Field>
          <Field label="Auto-discovery, minutes" hint="0 = only when you press Run.">
            <input type="number" min={0} className={inputCls + ' text-[28px] font-bold tracking-[-0.03em] tabular-nums'} value={s.discovery_interval_min} onChange={e => setS({ ...s, discovery_interval_min: +e.target.value })} />
          </Field>
          <Field label="Default match threshold" hint="Profile setting overrides this.">
            <input type="number" min={0} max={100} className={inputCls + ' text-[28px] font-bold tracking-[-0.03em] tabular-nums'} value={s.min_match_score} onChange={e => setS({ ...s, min_match_score: +e.target.value })} />
          </Field>
        </div>
        <div className="mt-8"><button className={btnCls} disabled={a.busy} onClick={() => a.run(async () => { await api('/settings', { method: 'PUT', json: s }); return 'Controls saved.' })}>Save controls</button></div>
        <Msg m={a.msg} />
      </>}
    </Section>
  )
}

/* 02 — Register ---------------------------------------------------------- */
function Register() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [f, setF] = useState({ status: '', source: '', q: '' })
  const [sort, setSort] = useState<{ k: SortKey; desc: boolean }>({ k: 'discovered_at', desc: true })

  useEffect(() => {
    const load = () => api('/jobs?' + new URLSearchParams(Object.entries(f).filter(([, v]) => v))).then(setJobs).catch(() => setJobs([]))
    const t0 = setTimeout(load, 200); const t = setInterval(load, 5000)
    return () => { clearTimeout(t0); clearInterval(t) }
  }, [f])

  const rows = useMemo(() => {
    if (!jobs) return []
    const dir = sort.desc ? -1 : 1
    return [...jobs].sort((a, b) => {
      const x = a[sort.k] ?? -1, y = b[sort.k] ?? -1
      return (x < y ? -1 : x > y ? 1 : 0) * dir
    })
  }, [jobs, sort])

  const Th = ({ k, children, className = '' }: { k?: SortKey; children: React.ReactNode; className?: string }) => (
    <th scope="col" className={`py-3 pr-4 font-semibold ${className}`} aria-sort={k && sort.k === k ? (sort.desc ? 'descending' : 'ascending') : undefined}>
      {k ? <button className="inline-flex items-center gap-1 uppercase tracking-[0.12em] hover:text-ink" onClick={() => setSort({ k, desc: sort.k === k ? !sort.desc : k !== 'company' && k !== 'title' })}>
        {children}<span className={sort.k === k ? 'text-accent' : 'opacity-0'}>{sort.desc ? '↓' : '↑'}</span></button>
        : <span className="uppercase tracking-[0.12em]">{children}</span>}
    </th>
  )

  return (
    <Section n="02" title="Register" note="Every job Seeko has seen, mirrored to Notion when it is connected.">
      <div className="grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-[1fr_12rem_12rem]">
        <Field label="Search"><input className={inputCls} placeholder="Title or company" value={f.q} onChange={e => setF({ ...f, q: e.target.value })} /></Field>
        <Field label="Status">
          <select className={selectCls} value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
            <option value="">All</option>{STATUSES.map(x => <option key={x} value={x}>{x.replace('_', ' ')}</option>)}
          </select>
        </Field>
        <Field label="Source">
          <select className={selectCls} value={f.source} onChange={e => setF({ ...f, source: e.target.value })}>
            <option value="">All</option>{SOURCES.map(x => <option key={x} value={x}>{x}</option>)}
          </select>
        </Field>
      </div>

      <div className="mt-8 overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left">
          <thead className="border-y-2 border-rule text-[11px] text-mute">
            <tr>
              <th scope="col" className="w-12 py-3 pr-4 font-semibold uppercase tracking-[0.12em]">No.</th>
              <Th k="title">Role</Th><Th k="company">Company</Th><Th>Source</Th>
              <Th k="score" className="text-right">Score</Th><Th>Status</Th><Th>Notion</Th><Th k="discovered_at">Found</Th>
            </tr>
          </thead>
          <tbody className="text-[14px]">
            {rows.map((j, i) => (
              <tr key={j.id} className="border-b border-hair align-baseline hover:bg-paper-2">
                <td className="py-3.5 pr-4 font-mono text-[11px] text-mute">{pad(i + 1, 3)}</td>
                <td className="py-3.5 pr-4"><a className="font-semibold tracking-[-0.005em] underline decoration-transparent underline-offset-4 hover:decoration-accent" href={j.url} target="_blank" rel="noreferrer">{j.title}</a></td>
                <td className="py-3.5 pr-4">{j.company}</td>
                <td className="py-3.5 pr-4 text-mute">{j.source}</td>
                <td className="py-3.5 pr-4 text-right text-[18px] font-bold tracking-[-0.02em] tabular-nums">{j.score ?? <span className="text-mute">—</span>}</td>
                <td className="py-3.5 pr-4"><Badge s={j.status} /></td>
                <td className="py-3.5 pr-4 font-mono text-[12px]">{j.notion_page_id ? 'synced' : <span className="text-mute">—</span>}</td>
                <td className="py-3.5 font-mono text-[12px] text-mute tabular-nums">{(j.applied_at ?? j.discovered_at).slice(0, 10)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {jobs && !rows.length && <p className="py-10 text-[15px] text-mute">{Object.values(f).some(Boolean) ? 'No jobs match these filters.' : 'The register is empty. Run discovery from the Pipeline tab.'}</p>}
        {!jobs && <div className="mt-4 h-40 bg-paper-2" />}
      </div>
    </Section>
  )
}

/* 03 — Log --------------------------------------------------------------- */
function Log() {
  const [logs, setLogs] = useState<any[]>([])
  const [level, setLevel] = useState('')
  useEffect(() => {
    const load = () => api('/logs?limit=200' + (level ? `&level=${level}` : '')).then(setLogs).catch(() => {})
    load(); const t = setInterval(load, 5000); return () => clearInterval(t)
  }, [level])

  return (
    <Section n="03" title="Log" note="Most recent first. Errors never stop the worker; failed jobs can be retried from the Pipeline.">
      <div role="tablist" aria-label="Log level" className="flex gap-5 border-b border-ink pb-2">
        {[['', 'All'], ['error', 'Errors'], ['warn', 'Warnings'], ['info', 'Info']].map(([v, t]) => (
          <button key={t} role="tab" aria-selected={level === v} onClick={() => setLevel(v)}
            className={`text-[12px] font-semibold uppercase tracking-[0.1em] ${level === v ? 'text-ink underline decoration-accent decoration-2 underline-offset-[10px]' : 'text-mute hover:text-ink'}`}>{t}</button>
        ))}
      </div>
      <ol className="max-h-[420px] overflow-auto font-mono text-[12px] leading-relaxed">
        {logs.map(l => (
          <li key={l.id} className="grid grid-cols-[4.5rem_3.5rem_1fr] gap-3 border-b border-hair py-2">
            <span className="text-mute tabular-nums">{l.ts.slice(11, 19)}</span>
            <span className={l.level === 'error' ? 'text-accent' : l.level === 'warn' ? 'text-warn' : 'text-mute'}>{l.level}</span>
            <span className="break-words text-ink-2">{l.message}</span>
          </li>
        ))}
        {!logs.length && <li className="py-8 font-sans text-[14px] text-mute">Nothing logged{level ? ' at this level' : ' yet'}.</li>}
      </ol>
    </Section>
  )
}

export default function Tracker() {
  return <div><Controls /><Register /><Log /></div>
}
