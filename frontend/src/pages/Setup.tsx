import { useEffect, useState } from 'react'
import { api } from '../api'
import { Card, Field, inputCls, btnCls, btn2Cls, useAction, Msg } from '../ui'

const split = (s: string) => s.split(',').map(x => x.trim()).filter(Boolean)

function LLMCard() {
  const [s, setS] = useState<any>(null); const save = useAction(); const test = useAction()
  useEffect(() => { api('/llm/settings').then(setS) }, [])
  if (!s) return null
  const set = (k: string) => (e: any) => setS({ ...s, [k]: e.target.value })
  return (
    <Card title="LLM endpoint (any OpenAI-compatible /chat/completions)">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Base URL (blank = OpenAI default)"><input className={inputCls} value={s.llm_base_url} onChange={set('llm_base_url')} placeholder="http://localhost:11434/v1" /></Field>
        <Field label="API key (blank OK for local servers)"><input className={inputCls} type="password" value={s.llm_api_key} onChange={set('llm_api_key')} /></Field>
        <Field label="Model"><input className={inputCls} value={s.llm_model} onChange={set('llm_model')} /></Field>
        <Field label="Extra headers (JSON)"><input className={inputCls} value={s.llm_extra_headers} onChange={set('llm_extra_headers')} placeholder='{"X-Title":"Seeko"}' /></Field>
        <Field label="Scoring model override"><input className={inputCls} value={s.scoring_model} onChange={set('scoring_model')} /></Field>
        <Field label="Tailoring model override"><input className={inputCls} value={s.tailor_model} onChange={set('tailor_model')} /></Field>
        <Field label="Applier model override"><input className={inputCls} value={s.applier_model} onChange={set('applier_model')} /></Field>
      </div>
      <div className="flex gap-2">
        <button className={btn2Cls} disabled={save.busy} onClick={() => save.run(async () => { await api('/llm/settings', { method: 'PUT', json: s }); return 'Saved' })}>Save</button>
        <button className={btnCls} disabled={test.busy} onClick={() => test.run(async () => {
          await api('/llm/settings', { method: 'PUT', json: s })
          const r = await api('/llm/test', { method: 'POST' })
          const lines = Object.entries<any>(r.checks).map(([k, v]) => `${v.ok ? '✓' : '✗'} ${k}${v.latency_ms != null ? ` ${v.latency_ms}ms` : ''}${v.tier ? ` (${v.tier})` : ''}${v.error ? ` — ${v.error}` : ''}`)
          if (!r.ok) throw new Error(lines.join('\n')); return lines.join('\n') })}>{test.busy ? 'Testing…' : 'Test endpoint'}</button>
      </div>
      <Msg m={save.msg} /><Msg m={test.msg} />
    </Card>)
}

function ProfileCard() {
  const [p, setP] = useState<any>(null); const a = useAction()
  useEffect(() => { api('/profile').then(setP) }, [])
  if (!p) return null
  const up = (sec: string, k: string, v: any) => setP({ ...p, [sec]: { ...p[sec], [k]: v } })
  const txt = (sec: string, k: string, label: string) => <Field label={label}><input className={inputCls} value={p[sec][k]} onChange={e => up(sec, k, e.target.value)} /></Field>
  const list = (sec: string, k: string, label: string) => <Field label={`${label} (comma-separated)`}><input className={inputCls} value={p[sec][k].join(', ')} onChange={e => up(sec, k, split(e.target.value))} /></Field>
  return (
    <Card title="Candidate profile">
      <div className="grid gap-3 sm:grid-cols-3">
        {txt('personal', 'name', 'Name')}{txt('personal', 'email', 'Email')}{txt('personal', 'phone', 'Phone')}{txt('personal', 'location', 'Location')}
        {txt('personal', 'linkedin', 'LinkedIn')}{txt('personal', 'github', 'GitHub')}{txt('personal', 'portfolio', 'Portfolio')}
        {txt('application', 'work_authorization', 'Work authorization')}{txt('application', 'salary_expectation', 'Salary expectation')}{txt('application', 'notice_period', 'Notice period')}
        <Field label="Years of experience"><input type="number" className={inputCls} value={p.application.years_experience} onChange={e => up('application', 'years_experience', +e.target.value)} /></Field>
        <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" checked={p.application.needs_sponsorship} onChange={e => up('application', 'needs_sponsorship', e.target.checked)} />Needs visa sponsorship</label>
        {list('search', 'role_titles', 'Target role titles')}{list('search', 'keywords', 'Keywords')}{list('search', 'locations', 'Locations')}{list('search', 'excluded_companies', 'Excluded companies')}
        <Field label="Min match score"><input type="number" className={inputCls} value={p.search.min_match_score} onChange={e => up('search', 'min_match_score', +e.target.value)} /></Field>
        <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" checked={p.search.remote_only} onChange={e => up('search', 'remote_only', e.target.checked)} />Remote only</label>
      </div>
      <Field label="Company career URLs — Greenhouse / Lever / Ashby / Workday (one per line)">
        <textarea className={inputCls} rows={4} value={p.search.watchlist_urls.join('\n')} onChange={e => up('search', 'watchlist_urls', e.target.value.split('\n').map((x: string) => x.trim()).filter(Boolean))} />
      </Field>
      <button className={btnCls} disabled={a.busy} onClick={() => a.run(async () => { await api('/profile', { method: 'PUT', json: p }); return 'Profile saved' })}>Save profile</button><Msg m={a.msg} />
    </Card>)
}

