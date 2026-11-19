import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactDiffViewer from 'react-diff-viewer-continued'
import { api, type Job, type JobDetail, type TailorReport } from '../api'
import { Badge, Figure, Kicker, Msg, btn2Cls, btnAccentCls, btnCls, linkBtnCls, pad, useAction, useDark } from '../ui'
import { TrackingPanel } from '../tracking'

const FILTERS = [
  { key: 'all', label: 'All', match: (_: Job) => true },
  { key: 'review', label: 'Needs review', match: (j: Job) => j.status === 'needs_review' },
  { key: 'active', label: 'In progress', match: (j: Job) => ['scraped', 'scored', 'tailoring', 'tailored', 'applying'].includes(j.status) },
  { key: 'applied', label: 'Applied', match: (j: Job) => j.status === 'applied' },
  { key: 'closed', label: 'Closed', match: (j: Job) => ['skipped', 'failed'].includes(j.status) },
] as const

function useDiffStyles() {
  const dark = useDark()
  const v = (d: string, l: string) => (dark ? d : l)
  return {
    dark,
    styles: {
      variables: {
        light: {
          diffViewerBackground: '#f4f2ec', diffViewerColor: '#111', gutterBackground: '#ebe8e0', gutterColor: '#6b6862',
          addedBackground: '#e3efe6', addedGutterBackground: '#d4e7da', wordAddedBackground: '#b9dcc4', addedColor: '#111',
          removedBackground: '#fbe4e6', removedGutterBackground: '#f6d3d7', wordRemovedBackground: '#f2b6bd', removedColor: '#111',
          codeFoldBackground: '#ebe8e0', codeFoldGutterBackground: '#e2dfd6', codeFoldContentColor: '#6b6862', emptyLineBackground: '#f4f2ec',
        },
        dark: {
          diffViewerBackground: '#0f0f0e', diffViewerColor: '#f1efe8', gutterBackground: '#1a1a18', gutterColor: '#8f8b82',
          addedBackground: '#132a1d', addedGutterBackground: '#183424', wordAddedBackground: '#1f4d33', addedColor: '#f1efe8',
          removedBackground: '#3a1418', removedGutterBackground: '#45181d', wordRemovedBackground: '#6b1f28', removedColor: '#f1efe8',
          codeFoldBackground: '#1a1a18', codeFoldGutterBackground: '#22221f', codeFoldContentColor: '#8f8b82', emptyLineBackground: '#0f0f0e',
        },
      },
      contentText: { fontFamily: 'JetBrains Mono, ui-monospace, monospace', fontSize: '12px', lineHeight: '1.6', whiteSpace: 'pre-wrap' as const, wordBreak: 'break-word' as const },
      diffContainer: { tableLayout: 'fixed' as const, width: '100%' },
      gutter: { minWidth: '36px', padding: '0 8px', fontFamily: 'JetBrains Mono, ui-monospace, monospace', fontSize: '11px' },
      line: { borderBottom: `1px solid ${v('#1f1f1c', '#e2dfd6')}` },
    },
  }
}

function TailorAudit({ r }: { r: TailorReport }) {
  const n = (x: number, w: string) => `${x} ${w}${x === 1 ? '' : 's'}`
  return (
    <details className="mt-5 border-t border-hair pt-3">
      <summary className="cursor-pointer text-[13px] text-ink-2">
        <span className="font-semibold text-ink">{n(r.accepted.length, 'edit')}</span> applied of {r.segments} editable ·{' '}
        <span className={r.rejected.length ? 'text-warn' : ''}>{r.rejected.length} rejected</span> ·{' '}
        <span className={r.reverted.length ? 'text-warn' : ''}>{r.reverted.length} reverted</span>
        {r.pages != null && <> · {r.pages}/{r.base_pages} page</>}
      </summary>
      {r.fallback && <p className="mt-3 text-[13px] text-accent">{r.fallback}</p>}
      {r.rejected.length + r.reverted.length > 0 && (
        <ul className="mt-3 space-y-2 text-[13px]">
          {r.rejected.map((x, i) => <li key={'r' + i}><span className="font-mono text-[11px] text-warn">{x.id} rejected</span> <span className="text-ink-2">{x.reason}</span></li>)}
          {r.reverted.map((x, i) => <li key={'v' + i}><span className="font-mono text-[11px] text-warn">{x.id} reverted</span> <span className="text-ink-2">{x.reason}</span></li>)}
        </ul>
      )}
      <p className="mt-3 text-[12px] text-mute">Every edit is checked on its own: no new numbers, tools or names; skills may only be re-ordered; LaTeX must compile and fit the original page count.</p>
    </details>
  )
}

