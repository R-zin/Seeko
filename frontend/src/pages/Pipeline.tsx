import { useCallback, useEffect, useState } from 'react'
import ReactDiffViewer from 'react-diff-viewer-continued'
import { api, type Job, type JobDetail } from '../api'
import { Card, Badge, btnCls, btn2Cls, useAction, Msg } from '../ui'

function Review({ id, onChange }: { id: string; onChange: () => void }) {
  const [d, setD] = useState<JobDetail | null>(null); const a = useAction()
  useEffect(() => { setD(null); api(`/jobs/${id}`).then(setD) }, [id])
  if (!d) return <p className="text-sm text-slate-500">Loading…</p>
  const cl = useAction()
  const act = (what: 'approve' | 'skip') => a.run(async () => { await api(`/pipeline/jobs/${id}/${what}`, { method: 'POST' }); onChange(); return what === 'approve' ? 'Submitted' : 'Skipped' })
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h3 className="font-semibold">{d.title} <span className="font-normal text-slate-500">· {d.company}</span></h3>
          <a className="text-xs text-indigo-600 underline" href={d.url} target="_blank" rel="noreferrer">{d.url}</a></div>
        <div className="flex gap-2">
          <button className={btnCls} disabled={a.busy || d.status !== 'needs_review'} onClick={() => confirm('Submit this application now?') && act('approve')}>Approve &amp; Submit</button>
          <button className={btn2Cls} disabled={a.busy} onClick={() => act('skip')}>Skip</button>
        </div>
      </div>
      <Msg m={a.msg} />
      <div className="space-y-2">
        <button className={btn2Cls} disabled={cl.busy} onClick={() => cl.run(async () => { const r = await api(`/jobs/${id}/cover-letter`, { method: 'POST' }); setD({ ...d, cover_letter: r.letter }); return 'Cover letter generated' })}>{cl.busy ? 'Writing…' : d.cover_letter ? 'Regenerate cover letter' : 'Generate cover letter'}</button>
        <Msg m={cl.msg} />
        {d.cover_letter && <textarea readOnly className="w-full rounded border border-slate-200 bg-white p-2 text-sm dark:border-slate-700 dark:bg-slate-900" rows={8} value={d.cover_letter} />}
      </div>
      {d.error && <p className="rounded bg-orange-50 p-2 text-xs text-orange-800 dark:bg-orange-950 dark:text-orange-300">{d.error}</p>}
      {!d.tailored_ok && d.tailored_summary && <p className="text-xs text-amber-700">Tailoring was rejected by safety checks — the untailored base resume is used.</p>}
      <p className="text-sm"><b>Tailoring summary:</b> {d.tailored_summary || '—'}</p>
      {d.missing_keywords.length > 0 && <p className="text-xs text-slate-500">Missing keywords: {d.missing_keywords.join(', ')}</p>}
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="max-h-[520px] overflow-auto rounded border border-slate-200 text-xs dark:border-slate-700">
          {d.tailored_tex ? <ReactDiffViewer oldValue={d.base_tex} newValue={d.tailored_tex} splitView={false} showDiffOnly useDarkTheme={false} /> : <p className="p-3 text-slate-500">No diff yet.</p>}
        </div>
        {d.has_pdf ? <object data={`/api/jobs/${id}/pdf`} type="application/pdf" className="h-[520px] w-full rounded border border-slate-200 dark:border-slate-700"><a href={`/api/jobs/${id}/pdf`}>Open PDF</a></object> : <p className="text-sm text-slate-500">No PDF yet.</p>}
      </div>
      {d.has_screenshot && <details open><summary className="cursor-pointer text-sm font-medium">Filled form screenshot</summary><img className="mt-2 max-h-[600px] rounded border" src={`/api/jobs/${id}/screenshot?${Date.now()}`} /></details>}
    </div>)
}

export default function Pipeline() {
  const [jobs, setJobs] = useState<Job[]>([]); const [sel, setSel] = useState<string | null>(null)
  const [st, setSt] = useState<any>(null); const a = useAction()
  const load = useCallback(() => { api('/jobs').then(setJobs); api('/pipeline/status').then(setSt) }, [])
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t) }, [load])
  const review = jobs.filter(j => j.status === 'needs_review')
  return (
    <div className="space-y-4">
      <Card title="Controls">
        <div className="flex flex-wrap items-center gap-2">
          <button className={btnCls} onClick={() => a.run(async () => { await api('/pipeline/discover', { method: 'POST' }); return 'Discovery started' })}>Run discovery</button>
          <button className={btn2Cls} onClick={() => api(`/pipeline/${st?.paused ? 'resume' : 'pause'}`, { method: 'POST' }).then(load)}>{st?.paused ? 'Resume worker' : 'Pause worker'}</button>
          {st && <span className="text-xs text-slate-500">Mode: <b>{st.approval_mode ? 'approval gate' : 'full-auto'}</b> · applied today {st.applied_today}/{st.daily_cap}</span>}
        </div><Msg m={a.msg} />
      </Card>
      {review.length > 0 && <p className="rounded bg-orange-100 p-2 text-sm text-orange-900">{review.length} application(s) waiting for your review.</p>}
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <Card title={`Jobs (${jobs.length})`}>
          <ul className="max-h-[70vh] divide-y divide-slate-100 overflow-auto dark:divide-slate-800">
            {jobs.map(j => (
              <li key={j.id}><button onClick={() => setSel(j.id)} className={`w-full px-1 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800 ${sel === j.id ? 'bg-indigo-50 dark:bg-indigo-950' : ''}`}>
                <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium">{j.title}</span><Badge s={j.status} /></div>
                <div className="flex justify-between text-xs text-slate-500"><span className="truncate">{j.company} · {j.source}</span>{j.score != null && <span className="font-semibold">{j.score}</span>}</div>
              </button></li>))}
            {!jobs.length && <li className="py-6 text-center text-sm text-slate-500">No jobs yet — set up your profile, then run discovery.</li>}
          </ul>
        </Card>
        <Card title="Review">{sel ? <Review id={sel} onChange={load} /> : <p className="text-sm text-slate-500">Select a job.</p>}</Card>
      </div>
    </div>)
}
