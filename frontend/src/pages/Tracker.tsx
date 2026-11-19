import { useCallback, useEffect, useMemo, useState } from 'react'
import { APPLICATION_STATUSES, api, type Job, type SearchRun } from '../api'
import { Badge, Field, Kicker, Msg, Section, btn2Cls, btnCls, inputCls, linkBtnCls, pad, useAction } from '../ui'
import { ApplicationSelect, NotionMark } from '../tracking'

const STATUSES = ['scraped', 'scored', 'tailoring', 'tailored', 'applying', 'needs_review', 'applied', 'failed', 'skipped']
const SOURCES = ['greenhouse', 'lever', 'ashby', 'workday', 'careers', 'linkedin', 'indeed', 'glassdoor', 'internshala']
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

type Sort = { k: SortKey; desc: boolean }

/** Column head; sortable when `k` is given. Module-level so React keeps its identity between renders. */
function Th({ k, sort, onSort, children, className = '' }: { k?: SortKey; sort: Sort; onSort: (s: Sort) => void; children: React.ReactNode; className?: string }) {
  return (
    <th scope="col" className={`py-3 pr-4 font-semibold ${className}`} aria-sort={k && sort.k === k ? (sort.desc ? 'descending' : 'ascending') : undefined}>
      {k ? <button className="inline-flex items-center gap-1 uppercase tracking-[0.12em] hover:text-ink" onClick={() => onSort({ k, desc: sort.k === k ? !sort.desc : k !== 'company' && k !== 'title' })}>
        {children}<span className={sort.k === k ? 'text-accent' : 'opacity-0'}>{sort.desc ? '↓' : '↑'}</span></button>
        : <span className="uppercase tracking-[0.12em]">{children}</span>}
    </th>
  )
}

