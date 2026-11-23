import { useCallback, useEffect, useMemo, useState } from 'react'
import { APPLICATION_STATUSES, api, type Job, type SearchRun } from '../api'
import { Badge, Kicker, Msg, Section, btn2Cls, btnCls, inputCls, linkBtnCls, pad, toast, useAction } from '../ui'
import { ApplicationSelect, NotionMark } from '../tracking'

const STATUSES = ['scraped', 'scored', 'tailoring', 'tailored', 'applying', 'needs_review', 'applied', 'failed', 'skipped', 'tracked']
const SOURCES = ['greenhouse', 'lever', 'ashby', 'workday', 'careers', 'linkedin', 'indeed', 'glassdoor', 'internshala', 'manual']
type SortKey = 'discovered_at' | 'score' | 'company' | 'title'

const selectCls = inputCls + ' cursor-pointer pr-6 capitalize'

/* Summary strip ---------------------------------------------------------- */
const SENT = ['applied', 'assessment', 'interviewing', 'offer', 'accepted', 'rejected', 'ghosted']
function Stats({ jobs }: { jobs: Job[] | null }) {
  const sent = jobs?.filter(j => SENT.includes(j.application_status)) ?? []
  const n = (k: string[]) => sent.filter(j => k.includes(j.application_status)).length
  const replied = n(['assessment', 'interviewing', 'offer', 'accepted', 'rejected'])
  const tiles: [string, string | number, string, number?][] = [
    ['Applications', sent.length, `${jobs?.length ?? 0} jobs tracked`, jobs?.length ? sent.length / jobs.length : 0],
    ['Interviewing', n(['interviewing']), `${n(['assessment'])} in assessment`],
    ['Offers', n(['offer', 'accepted']), n(['accepted']) ? `${n(['accepted'])} accepted` : 'decision pending'],
    ['Response rate', sent.length ? `${Math.round(100 * replied / sent.length)}%` : '—', `${replied} of ${sent.length} replied`, sent.length ? replied / sent.length : 0],
    ['Awaiting reply', n(['applied']), `${n(['ghosted'])} marked no response`],
  ]
  return (
    <div className="stagger mb-10 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5" aria-label="Summary">
      {tiles.map(([label, value, sub, bar], i) => (
        <div key={label} className="rounded-xl border border-hair p-4 transition-shadow duration-200 hover:shadow-[var(--shadow)]">
          <div className="font-mono text-[10.5px] uppercase tracking-[0.1em] text-mute">{pad(i + 1)} / {label}</div>
          <div className="mt-2 text-[34px] font-semibold leading-none tracking-[-0.04em] tabular-nums">{jobs ? value : <span className="skeleton inline-block h-8 w-14" />}</div>
          <div className="mt-2 font-mono text-[11px] text-mute">{sub}</div>
          {bar !== undefined && <div className="mt-3 h-[3px] overflow-hidden rounded-full bg-paper-3"><div className="h-full rounded-full bg-ink transition-[width] duration-700 ease-out" style={{ width: `${Math.min(1, bar) * 100}%` }} /></div>}
        </div>
      ))}
    </div>
  )
}

/* 02 — Application board ------------------------------------------------- */
const COLUMNS: { key: string; title: string; holds: string[]; note?: string }[] = [
  { key: 'applied', title: 'Applied', holds: ['applied'] },
  { key: 'assessment', title: 'Assessment', holds: ['assessment'] },
  { key: 'interviewing', title: 'Interviewing', holds: ['interviewing'] },
  { key: 'offer', title: 'Offer', holds: ['offer', 'accepted'] },
  { key: 'rejected', title: 'Closed', holds: ['rejected', 'withdrawn', 'ghosted'], note: 'Rejected · withdrawn · no response' },
]