function Review({ id, onChange }: { id: string; onChange: () => void }) {
  const [d, setD] = useState<JobDetail | null>(null)
  const [err, setErr] = useState('')
  const a = useAction(); const cl = useAction()
  const diff = useDiffStyles()
  useEffect(() => { setD(null); setErr(''); api(`/jobs/${id}`).then(setD).catch(e => setErr(e.message)) }, [id])

  if (err) return <p className="font-mono text-[12px] text-accent">{err}</p>
  if (!d) return <div className="space-y-4"><div className="h-4 w-40 bg-paper-2" /><div className="h-12 w-3/4 bg-paper-2" /><div className="h-64 bg-paper-2" /></div>

  const act = (what: 'approve' | 'skip') => a.run(async () => {
    await api(`/pipeline/jobs/${id}/${what}`, { method: 'POST' }); onChange()
    setD({ ...d, status: what === 'approve' ? 'applied' : 'skipped' })
    return what === 'approve' ? 'Submitted.' : 'Skipped.'
  })
  const retry = () => a.run(async () => { const r = await api(`/pipeline/jobs/${id}/retry`, { method: 'POST' }); onChange(); setD({ ...d, status: r.status }); return `Re-queued at “${r.status}”.` })
  const awaiting = d.status === 'needs_review'

  return (
    <article>
      {/* Headline */}
      <Kicker>{d.closed_at ? <span className="text-warn">Closed {d.closed_at.slice(0, 10)} — </span> : ''}{d.job_type === 'internship' ? 'Internship — ' : ''}{d.company} — {d.source}{d.location ? ` — ${d.location}` : ''}{d.is_remote ? ' — remote' : ''}</Kicker>
      <h2 className="mt-3 text-[clamp(32px,4.2vw,56px)] font-bold leading-[0.98] tracking-[-0.035em]">{d.title}</h2>
      <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2">
        <Badge s={d.status} />
        {d.score != null && <span className="text-[13px]"><span className="font-semibold tabular-nums">{d.score}</span><span className="text-mute"> / 100 match</span></span>}
        <a className={linkBtnCls + ' max-w-full truncate'} href={d.url} target="_blank" rel="noreferrer">View posting ↗</a>
      </div>

      {/* Decision bar */}
      <div className={`mt-8 flex flex-wrap items-center gap-3 border-y py-4 ${awaiting ? 'border-accent' : 'border-hair'}`}>
        {awaiting ? <>
          <span className="mr-auto text-[14px] font-medium">Form filled and paused before submit.</span>
          <button className={btnAccentCls} disabled={a.busy} onClick={() => confirm(`Submit the application to ${d.company} now?`) && act('approve')}>Approve &amp; submit</button>
          <button className={btn2Cls} disabled={a.busy} onClick={() => act('skip')}>Skip</button>
        </> : ['failed', 'skipped'].includes(d.status) ? <>
          <span className="mr-auto text-[14px] text-mute">This job is closed.</span>
          <button className={btn2Cls} disabled={a.busy} onClick={retry}>Retry</button>
        </> : <>
          <span className="mr-auto text-[14px] text-mute">{d.status === 'applied' ? `Applied${d.applied_at ? ' on ' + d.applied_at.slice(0, 10) : ''}.` : 'Working — the review opens once the form is filled.'}</span>
          {d.status !== 'applied' && <button className={btn2Cls} disabled={a.busy} onClick={() => act('skip')}>Skip</button>}
        </>}
      </div>
      <Msg m={a.msg} />
      {d.error && <p className="mt-4 border-l-2 border-warn pl-3 text-[13px] leading-relaxed text-warn">{d.error}</p>}

      <TrackingPanel job={d} onSaved={j => { setD({ ...d, ...j }); onChange() }} />

      {/* Notes */}
      <div className="mt-10 grid grid-cols-12 gap-x-6 gap-y-8">
        <div className="col-span-12 md:col-span-7">
          <Kicker>What changed</Kicker>
          <p className="mt-2 text-[19px] leading-[1.45] tracking-[-0.005em]">{d.tailored_summary || <span className="text-mute">Not tailored yet.</span>}</p>
          {!d.tailored_ok && d.tailored_summary && <p className="mt-2 text-[13px] text-accent">Rejected by the safety checks — your untailored résumé is used.</p>}
          {d.tailor_report && <TailorAudit r={d.tailor_report} />}
        </div>
        <div className="col-span-12 md:col-span-5">
          <Kicker>Missing from your résumé</Kicker>
          {d.missing_keywords.length
            ? <ul className="mt-2 flex flex-wrap gap-x-1 text-[15px] leading-relaxed">{d.missing_keywords.map((k, i) => <li key={k}>{k}{i < d.missing_keywords.length - 1 && <span className="text-mute"> /</span>}</li>)}</ul>
            : <p className="mt-2 text-[15px] text-mute">Nothing flagged.</p>}
        </div>
      </div>

      {/* Exhibits */}
      <div className="mt-12 grid grid-cols-12 gap-6">
        <figure className="col-span-12 xl:col-span-7">
          <figcaption className="mb-2 flex items-baseline justify-between border-b border-ink pb-2"><Kicker>Fig. 1 — Résumé diff</Kicker><span className="font-mono text-[11px] text-mute">base → tailored</span></figcaption>
          <div className="max-h-[560px] overflow-auto border border-hair">
            {d.tailored_tex && d.tailored_tex !== d.base_tex
              ? <ReactDiffViewer oldValue={d.base_tex} newValue={d.tailored_tex} splitView={false} showDiffOnly useDarkTheme={diff.dark} styles={diff.styles} />
              : <p className="p-6 text-[14px] text-mute">{d.tailored_tex ? 'No changes — base résumé used as-is.' : 'No tailored version yet.'}</p>}
          </div>
        </figure>
        <figure className="col-span-12 xl:col-span-5">
          <figcaption className="mb-2 flex items-baseline justify-between border-b border-ink pb-2"><Kicker>Fig. 2 — Compiled PDF</Kicker>{d.has_pdf && <a className="font-mono text-[11px] text-mute hover:text-ink" href={`/api/jobs/${id}/pdf`} target="_blank" rel="noreferrer">open ↗</a>}</figcaption>
          {d.has_pdf
            ? <object data={`/api/jobs/${id}/pdf#toolbar=0&view=FitH`} type="application/pdf" className="h-[560px] w-full border border-hair bg-paper-2" aria-label="Tailored résumé PDF">
                <div className="flex h-full flex-col items-start justify-end gap-3 p-6">
                  <span className="text-[48px] font-bold leading-none tracking-[-0.04em]">PDF</span>
                  <span className="max-w-[28ch] text-[13px] text-mute">This browser can’t preview PDFs inline.</span>
                  <a className={btn2Cls} href={`/api/jobs/${id}/pdf`} target="_blank" rel="noreferrer">Open the PDF ↗</a>
                </div>
              </object>
            : <div className="flex h-[200px] items-center justify-center border border-dashed border-hair text-[14px] text-mute">No PDF yet</div>}
        </figure>
      </div>

      {d.has_screenshot && (
        <figure className="mt-12">
          <figcaption className="mb-2 border-b border-ink pb-2"><Kicker>Fig. 3 — Filled application, before submit</Kicker></figcaption>
          <img className="max-h-[720px] w-full border border-hair object-contain object-top" src={`/api/jobs/${id}/screenshot`} alt={`Filled application form for ${d.title} at ${d.company}`} />
        </figure>
      )}

      {/* Job description, as saved when the posting was found */}
      <details className="mt-12 border-t border-ink pt-2" open={!d.tailored_tex}>
        <summary className="flex cursor-pointer flex-wrap items-baseline justify-between gap-3 pb-2">
          <Kicker>Job description</Kicker>
          <span className="font-mono text-[11px] text-mute">
            {d.jd_text ? `${d.jd_text.length.toLocaleString()} chars` : 'not captured'}{d.jd_source ? ` · via ${d.jd_source}` : ''}
            {d.posted_at ? ` · posted ${d.posted_at.slice(0, 10)}` : ''} · seen {d.times_seen}×
          </span>
        </summary>
        {d.jd_text
          ? <div className="max-h-[520px] max-w-[72ch] overflow-auto whitespace-pre-wrap pr-2 text-[15px] leading-[1.65] text-ink-2">{d.jd_text}</div>
          : <p className="text-[14px] text-mute">The page had no readable description.</p>}
      </details>

      {/* Cover letter */}
      <section className="mt-12">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3 border-b border-ink pb-2">
          <Kicker>Cover letter</Kicker>
          <button className={linkBtnCls} disabled={cl.busy} onClick={() => cl.run(async () => { const r = await api(`/jobs/${id}/cover-letter`, { method: 'POST' }); setD({ ...d, cover_letter: r.letter }); return 'Drafted from your résumé and profile only.' })}>
            {cl.busy ? 'Writing…' : d.cover_letter ? 'Redraft' : 'Draft a letter'}
          </button>
        </div>
        <Msg m={cl.msg} />
        {d.cover_letter
          ? <div className="max-w-[68ch] whitespace-pre-wrap text-[16px] leading-[1.65]">{d.cover_letter}</div>
          : !cl.busy && <p className="text-[14px] text-mute">No letter yet.</p>}
      </section>
    </article>
  )
}