/* 02 — Register ---------------------------------------------------------- */
function Register() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [f, setF] = useState({ status: '', source: '', job_type: '', state: '', application_status: '', q: '' })
  const [err, setErr] = useState('')
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString()
  const [sort, setSort] = useState<Sort>({ k: 'discovered_at', desc: true })
  const th = { sort, onSort: setSort }

  useEffect(() => {
    const load = () => api('/jobs?' + qs).then(setJobs).catch(() => setJobs([]))
    const t0 = setTimeout(load, 200); const t = setInterval(load, 5000)
    return () => { clearTimeout(t0); clearInterval(t) }
  }, [qs])

  const rows = useMemo(() => {
    if (!jobs) return []
    const dir = sort.desc ? -1 : 1
    return [...jobs].sort((a, b) => {
      const x = a[sort.k] ?? -1, y = b[sort.k] ?? -1
      return (x < y ? -1 : x > y ? 1 : 0) * dir
    })
  }, [jobs, sort])

  const setApplication = async (j: Job, v: string) => {
    setErr('')
    try {
      const u = await api<Job>(`/jobs/${j.id}`, { method: 'PATCH', json: { application_status: v } })
      setJobs(js => js?.map(x => x.id === u.id ? u : x) ?? js)
    } catch (e: any) { setErr(e.message) }
  }

  return (
    <Section n="02" title="Register" note="Every job Seeko has seen, mirrored to Notion when it is connected.">
      <div className="grid grid-cols-1 gap-x-6 gap-y-5 sm:grid-cols-2 xl:grid-cols-[minmax(10rem,1fr)_6.5rem_7rem_8rem_8rem_8.5rem]">
        <Field label="Search" hint='Also searches saved descriptions. "exact phrase", -exclude, OR.'><input className={inputCls} placeholder="Title, company or description" value={f.q} onChange={e => setF({ ...f, q: e.target.value })} /></Field>
        <Field label="Posting">
          <select className={selectCls} value={f.state} onChange={e => setF({ ...f, state: e.target.value })}>
            <option value="">All</option><option value="open">Open</option><option value="closed">Closed</option>
          </select>
        </Field>
        <Field label="Type">
          <select className={selectCls} value={f.job_type} onChange={e => setF({ ...f, job_type: e.target.value })}>
            <option value="">All</option><option value="job">Jobs</option><option value="internship">Internships</option>
          </select>
        </Field>
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
        <Field label="Application">
          <select className={selectCls.replace(' capitalize', '')} value={f.application_status} onChange={e => setF({ ...f, application_status: e.target.value })}>
            <option value="">All</option>{APPLICATION_STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Field>
      </div>

      <div className="mt-6 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[13px]">
        <span className="text-mute">{jobs ? `${jobs.length}${jobs.length === 500 ? '+' : ''} shown` : ''}</span>
        <a className={linkBtnCls} href={`/api/jobs/export?format=csv${qs ? '&' + qs : ''}`}>Export CSV ↓</a>
        <a className={linkBtnCls} href={`/api/jobs/export?format=json${qs ? '&' + qs : ''}`}>Export JSON ↓</a>
        <span className="text-[12px] text-mute">Includes the full job descriptions.</span>
      </div>
      {err && <Msg m={{ ok: false, text: err }} />}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[900px] border-collapse text-left">
          <thead className="border-y-2 border-rule text-[11px] text-mute">
            <tr>
              <th scope="col" className="w-12 py-3 pr-4 font-semibold uppercase tracking-[0.12em]">No.</th>
              <Th {...th} k="title">Role</Th><Th {...th} k="company">Company</Th><Th {...th}>Type</Th><Th {...th}>Source</Th>
              <Th {...th} k="score" className="text-right">Score</Th><Th {...th}>Status</Th><Th {...th}>Application</Th><Th {...th}>Notion</Th>
              <Th {...th} className="text-right">Seen</Th><Th {...th} k="discovered_at">Found</Th>
            </tr>
          </thead>
          <tbody className="text-[14px]">
            {rows.map((j, i) => (
              <tr key={j.id} className="border-b border-hair align-baseline hover:bg-paper-2">
                <td className="py-3.5 pr-4 font-mono text-[11px] text-mute">{pad(i + 1, 3)}</td>
                <td className="py-3.5 pr-4"><a className="font-semibold tracking-[-0.005em] underline decoration-transparent underline-offset-4 hover:decoration-accent" href={j.url} target="_blank" rel="noreferrer">{j.title}</a></td>
                <td className="py-3.5 pr-4">{j.company}</td>
                <td className="py-3.5 pr-4 text-[12px] font-semibold uppercase tracking-[0.08em]">{j.job_type === 'internship' ? 'Internship' : <span className="text-mute">Job</span>}{j.is_remote && <span className="ml-1.5 font-normal normal-case tracking-normal text-mute">· remote</span>}
                  {j.closed_at && <span className="block text-[11px] text-warn" title={`No longer listed or expired since ${j.closed_at.slice(0, 10)}`}>Closed</span>}</td>
                <td className="py-3.5 pr-4 text-mute">{j.source}</td>
                <td className="py-3.5 pr-4 text-right text-[18px] font-bold tracking-[-0.02em] tabular-nums">{j.score ?? <span className="text-mute">—</span>}</td>
                <td className="py-3.5 pr-4"><Badge s={j.status} /></td>
                <td className="py-1 pr-4 text-[13px]">
                  <ApplicationSelect className="w-[8.25rem] border-transparent py-1 text-[13px] hover:border-hair" label={`Application status for ${j.title}`} value={j.application_status} onChange={v => setApplication(j, v)} />
                  {j.next_step_date && <span className="block font-mono text-[11px] text-mute" title={j.next_step || 'Next step'}>next {j.next_step_date}</span>}
                </td>
                <td className="py-3.5 pr-4"><NotionMark j={j} /></td>
                <td className="py-3.5 pr-4 text-right font-mono text-[12px] tabular-nums" title={j.last_seen_at ? `Last seen ${j.last_seen_at.slice(0, 10)}` : undefined}>{j.times_seen}×</td>
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

/* 03 — Searches --------------------------------------------------------- */
const RUN_TONE: Record<string, string> = { running: 'text-ink', done: 'text-ok', failed: 'text-accent', interrupted: 'text-warn' }
const when = (iso: string) => iso.slice(0, 16).replace('T', ' ')

function Searches() {
  const [d, setD] = useState<{ running: boolean; runs: SearchRun[] } | null>(null)
  const a = useAction()
  const load = useCallback(() => api('/search/runs?limit=15').then(setD).catch(() => setD({ running: false, runs: [] })), [])
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [load])
  const start = () => a.run(async () => { const r = await api('/search/runs', { method: 'POST' }); setTimeout(load, 800); return `Search ${r.run_id} started.` })
  const resume = (id: string) => a.run(async () => { await api(`/search/runs/${id}/resume`, { method: 'POST' }); setTimeout(load, 800); return `Resuming ${id} from its last checkpoint.` })
  const stop = () => a.run(async () => { const r = await api('/search/cancel', { method: 'POST' }); setTimeout(load, 800); return `Stopping ${r.run_id} — it can be resumed later.` })

  return (
    <Section n="03" title="Searches" note="Each run browses your sources, skips anything already in the database, and saves new jobs and internships with their full description.">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-ink pb-4">
        {d?.running
          ? <button className={btn2Cls} disabled={a.busy} onClick={stop}>Stop search</button>
          : <button className={btnCls} disabled={a.busy} onClick={start}>Search now</button>}
        <span className="max-w-[60ch] text-[13px] text-mute">Known postings are recognised by link or by company and title, and are not opened again. A CAPTCHA or login wall stops that source for the run.</span>
      </div>
      <Msg m={a.msg} />
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left text-[14px]">
          <thead className="border-b-2 border-rule text-[11px] uppercase tracking-[0.12em] text-mute">
            <tr>{['Started (UTC)', 'Status', 'Found', 'Known', 'New', 'Rejected', 'Notes'].map((h, i) =>
              <th key={h} scope="col" className={`py-3 pr-4 font-semibold ${i >= 2 && i <= 5 ? 'text-right' : ''}`}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {d?.runs.map(r => {
              const s = r.stats, rej = Object.entries(s.rejected ?? {}), blocked = Object.entries(s.blocked ?? {})
              const skipped = Object.entries(s.skipped_sources ?? {}), live = r.status === 'running' ? s.progress : undefined
              return (
                <tr key={r.id} className="border-b border-hair align-baseline">
                  <td className="py-3 pr-4 font-mono text-[12px] tabular-nums">{when(r.started_at)}<span className="ml-2 text-mute">{r.trigger}</span></td>
                  <td className={`py-3 pr-4 text-[11px] font-semibold uppercase tracking-[0.1em] ${RUN_TONE[r.status] ?? ''}`}>
                    {r.status}
                    {live && <span className="mt-1 block font-normal normal-case tracking-normal text-mute">{live.step}…</span>}
                    {(r.status === 'interrupted' || r.status === 'failed') && <button className={linkBtnCls + ' ml-3 normal-case tracking-normal'} disabled={a.busy || !!d?.running} onClick={() => resume(r.id)}>Resume</button>}
                  </td>
                  <td className="py-3 pr-4 text-right tabular-nums">{s.leads ?? live?.leads ?? '—'}{live && !!live.read && <span className="block text-[11px] text-mute">{live.read} read</span>}</td>
                  <td className="py-3 pr-4 text-right tabular-nums text-mute">{s.known ?? '—'}</td>
                  <td className="py-3 pr-4 text-right tabular-nums">
                    <span className="text-[18px] font-bold tracking-[-0.02em]">{s.new ?? '—'}</span>
                    {!!s.new && <span className="block text-[11px] text-mute">{s.new_jobs ?? 0} jobs · {s.new_internships ?? 0} internships</span>}
                  </td>
                  <td className="py-3 pr-4 text-right tabular-nums" title={rej.map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join('\n') || undefined}>
                    {rej.reduce((t, [, v]) => t + v, 0) || <span className="text-mute">0</span>}
                  </td>
                  <td className="py-3 text-[12.5px] leading-snug">
                    {blocked.map(([src, why]) => <span key={src} className="block text-accent">{src}: {why}</span>)}
                    {skipped.map(([src, why]) => <span key={src} className="block text-warn">{src} skipped: {why}</span>)}
                    {!!s.closed && <span className="block text-mute">{s.closed} posting{s.closed === 1 ? '' : 's'} closed</span>}
                    {!!s.deferred && <span className="block text-mute">{s.deferred} left for next run</span>}
                    {r.error && <span className="block text-warn">{r.error}</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {d && !d.runs.length && <p className="py-8 text-[15px] text-mute">No searches yet. Set target roles and sources under Setup, then search.</p>}
        {!d && <div className="mt-4 h-24 bg-paper-2" />}
      </div>
    </Section>
  )
}

/* 04 — Log --------------------------------------------------------------- */
function Log() {
  const [logs, setLogs] = useState<any[]>([])
  const [level, setLevel] = useState('')
  useEffect(() => {
    const load = () => api('/logs?limit=200' + (level ? `&level=${level}` : '')).then(setLogs).catch(() => {})
    load(); const t = setInterval(load, 5000); return () => clearInterval(t)
  }, [level])

  return (
    <Section n="04" title="Log" note="Most recent first. Errors never stop the worker; failed jobs can be retried from the Pipeline.">
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
  return <div><Controls /><Register /><Searches /><Log /></div>
}