/** Drag a card to another column (or use its menu) to change its application status; it reaches Notion like any edit. */
function Board({ jobs, reload }: { jobs: Job[] | null; reload: () => void }) {
  const [drag, setDrag] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const [moved, setMoved] = useState<Record<string, string>>({})   // optimistic moves until the next reload
  useEffect(() => setMoved({}), [jobs])
  const statusOf = (j: Job) => moved[j.id] ?? j.application_status
  const move = async (j: Job, to: string) => {
    if (COLUMNS.find(c => c.key === to)?.holds.includes(statusOf(j))) return
    setMoved(m => ({ ...m, [j.id]: to }))
    try { await api(`/jobs/${j.id}`, { method: 'PATCH', json: { application_status: to } }); toast(`${j.title} → ${COLUMNS.find(c => c.key === to)?.title}`) }
    catch (e: any) { toast(e.message, false); setMoved(m => { const { [j.id]: _, ...r } = m; return r }) }
    reload()
  }
  const cards = (jobs ?? []).filter(j => COLUMNS.some(c => c.holds.includes(statusOf(j))))
  return (
    <Section n="02" title="Application board" note="Every application you sent, by stage. Drag a card to another column, or use its menu."
      aside={<span className="font-mono text-[12px] text-mute">{cards.length} applications · {COLUMNS.length} columns</span>}>
      {!jobs ? <div className="skeleton h-56" /> : (
        <div className="grid auto-cols-[minmax(13rem,1fr)] grid-flow-col gap-3 overflow-x-auto pb-2">
          {COLUMNS.map(c => {
            const list = cards.filter(j => c.holds.includes(statusOf(j))).sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
            const hot = over === c.key && drag && !c.holds.includes(statusOf(cards.find(j => j.id === drag)!))
            return (
              <div key={c.key} aria-label={`${c.title} column`}
                onDragOver={e => { e.preventDefault(); setOver(c.key) }} onDragLeave={() => setOver(o => o === c.key ? null : o)}
                onDrop={e => { e.preventDefault(); const id = e.dataTransfer.getData('text/plain') || drag; const j = cards.find(x => x.id === id); setOver(null); setDrag(null); if (j) move(j, c.key) }}
                className={`flex min-h-[12rem] flex-col rounded-xl border p-2.5 transition-[background-color,border-color] duration-200 ${hot ? 'border-ink bg-paper-3' : 'border-hair bg-paper-2'}`}>
                <div className="flex items-start justify-between px-1.5 pb-2.5 pt-1">
                  <div><div className="font-mono text-[11px] font-semibold uppercase tracking-[0.1em]">{c.title}</div>
                    {c.note && <div className="text-[10.5px] text-mute">{c.note}</div>}</div>
                  <span className={`rounded-md px-1.5 py-0.5 font-mono text-[11px] ${c.key === 'offer' && list.length ? 'bg-ink text-paper' : 'border border-hair bg-paper'}`}>{list.length}</span>
                </div>
                <ul className="stagger flex flex-1 flex-col gap-2">
                  {list.map(j => {
                    const closed = c.key === 'rejected'
                    return (
                      <li key={j.id} draggable onDragStart={e => { e.dataTransfer.setData('text/plain', j.id); e.dataTransfer.effectAllowed = 'move'; setDrag(j.id) }}
                        onDragEnd={() => { setDrag(null); setOver(null) }}
                        className={`group cursor-grab rounded-lg border bg-paper p-3 transition-[box-shadow,transform,opacity] duration-200 active:cursor-grabbing ${drag === j.id ? 'rotate-1 scale-[1.02] opacity-60 shadow-[var(--shadow)]' : 'border-hair hover:-translate-y-px hover:shadow-[var(--shadow)]'} ${c.key === 'offer' ? 'border-l-[3px] border-l-ink' : ''}`}>
                        <div className="flex items-start justify-between gap-2">
                          <span className={`text-[14px] font-semibold leading-tight ${closed ? 'text-mute line-through' : ''}`}>{j.title}</span>
                          {j.score != null && <span className="rounded bg-paper-2 px-1.5 py-0.5 font-mono text-[11px] font-semibold">{j.score}</span>}
                        </div>
                        <div className="mt-1.5 flex items-center justify-between gap-2 text-[12.5px] text-mute">
                          <span className="truncate">{j.company}</span>
                          <span className="shrink-0 font-mono text-[11px]">{closed ? APPLICATION_STATUSES.find(([k]) => k === statusOf(j))?.[1] ?? statusOf(j)
                            : j.next_step_date ? `next ${j.next_step_date.slice(5)}` : j.applied_at ? j.applied_at.slice(5, 10) : ''}</span>
                        </div>
                        <select aria-label={`Move ${j.title}`} value="" onChange={e => e.target.value && move(j, e.target.value)}
                          className="-mx-1.5 mt-1.5 w-[calc(100%+0.75rem)] cursor-pointer rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-[11.5px] text-mute transition-[border-color,color] duration-150 hover:border-hair hover:text-ink focus:border-ink focus:text-ink group-hover:text-ink-2">
                          <option value="">Move to…</option>{APPLICATION_STATUSES.filter(([k]) => k !== 'not_applied').map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                        </select>
                      </li>
                    )
                  })}
                  {hot && <li className="anim-fade rounded-lg border border-dashed border-ink-2 py-5 text-center font-mono text-[11px] uppercase tracking-[0.1em] text-mute">Drop to move here</li>}
                  {!list.length && !hot && <li className="py-6 text-center text-[12px] text-mute">Nothing here</li>}
                </ul>
              </div>
            )
          })}
        </div>
      )}
    </Section>
  )
}

/* 01 — Agenda ------------------------------------------------------------ */
type AItem = { id: string; title: string; company: string; next_step: string; next_step_date: string | null; url: string
  applied_at: string | null; valid_through: string | null; days?: number; when?: 'overdue' | 'today' | 'soon' }
type AgendaT = { today: string; next_steps: AItem[]; follow_up: AItem[]; apply_by: AItem[]; review: number; questions: number; followup_after_days: number }

function Agenda() {
  const [a, setA] = useState<AgendaT | null>(null)
  const act = useAction()
  const load = useCallback(() => api<AgendaT>('/agenda').then(setA).catch(() => {}), [])
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t) }, [load])
  const mark = (j: AItem, status: string) => act.run(async () => {
    await api(`/jobs/${j.id}`, { method: 'PATCH', json: { application_status: status } }); load()
    return `${j.title}: marked ${status === 'ghosted' ? 'no response' : status}.`
  })
  const col = (title: string, items: AItem[], empty: string, row: (j: AItem) => React.ReactNode) => (
    <div className="rounded-xl border border-hair p-5 transition-shadow duration-200 hover:shadow-[var(--shadow)]">
      <div className="flex items-center justify-between pb-2"><Kicker className="text-ink">{title}</Kicker><span className="rounded-md border border-hair bg-paper-2 px-2 py-0.5 font-mono text-[11px]">{items.length}</span></div>
      <ul className="stagger divide-y divide-hair">{items.map(j => <li key={j.id} className="py-3 text-[13.5px]">{row(j)}</li>)}</ul>
      {!items.length && <p className="py-3 text-[13px] text-mute">{empty}</p>}
    </div>
  )
  const name = (j: AItem) => <a className="font-semibold underline decoration-transparent underline-offset-4 hover:decoration-ink" href={j.url} target="_blank" rel="noreferrer">{j.title}</a>

  return (
    <Section n="01" title="Agenda" note={a ? `What your search needs from you${a.review || a.questions ? '' : ' today'}.` : 'Loading…'}
      aside={<div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1">
        {a && a.review > 0 && <a className="underline underline-offset-4" href="#pipeline">{a.review} form{a.review === 1 ? '' : 's'} waiting →</a>}
        {a && a.questions > 0 && <a className="underline underline-offset-4" href="#setup">{a.questions} question{a.questions === 1 ? '' : 's'} to answer →</a>}
        <button className={btn2Cls} title="Also sent by itself once a day (Setup → Notifications)" disabled={act.busy}
          onClick={() => act.run(async () => { await api('/agenda/digest', { method: 'POST' }); return 'Digest sent.' })}>Send digest now</button>
      </div>}>
      {!a ? <div className="h-32 bg-paper-2" /> : <>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {col('Next steps', a.next_steps, 'Nothing booked this week.', j => <>
            <span className={`inline-block font-mono text-[11px] ${j.when === 'overdue' ? 'rounded bg-ink px-1.5 py-0.5 font-semibold uppercase text-paper' : j.when === 'today' ? 'font-semibold text-ink' : 'text-mute'}`}>
              {j.when === 'overdue' ? '! overdue · ' : ''}{j.next_step_date}{j.when === 'today' ? ' · today' : ''}</span>
            <span className="block">{j.next_step || 'Next step'} — {name(j)} <span className="text-mute">at {j.company}</span></span>
          </>)}
          {col(`Follow up · ${a.followup_after_days}+ days`, a.follow_up, 'No application has gone quiet.', j => <>
            {name(j)} <span className="text-mute">at {j.company} · applied {j.days} days ago</span>
            <span className="mt-2 flex flex-wrap gap-1.5">
              <button className="rounded-md border border-hair px-2 py-1 text-[12px] transition-colors duration-150 hover:border-ink hover:bg-paper-2 active:scale-[.97] disabled:opacity-40" disabled={act.busy} onClick={() => mark(j, 'ghosted')}>No response</button>
              <button className="rounded-md border border-hair px-2 py-1 text-[12px] transition-colors duration-150 hover:border-ink hover:bg-paper-2 active:scale-[.97] disabled:opacity-40" disabled={act.busy} onClick={() => mark(j, 'rejected')}>Rejected</button>
              <button className="rounded-md border border-hair px-2 py-1 text-[12px] transition-colors duration-150 hover:border-ink hover:bg-paper-2 active:scale-[.97] disabled:opacity-40" disabled={act.busy} onClick={() => mark(j, 'interviewing')}>Interviewing</button>
            </span>
          </>)}
          {col('Closing soon', a.apply_by, 'No deadline in the next 5 days.', j => <>
            {name(j)} <span className="text-mute">at {j.company}</span>
            <span className="mt-1 flex items-center justify-between font-mono text-[11px] text-mute">closes {j.valid_through?.slice(0, 10)}
              <span className={`rounded px-1.5 py-0.5 font-semibold ${(j.days ?? 9) <= 2 ? 'border border-ink text-ink' : 'bg-paper-2 text-ink-2'}`}>{j.days === 0 ? 'today' : `${j.days}d left`}</span></span>
          </>)}
        </div>
        <Msg m={act.msg} />
      </>}
    </Section>
  )
}

