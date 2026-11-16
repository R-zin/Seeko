import { useEffect, useState } from 'react'
import { api, type Job } from '../api'
import { Card, Badge, inputCls, Field, useAction, Msg, btn2Cls } from '../ui'

export default function Tracker() {
  const [jobs, setJobs] = useState<Job[]>([]); const [logs, setLogs] = useState<any[]>([]); const [s, setS] = useState<any>(null)
  const [f, setF] = useState({ status: '', source: '', q: '' }); const a = useAction()
  useEffect(() => { api('/settings').then(setS) }, [])
  useEffect(() => {
    const load = () => { api('/jobs?' + new URLSearchParams(Object.entries(f).filter(([, v]) => v))).then(setJobs); api('/logs?limit=100').then(setLogs) }
    load(); const t = setInterval(load, 5000); return () => clearInterval(t)
  }, [f])
  const STATUSES = ['scraped', 'scored', 'tailoring', 'tailored', 'applying', 'needs_review', 'applied', 'failed', 'skipped']
  return (
    <div className="space-y-4">
      {s && <Card title="Application controls">
        <div className="grid items-end gap-3 sm:grid-cols-5">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!s.approval_mode} onChange={e => setS({ ...s, approval_mode: !e.target.checked })} />Full-auto mode (off = approval gate)</label>
          <Field label="Daily submit cap"><input type="number" className={inputCls} value={s.daily_apply_cap} onChange={e => setS({ ...s, daily_apply_cap: +e.target.value })} /></Field>
          <Field label="Auto-discovery every N min (0 = manual)"><input type="number" min={0} className={inputCls} value={s.discovery_interval_min} onChange={e => setS({ ...s, discovery_interval_min: +e.target.value })} /></Field>
          <Field label="Min match score"><input type="number" className={inputCls} value={s.min_match_score} onChange={e => setS({ ...s, min_match_score: +e.target.value })} /></Field>
          <button className={btn2Cls} onClick={() => a.run(async () => { await api('/settings', { method: 'PUT', json: s }); return 'Saved' })}>Save</button>
        </div>
        {!s.approval_mode && <p className="text-xs text-red-600">Full-auto submits without your review (up to the cap). It still pauses on CAPTCHA, 2FA or unknown required fields.</p>}
        <Msg m={a.msg} />
      </Card>}
      <Card title="Tracker">
        <div className="flex flex-wrap gap-2">
          <select className={inputCls + ' !w-auto'} value={f.status} onChange={e => setF({ ...f, status: e.target.value })}><option value="">All statuses</option>{STATUSES.map(x => <option key={x}>{x}</option>)}</select>
          <select className={inputCls + ' !w-auto'} value={f.source} onChange={e => setF({ ...f, source: e.target.value })}><option value="">All sources</option>{['greenhouse', 'lever', 'ashby', 'workday', 'linkedin', 'indeed', 'glassdoor'].map(x => <option key={x}>{x}</option>)}</select>
          <input className={inputCls + ' !w-56'} placeholder="Search title / company" value={f.q} onChange={e => setF({ ...f, q: e.target.value })} />
        </div>
        <div className="overflow-auto"><table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-500"><tr><th className="p-2">Role</th><th>Company</th><th>Source</th><th>Score</th><th>Status</th><th>Notion</th><th>Applied</th></tr></thead>
          <tbody>{jobs.map(j => <tr key={j.id} className="border-t border-slate-100 dark:border-slate-800"><td className="p-2"><a className="text-indigo-600 hover:underline" href={j.url} target="_blank" rel="noreferrer">{j.title}</a></td><td>{j.company}</td><td>{j.source}</td><td>{j.score ?? '—'}</td><td><Badge s={j.status} /></td><td>{j.notion_page_id ? '✓' : '—'}</td><td>{j.applied_at?.slice(0, 10) ?? ''}</td></tr>)}</tbody>
        </table></div>
      </Card>
      <Card title="Logs">
        <ul className="max-h-72 space-y-1 overflow-auto font-mono text-xs">
          {logs.map(l => <li key={l.id} className={l.level === 'error' ? 'text-red-600' : l.level === 'warn' ? 'text-amber-600' : 'text-slate-600 dark:text-slate-400'}>{l.ts.slice(11, 19)} [{l.level}] {l.message}</li>)}
          {!logs.length && <li className="text-slate-500">No log entries.</li>}
        </ul>
      </Card>
    </div>)
}