function ResumeCard() {
  const [tex, setTex] = useState(''); const a = useAction()
  useEffect(() => { api('/resume').then(r => setTex(r.tex)) }, [])
  return (
    <Card title="Base resume (LaTeX)">
      <input type="file" accept=".tex" className="text-sm" onChange={async e => { const f = e.target.files?.[0]; if (f) setTex(await f.text()) }} />
      <textarea className={inputCls + ' font-mono text-xs'} rows={14} value={tex} onChange={e => setTex(e.target.value)} />
      <button className={btnCls} disabled={a.busy || !tex} onClick={() => a.run(async () => { const f = new FormData(); f.append('file', new Blob([tex]), 'base_resume.tex'); await api('/resume', { method: 'PUT', body: f }); return 'Saved and compiled OK' })}>Save &amp; compile</button><Msg m={a.msg} />
    </Card>)
}

function AccountsCard() {
  const [s, setS] = useState<any>(null); const a = useAction(); const n = useAction()
  useEffect(() => { api('/settings').then(setS) }, [])
  if (!s) return null
  const SRC = ['watchlist', 'linkedin', 'indeed', 'glassdoor']
  return (
    <Card title="Sources, job-board logins & Notion">
      <p className="text-xs text-amber-700 dark:text-amber-400">LinkedIn, Indeed and Glassdoor restrict automation in their terms and may limit your account. They are off by default; keep volumes low.</p>
      <div className="flex flex-wrap gap-4">{SRC.map(x => (
        <label key={x} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.enabled_sources.includes(x)} onChange={e => setS({ ...s, enabled_sources: e.target.checked ? [...s.enabled_sources, x] : s.enabled_sources.filter((y: string) => y !== x) })} />{x}</label>))}</div>
      <div className="flex flex-wrap gap-2">{['linkedin', 'indeed', 'glassdoor'].map(x => (
        <button key={x} className={btn2Cls} onClick={() => a.run(async () => { await api(`/pipeline/login/${x}`, { method: 'POST' }); return `Opened ${x} — sign in, then close that window.` })}>Log in to {x}</button>))}</div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Notion integration token"><input className={inputCls} type="password" value={s.notion_token} onChange={e => setS({ ...s, notion_token: e.target.value })} /></Field>
        <Field label="Notion database ID"><input className={inputCls} value={s.notion_database_id} onChange={e => setS({ ...s, notion_database_id: e.target.value })} /></Field>
      </div>
      <div className="flex gap-2">
        <button className={btn2Cls} onClick={() => a.run(async () => { await api('/settings', { method: 'PUT', json: s }); return 'Saved' })}>Save</button>
        <button className={btnCls} disabled={n.busy} onClick={() => n.run(async () => {
          await api('/settings', { method: 'PUT', json: s }); const r = await api('/notion/test', { method: 'POST' })
          if (!r.ok) throw new Error(r.error ?? `Schema problems — missing: ${r.missing?.join(', ') || 'none'}; wrong type: ${JSON.stringify(r.wrong_type)}`)
          return `Connected. ${r.tools.length} MCP tools; schema OK` })}>{n.busy ? 'Connecting…' : 'Test Notion MCP'}</button>
      </div>
      <Msg m={a.msg} /><Msg m={n.msg} />
    </Card>)
}

export default function Setup() { return <div className="space-y-4"><LLMCard /><ResumeCard /><ProfileCard /><AccountsCard /></div> }