/* 04 — Controls ---------------------------------------------------------- */
function Controls() {
  const [s, setS] = useState<any>(null)
  const a = useAction()
  useEffect(() => { api('/settings').then(setS) }, [])

  const setMode = (approval: boolean) => {
    if (!approval && !confirm('Full-auto submits applications without your review, up to the daily cap. Continue?')) return
    setS({ ...s, approval_mode: approval })
  }

  const num = (label: string, key: string, unit: string, max?: number) => (
    <label className="flex items-center gap-2 font-mono text-[11.5px] text-mute">{label}
      <span className="flex items-center rounded-md border border-hair bg-paper-2 pr-2 focus-within:border-ink">
        <input type="number" min={0} max={max} aria-label={label} value={s[key]} onChange={e => setS({ ...s, [key]: +e.target.value })}
          className="w-14 bg-transparent px-2 py-1.5 text-right font-semibold text-ink outline-none focus-visible:outline-none" />{unit}</span>
    </label>
  )
  return (
    <Section n="04" title="Controls" note="How much the assistant may do on its own.">
      {!s ? <div className="skeleton h-16" /> : <>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3 rounded-xl border border-hair p-3">
          <div role="radiogroup" aria-label="Submission mode" className="flex rounded-lg bg-paper-2 p-1">
            {[{ v: true, t: 'Approval gate', d: 'Fills every form, then waits for you to approve each submission.' },
              { v: false, t: 'Full-auto', d: 'Submits on its own up to the daily cap. Still pauses on CAPTCHA, 2FA or unknown required fields.' }].map(o => {
              const on = s.approval_mode === o.v
              return <button key={o.t} role="radio" aria-checked={on} title={o.d} onClick={() => setMode(o.v)}
                className={`rounded-md px-3.5 py-1.5 text-[13.5px] font-medium transition-[background-color,color] duration-200 ${on ? 'bg-ink text-paper shadow-[var(--shadow)]' : 'text-ink-2 hover:text-ink'}`}>{o.t}{on ? ' (active)' : ''}</button>
            })}
          </div>
          <span className="hidden h-6 w-px bg-hair sm:block" aria-hidden />
          {num('Daily cap', 'daily_apply_cap', 'apps')}
          {num('Auto-discovery', 'discovery_interval_min', 'min')}
          {num('Match threshold', 'min_match_score', '%', 100)}
          <button className={btnCls + ' ml-auto'} disabled={a.busy} onClick={() => a.run(async () => { await api('/settings', { method: 'PUT', json: s }); return 'Controls saved.' })}>Save</button>
        </div>
        <p className="mt-2 text-[12px] text-mute">{s.approval_mode ? 'Approval gate: nothing is submitted without you.' : `Full-auto: up to ${s.daily_apply_cap} applications a day without review.`} Auto-discovery 0 = only when you press Run.</p>
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

/* 03 — Register ---------------------------------------------------------- */
function Register() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [f, setF] = useState({ status: '', source: '', job_type: '', state: '', application_status: '', q: '' })
  const [err, setErr] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [bulk, setBulk] = useState(false)
  const togglePick = (id: string) => setPicked(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const bulkDo = async (label: string, fn: (id: string) => Promise<unknown>) => {
    setBulk(true); let ok = 0
    for (const id of picked) { try { await fn(id); ok++ } catch {} }
    setBulk(false); setPicked(new Set())
    api('/jobs?' + qs).then(setJobs).catch(() => {})
    toast(`${label}: ${ok} of ${picked.size} job${picked.size === 1 ? '' : 's'}`, ok === picked.size)
  }
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
    <Section n="03" title="Register" note={`Every job Seeko has seen, mirrored to Notion when it is connected. ${jobs ? `${jobs.length}${jobs.length === 500 ? '+' : ''} shown.` : ''}`}
      aside={<div className="flex gap-2">
        <a className={btn2Cls} title="Includes the full job descriptions" href={`/api/jobs/export?format=csv${qs ? '&' + qs : ''}`}>↓ Export CSV</a>
        <a className={btn2Cls} title="Includes the full job descriptions" href={`/api/jobs/export?format=json${qs ? '&' + qs : ''}`}>↓ Export JSON</a>
      </div>}>
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-hair bg-paper-2 p-2">
        <input className={inputCls.replace('w-full ', '') + ' min-w-[14rem] flex-1 py-1.5'} aria-label="Search the register" title='Also searches saved descriptions. "exact phrase", -exclude, OR.'
          placeholder="Filter by role, company or description…" value={f.q} onChange={e => setF({ ...f, q: e.target.value })} />
        {([['state', 'Posting', [['open', 'Open'], ['closed', 'Closed']]],
           ['job_type', 'Type', [['job', 'Jobs'], ['internship', 'Internships']]],
           ['status', 'Status', STATUSES.map(x => [x, x.replace('_', ' ')])],
           ['source', 'Source', SOURCES.map(x => [x, x])],
           ['application_status', 'Application', APPLICATION_STATUSES]] as [keyof typeof f, string, string[][]][]).map(([k, label, opts]) => (
          <select key={k} aria-label={label} value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })}
            className={`cursor-pointer rounded-md border bg-paper px-2.5 py-1.5 text-[13px] transition-colors hover:border-ink ${f[k] ? 'border-ink font-semibold' : 'border-hair'}`}>
            <option value="">{label}: all</option>{opts.map(([v, l]) => <option key={v} value={v}>{label}: {l}</option>)}
          </select>
        ))}
        {Object.values(f).some(Boolean) && <button className={linkBtnCls + ' px-2'} onClick={() => setF({ status: '', source: '', job_type: '', state: '', application_status: '', q: '' })}>Reset</button>}
      </div>
      {err && <Msg m={{ ok: false, text: err }} />}
      {picked.size > 0 && (
        <div role="toolbar" aria-label="Bulk actions" className="anim-toast fixed bottom-6 left-1/2 z-30 flex w-[min(94vw,760px)] -translate-x-1/2 flex-wrap items-center gap-2 rounded-xl border border-ink bg-paper px-4 py-2.5 shadow-[var(--shadow)]">
          <span className="mr-2 text-[13px] font-semibold">{picked.size} selected</span>
          <select aria-label="Set application status" disabled={bulk} className={selectCls.replace(' capitalize', '').replace('w-full ', '') + ' w-60 py-1.5'} value=""
            onChange={e => { const v = e.target.value; if (v) bulkDo(`Marked ${APPLICATION_STATUSES.find(([k]) => k === v)?.[1]}`, id => api(`/jobs/${id}`, { method: 'PATCH', json: { application_status: v } })) }}>
            <option value="">Set application status…</option>{APPLICATION_STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <button className={btn2Cls} disabled={bulk} onClick={() => bulkDo('Skipped', id => api(`/pipeline/jobs/${id}/skip`, { method: 'POST' }))}>Skip</button>
          <button className={btn2Cls} disabled={bulk} onClick={() => bulkDo('Re-queued', id => api(`/pipeline/jobs/${id}/retry`, { method: 'POST' }))}>Retry failed</button>
          <button className={linkBtnCls + ' ml-auto'} onClick={() => setPicked(new Set())}>Clear</button>
        </div>
      )}
      <div className="mt-4 overflow-x-auto rounded-xl border border-hair">
        <table className="w-full min-w-[1060px] border-collapse text-left [&_td:first-child]:pl-4 [&_th:first-child]:pl-4">
          <thead className="border-b border-hair bg-paper-2 text-[11px] text-mute">
            <tr>
              <th scope="col" className="w-8 py-3 pr-2"><input type="checkbox" aria-label="Select all shown"
                checked={rows.length > 0 && rows.every(j => picked.has(j.id))}
                onChange={e => setPicked(e.target.checked ? new Set(rows.map(j => j.id)) : new Set())} /></th>
              <th scope="col" className="w-12 py-3 pr-4 font-semibold uppercase tracking-[0.12em]">No.</th>
              <Th {...th} k="title">Role</Th><Th {...th} k="company">Company</Th><Th {...th}>Type</Th><Th {...th}>Source</Th>
              <Th {...th} k="score" className="text-right">Score</Th><Th {...th}>Status</Th><Th {...th}>Application</Th><Th {...th}>Notion</Th>
              <Th {...th} className="text-right">Seen</Th><Th {...th} k="discovered_at">Found</Th>
            </tr>
          </thead>
          <tbody className="text-[14px]">
            {rows.map((j, i) => (
              <tr key={j.id} className={`border-b border-hair align-baseline transition-colors duration-150 ${picked.has(j.id) ? 'bg-paper-3' : 'hover:bg-paper-2'}`}>
                <td className="py-3.5 pr-2"><input type="checkbox" aria-label={`Select ${j.title}`} checked={picked.has(j.id)} onChange={() => togglePick(j.id)} /></td>
                <td className="py-3.5 pr-4 font-mono text-[11px] text-mute">{pad(i + 1, 3)}</td>
                <td className="min-w-[12rem] py-3.5 pr-4"><a className="font-semibold tracking-[-0.005em] underline decoration-transparent underline-offset-4 hover:decoration-ink" href={j.url} target="_blank" rel="noreferrer">{j.title}</a></td>
                <td className="py-3.5 pr-4">{j.company}</td>
                <td className="py-3.5 pr-4 text-[12px] font-semibold uppercase tracking-[0.08em]">{j.job_type === 'internship' ? 'Internship' : <span className="text-mute">Job</span>}{j.is_remote && <span className="ml-1.5 font-normal normal-case tracking-normal text-mute">· remote</span>}
                  {j.closed_at && <span className="block text-[11px] text-warn" title={`No longer listed or expired since ${j.closed_at.slice(0, 10)}`}>Closed</span>}</td>
                <td className="py-3.5 pr-4 text-mute">{j.source}</td>
                <td className="py-3.5 pr-4 text-right text-[18px] font-semibold tracking-[-0.02em] tabular-nums">{j.score ?? <span className="text-mute">—</span>}</td>
                <td className="py-3.5 pr-4"><Badge s={j.status} /></td>
                <td className="py-1 pr-4 text-[13px]">
                  <ApplicationSelect className="!w-[9.5rem] border-transparent !px-1.5 py-1 text-[13px] hover:border-hair" label={`Application status for ${j.title}`} value={j.application_status} onChange={v => setApplication(j, v)} />
                  {j.next_step_date && <span className="block font-mono text-[11px] text-mute" title={j.next_step || 'Next step'}>next {j.next_step_date}</span>}
                </td>
                <td className="py-3.5 pr-4"><NotionMark j={j} /></td>
                <td className="py-3.5 pr-4 text-right font-mono text-[12px] tabular-nums" title={j.last_seen_at ? `Last seen ${j.last_seen_at.slice(0, 10)}` : undefined}>{j.times_seen}×</td>
                <td className="py-3.5 font-mono text-[12px] text-mute tabular-nums">{(j.applied_at ?? j.discovered_at).slice(0, 10)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {jobs && !rows.length && <p className="px-4 py-10 text-[14px] text-mute">{Object.values(f).some(Boolean) ? 'No jobs match these filters.' : 'The register is empty. Run discovery from the Pipeline tab.'}</p>}
        {!jobs && <div className="mt-4 h-40 bg-paper-2" />}
      </div>
    </Section>
  )
}

/* 05 — Searches --------------------------------------------------------- */
const RUN_TONE: Record<string, string> = { running: 'border border-ink', done: 'bg-paper-3', failed: 'bg-ink text-paper', interrupted: 'border border-dashed border-ink-2' }
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
    <Section n="05" title="Searches" note="Each run skips postings already saved (same link, or same company and title) and stops a source at the first CAPTCHA or login wall."
      aside={d?.running
        ? <button className={btn2Cls} disabled={a.busy} onClick={stop}>Stop search</button>
        : <button className={btnCls} disabled={a.busy} onClick={start}>↻ Search now</button>}>
      <Msg m={a.msg} />
      <div className="mt-4 overflow-x-auto rounded-xl border border-hair">
        <table className="w-full min-w-[760px] border-collapse text-left text-[14px] [&_td:first-child]:pl-4 [&_th:first-child]:pl-4">
          <thead className="border-b border-hair bg-paper-2 text-[11px] uppercase tracking-[0.12em] text-mute">
            <tr>{['Started (UTC)', 'Status', 'Found', 'Known', 'New', 'Rejected', 'Notes'].map((h, i) =>
              <th key={h} scope="col" className={`py-3 pr-4 font-semibold ${i >= 2 && i <= 5 ? 'text-right' : ''}`}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {d?.runs.map(r => {
              const s = r.stats, rej = Object.entries(s.rejected ?? {}), blocked = Object.entries(s.blocked ?? {})
              const skipped = Object.entries(s.skipped_sources ?? {}), live = r.status === 'running' ? s.progress : undefined
              return (
                <tr key={r.id} className={`border-b border-hair align-baseline last:border-0 ${r.status === 'running' ? 'bg-paper-2' : ''}`}>
                  <td className="py-3 pr-4 font-mono text-[12px] tabular-nums">{r.status === 'running' && <i className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-ink pulse align-middle" aria-hidden />}{when(r.started_at)}<span className="ml-2 text-mute">{r.trigger}</span></td>
                  <td className="py-3 pr-4 text-[11px] font-semibold uppercase tracking-[0.1em]">
                    <span className={`rounded px-1.5 py-0.5 ${RUN_TONE[r.status] ?? ''}`}>{r.status}</span>
                    {live && <>
                      <span className="mt-1 block font-normal normal-case tracking-normal text-mute">{live.step}…</span>
                      <span className="mt-1.5 block h-[3px] w-28 overflow-hidden rounded-full bg-paper-3"><span className="block h-full rounded-full bg-ink transition-[width] duration-700"
                        style={{ width: `${Math.min(95, 15 + 80 * Math.max(0, ['planning', 'collecting leads', 'reading postings', 'screening', 'saving', 'finishing'].indexOf(live.step)) / 5)}%` }} /></span>
                    </>}
                    {(r.status === 'interrupted' || r.status === 'failed') && <button className={linkBtnCls + ' ml-3 normal-case tracking-normal'} disabled={a.busy || !!d?.running} onClick={() => resume(r.id)}>Resume</button>}
                  </td>
                  <td className="py-3 pr-4 text-right tabular-nums">{s.leads ?? live?.leads ?? '—'}{live && !!live.read && <span className="block text-[11px] text-mute">{live.read} read</span>}</td>
                  <td className="py-3 pr-4 text-right tabular-nums text-mute">{s.known ?? '—'}</td>
                  <td className="py-3 pr-4 text-right tabular-nums">
                    <span className="text-[18px] font-semibold tracking-[-0.02em]">{s.new ?? '—'}</span>
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
        {d && !d.runs.length && <p className="px-4 py-8 text-[14px] text-mute">No searches yet. Set target roles and sources under Setup, then search.</p>}
        {!d && <div className="mt-4 h-24 bg-paper-2" />}
      </div>
    </Section>
  )
}

/* 06 — Log --------------------------------------------------------------- */
function Log() {
  const [logs, setLogs] = useState<any[]>([])
  const [level, setLevel] = useState('')
  const [counts, setCounts] = useState<Record<string, number>>({})
  useEffect(() => {   // per-level counts over the latest 200 entries, for the filter pills
    const load = () => api<any[]>('/logs?limit=200').then(ls => setCounts(ls.reduce((c, l) => ({ ...c, [l.level]: (c[l.level] ?? 0) + 1 }), { error: 0, warn: 0, info: 0 }))).catch(() => {})
    load(); const t = setInterval(load, 15000); return () => clearInterval(t)
  }, [])
  useEffect(() => {
    const load = () => api('/logs?limit=200' + (level ? `&level=${level}` : '')).then(setLogs).catch(() => {})
    load(); const t = setInterval(load, 5000); return () => clearInterval(t)
  }, [level])

  return (
    <Section n="06" title="Log" note="Most recent first. Errors never stop the worker; failed jobs can be retried from the Pipeline."
      aside={<div role="tablist" aria-label="Log level" className="flex gap-1.5">
        {[['', 'All'], ['error', 'Errors'], ['warn', 'Warnings'], ['info', 'Info']].map(([v, t]) => (
          <button key={t} role="tab" aria-selected={level === v} onClick={() => setLevel(v)}
            className={`rounded-md border px-2.5 py-1 font-mono text-[11.5px] transition-[background-color,color,border-color] duration-200 ${level === v ? 'border-ink bg-ink text-paper' : 'border-hair text-ink-2 hover:border-ink'}`}>
            {t}{v && counts[v] !== undefined ? ` (${counts[v]})` : ''}</button>
        ))}
      </div>}>
      <ol className="max-h-[420px] overflow-auto rounded-xl border border-hair bg-paper-2 px-4 py-2 font-mono text-[12px] leading-relaxed">
        {logs.map(l => (
          <li key={l.id} className="grid grid-cols-[4.5rem_3.5rem_1fr] gap-3 border-b border-hair/60 py-1.5 last:border-0">
            <span className="text-mute tabular-nums">{l.ts.slice(11, 19)}</span>
            <span className={l.level === 'error' ? 'font-semibold text-ink' : l.level === 'warn' ? 'text-ink-2' : 'text-mute'}>{l.level === 'error' ? '! error' : l.level}</span>
            <span className="break-words text-ink-2">{l.message}</span>
          </li>
        ))}
        {!logs.length && <li className="py-8 font-sans text-[14px] text-mute">Nothing logged{level ? ' at this level' : ' yet'}.</li>}
      </ol>
    </Section>
  )
}

export default function Tracker() {
  const [all, setAll] = useState<Job[] | null>(null)   // every job (stats + board); the register has its own filtered list
  const reload = useCallback(() => api<Job[]>('/jobs').then(setAll).catch(() => setAll([])), [])
  useEffect(() => { reload(); const t = setInterval(reload, 8000); return () => clearInterval(t) }, [reload])
  return <div><Stats jobs={all} /><Agenda /><Board jobs={all} reload={reload} /><Register /><Controls /><Searches /><Log /></div>
}