export default function Pipeline() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loaded, setLoaded] = useState(false)
  const [sel, setSel] = useState<string | null>(null)
  const [filter, setFilter] = useState<(typeof FILTERS)[number]['key']>('all')
  const [st, setSt] = useState<any>(null)
  const a = useAction()

  const load = useCallback(() => {
    api('/jobs').then(j => { setJobs(j); setLoaded(true) }).catch(() => setLoaded(true))
    api('/pipeline/status').then(setSt).catch(() => {})
  }, [])
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t) }, [load])

  const review = jobs.filter(j => j.status === 'needs_review')
  const shown = useMemo(() => jobs.filter(FILTERS.find(f => f.key === filter)!.match), [jobs, filter])
  // Auto-open the first job awaiting review
  useEffect(() => { if (!sel && review[0]) setSel(review[0].id) }, [sel, review])

  return (
    <div>
      {/* Figures */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-8 md:grid-cols-4">
        <Figure value={pad(jobs.length)} label={`Found · ${jobs.filter(j => j.job_type === 'internship').length} internships`} />
        <Figure value={pad(review.length)} label="Awaiting your review" accent={review.length > 0} />
        <Figure value={pad(jobs.filter(j => ['applied'].includes(j.status)).length)} label="Applied, all time" />
        <Figure value={<>{st?.applied_today ?? 0}<span className="text-mute">/{st?.daily_cap ?? '—'}</span></>} label="Submitted today / cap" />
      </div>

      {/* Controls */}
      <div className="mt-10 flex flex-wrap items-center gap-3 border-t-2 border-rule pt-4">
        <button className={btnCls} disabled={a.busy} onClick={() => a.run(async () => { await api('/pipeline/discover', { method: 'POST' }); setTimeout(load, 1500); return 'Search started — new jobs and internships appear below once it finishes. Progress is under Tracker → Searches.' })}>{st?.searching ? 'Searching…' : 'Run discovery'}</button>
        <button className={btn2Cls} onClick={() => api(`/pipeline/${st?.paused ? 'resume' : 'pause'}`, { method: 'POST' }).then(load)}>{st?.paused ? 'Resume worker' : 'Pause worker'}</button>
        <span className="ml-auto text-[13px] text-mute">
          Mode: <span className={`font-semibold ${st && !st.approval_mode ? 'text-accent' : 'text-ink'}`}>{!st ? '—' : st.approval_mode ? 'Approval gate' : 'Full-auto'}</span>
        </span>
      </div>
      <Msg m={a.msg} />

      {/* Index + review */}
      <div className="mt-10 grid grid-cols-12 gap-x-8 gap-y-10">
        <aside className="col-span-12 lg:col-span-4">
          <div className="flex items-baseline justify-between border-b-2 border-rule pb-2">
            <h2 className="text-[22px] font-bold tracking-[-0.02em]">Index</h2>
            <span className="font-mono text-[12px] text-mute">{pad(shown.length)} / {pad(jobs.length)}</span>
          </div>
          <div role="tablist" aria-label="Filter jobs" className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
            {FILTERS.map(f => (
              <button key={f.key} role="tab" aria-selected={filter === f.key} onClick={() => setFilter(f.key)}
                className={`border-b-2 pb-1 text-[12px] font-semibold uppercase tracking-[0.1em] ${filter === f.key ? 'border-accent text-ink' : 'border-transparent text-mute hover:text-ink'}`}>
                {f.label}{f.key === 'review' && review.length > 0 && <span className="ml-1 text-accent">{review.length}</span>}
              </button>
            ))}
          </div>

          <ol className="mt-4 max-h-[78vh] overflow-auto border-t border-hair">
            {shown.map(j => {
              const on = sel === j.id
              return (
                <li key={j.id} className="border-b border-hair">
                  <button onClick={() => setSel(j.id)} aria-current={on ? 'true' : undefined}
                    className={`grid w-full grid-cols-[2.25rem_1fr_auto] items-start gap-x-3 py-3.5 pr-2 text-left transition-colors ${on ? 'bg-ink pl-2 text-paper' : 'hover:bg-paper-2'}`}>
                    <span className={`pt-0.5 font-mono text-[11px] ${on ? 'text-accent' : 'text-mute'}`}>{pad(jobs.indexOf(j) + 1, 3)}</span>
                    <span className="min-w-0">
                      <span className="block truncate text-[15px] font-semibold leading-tight tracking-[-0.01em]">{j.title}</span>
                      <span className={`mt-1 block truncate text-[12.5px] ${on ? 'text-paper/70' : 'text-mute'}`}>{j.closed_at && <span className={on ? 'text-paper' : 'text-warn'}>Closed · </span>}{j.job_type === 'internship' && <span className={`font-semibold uppercase tracking-[0.08em] ${on ? 'text-paper' : 'text-ink'}`}>Internship · </span>}{j.company} · {j.source}{j.is_easy_apply ? ' · easy apply' : ''}</span>
                      <span className="mt-2 block">{on ? <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-paper/80">{j.status.replace('_', ' ')}</span> : <Badge s={j.status} />}</span>
                    </span>
                    <span className={`text-right text-[26px] font-bold leading-none tracking-[-0.03em] tabular-nums ${j.score == null ? 'opacity-30' : ''}`}>{j.score ?? '—'}</span>
                  </button>
                </li>
              )
            })}
            {loaded && !shown.length && (
              <li className="py-12">
                {jobs.length
                  ? <p className="text-[14px] text-mute">Nothing in this view.</p>
                  : <>
                      <p className="text-[22px] font-bold leading-tight tracking-[-0.02em]">No jobs yet.</p>
                      <p className="mt-2 max-w-[36ch] text-[14px] leading-relaxed text-mute">Add your model, résumé and a few company career pages under <a className="text-ink underline underline-offset-4" href="#setup">Setup</a>, then run discovery.</p>
                    </>}
              </li>
            )}
          </ol>
        </aside>

        <div className="col-span-12 lg:col-span-8">
          {sel ? <Review id={sel} onChange={load} /> : (
            <div className="border-t-2 border-rule pt-6">
              <Kicker>Review</Kicker>
              <p className="mt-3 max-w-[30ch] text-[clamp(28px,3vw,40px)] font-bold leading-[1.05] tracking-[-0.03em] text-mute">
                Select a job from the index to read the diff, the PDF and the filled form.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
