import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Check, Field, Kicker, Msg, Section, areaCls, btn2Cls, btnCls, inputCls, linkBtnCls, monoAreaCls, useAction } from '../ui'

/** Comma-separated list input that keeps the raw text while typing (commas would otherwise vanish). */
function ListInput({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [raw, setRaw] = useState(value.join(', '))
  const focused = useRef(false)
  useEffect(() => { if (!focused.current) setRaw(value.join(', ')) }, [value])
  return (
    <input className={inputCls} value={raw} placeholder={placeholder}
      onFocus={() => (focused.current = true)}
      onBlur={() => { focused.current = false; setRaw(value.join(', ')) }}
      onChange={e => { setRaw(e.target.value); onChange(e.target.value.split(',').map(x => x.trim()).filter(Boolean)) }} />
  )
}

const Skeleton = () => <div className="space-y-6">{[0, 1, 2].map(i => <div key={i} className="h-10 bg-paper-2" />)}</div>

/* 01 — Model ------------------------------------------------------------- */
function ModelSection() {
  const [s, setS] = useState<any>(null)
  const [more, setMore] = useState(false)
  const save = useAction(); const test = useAction()
  const [checks, setChecks] = useState<[string, any][] | null>(null)
  useEffect(() => { api('/llm/settings').then(v => { setS(v); setMore(!!(v.scoring_model || v.tailor_model || v.applier_model || v.llm_extra_headers)) }) }, [])
  const set = (k: string) => (e: any) => setS({ ...s, [k]: e.target.value })

  return (
    <Section n="01" title="Model" note="Any OpenAI-compatible /chat/completions endpoint — OpenAI, OpenRouter, Ollama, vLLM, LM Studio, Groq, LiteLLM.">
      {!s ? <Skeleton /> : <>
        <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2">
          <Field label="Base URL" hint="Blank uses the OpenAI default. Most servers need the trailing /v1."><input className={inputCls} value={s.llm_base_url} onChange={set('llm_base_url')} placeholder="http://localhost:11434/v1" /></Field>
          <Field label="Model"><input className={inputCls} value={s.llm_model} onChange={set('llm_model')} placeholder="gpt-4o-mini" /></Field>
          <Field label="API key" hint="Leave blank for keyless local servers. Stored locally, never committed."><input className={inputCls} type="password" autoComplete="off" value={s.llm_api_key} onChange={set('llm_api_key')} /></Field>
        </div>

        <button className={linkBtnCls + ' mt-6'} onClick={() => setMore(!more)} aria-expanded={more}>{more ? '− Fewer options' : '+ Per-task models & headers'}</button>
        {more && (
          <div className="mt-5 grid grid-cols-1 gap-x-8 gap-y-6 border-l-2 border-hair pl-5 sm:grid-cols-2">
            <Field label="Scoring model" hint="Fast/cheap is fine."><input className={inputCls} value={s.scoring_model} onChange={set('scoring_model')} placeholder="inherits default" /></Field>
            <Field label="Tailoring model" hint="Use your strongest model."><input className={inputCls} value={s.tailor_model} onChange={set('tailor_model')} placeholder="inherits default" /></Field>
            <Field label="Form-filling model"><input className={inputCls} value={s.applier_model} onChange={set('applier_model')} placeholder="inherits default" /></Field>
            <Field label="Extra headers (JSON)"><input className={inputCls + ' font-mono text-[13px]'} value={s.llm_extra_headers} onChange={set('llm_extra_headers')} placeholder='{"X-Title":"Seeko"}' /></Field>
          </div>
        )}

        <div className="mt-8 flex flex-wrap gap-3">
          <button className={btnCls} disabled={test.busy} onClick={() => test.run(async () => {
            setChecks(null)
            await api('/llm/settings', { method: 'PUT', json: s })
            const r = await api('/llm/test', { method: 'POST' })
            setChecks(Object.entries(r.checks))
            if (!r.ok) throw new Error('Endpoint check failed — see below.')
            return 'Endpoint works.'
          })}>{test.busy ? 'Testing…' : 'Save & test endpoint'}</button>
          <button className={btn2Cls} disabled={save.busy} onClick={() => save.run(async () => { await api('/llm/settings', { method: 'PUT', json: s }); return 'Saved.' })}>Save only</button>
        </div>
        <Msg m={save.msg} /><Msg m={test.msg} />
        {checks && (
          <table className="mt-5 w-full border-t border-ink text-[13px]">
            <tbody>{checks.map(([k, v]) => (
              <tr key={k} className="border-b border-hair align-top">
                <td className="w-8 py-2.5 font-mono">{v.ok ? <span className="text-ok">✓</span> : <span className="text-accent">✗</span>}</td>
                <td className="w-44 py-2.5 font-semibold capitalize">{k.replace('_', ' ')}</td>
                <td className="py-2.5 text-ink-2">{v.ok ? <>{v.latency_ms} ms{v.tier && <> · structured output via <span className="font-mono">{v.tier}</span></>}</> : <span className="text-accent">{v.error}</span>}</td>
              </tr>))}</tbody>
          </table>
        )}
      </>}
    </Section>
  )
}

/* 02 — Résumé ------------------------------------------------------------ */
function ResumeSection() {
  const [tex, setTex] = useState<string | null>(null)
  const [saved, setSaved] = useState('')
  const a = useAction()
  useEffect(() => { api('/resume').then(r => { setTex(r.tex); setSaved(r.tex) }) }, [])
  const dirty = tex !== null && tex !== saved
  const lines = tex ? tex.split('\n').length : 0

  return (
    <Section n="02" title="Résumé" note={<>Your base <span className="font-mono text-[12.5px]">.tex</span>. It must compile. Tailoring may only rephrase what is here — never add employers, dates or tools.</>}>
      {tex === null ? <Skeleton /> : <>
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
          <label className={linkBtnCls + ' cursor-pointer'}>
            Upload .tex file
            <input type="file" accept=".tex,text/x-tex" className="sr-only" onChange={async e => { const f = e.target.files?.[0]; if (f) setTex(await f.text()) }} />
          </label>
          <span className="font-mono text-[11px] text-mute">{lines} lines{dirty && <span className="text-accent"> · unsaved</span>}</span>
        </div>
        <textarea aria-label="LaTeX source" className={monoAreaCls} rows={18} spellCheck={false} value={tex} onChange={e => setTex(e.target.value)}
          placeholder={'\\documentclass{article}\n\\begin{document}\n…\n\\end{document}'} />
        <div className="mt-5 flex gap-3">
          <button className={btnCls} disabled={a.busy || !tex} onClick={() => a.run(async () => {
            const f = new FormData(); f.append('file', new Blob([tex]), 'base_resume.tex')
            await api('/resume', { method: 'PUT', body: f }); setSaved(tex); return 'Saved — it compiles.'
          })}>{a.busy ? 'Compiling…' : 'Save & compile'}</button>
        </div>
        <Msg m={a.msg} />
      </>}
    </Section>
  )
}

/* 03 + 04 — Candidate & search ------------------------------------------- */
function ProfileSections() {
  const [p, setP] = useState<any>(null)
  const a = useAction()
  useEffect(() => { api('/profile').then(setP) }, [])
  const up = (sec: string, k: string, v: any) => setP({ ...p, [sec]: { ...p[sec], [k]: v } })
  const txt = (sec: string, k: string, label: string, props: any = {}) =>
    <Field label={label}><input className={inputCls} value={p[sec][k]} onChange={e => up(sec, k, e.target.value)} {...props} /></Field>
  const saveBtn = (label: string) => (
    <div className="mt-8"><button className={btnCls} disabled={a.busy} onClick={() => a.run(async () => { await api('/profile', { method: 'PUT', json: p }); return 'Profile saved.' })}>{label}</button><Msg m={a.msg} /></div>
  )

  return <>
    <Section n="03" title="Candidate" note="Used verbatim to fill application forms. Anything left blank is left for you to answer.">
      {!p ? <Skeleton /> : <>
        <Kicker className="block border-b border-ink pb-2">Personal</Kicker>
        <div className="mt-5 grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 xl:grid-cols-3">
          {txt('personal', 'name', 'Full name', { autoComplete: 'name' })}
          {txt('personal', 'email', 'Email', { type: 'email', autoComplete: 'email' })}
          {txt('personal', 'phone', 'Phone', { type: 'tel', autoComplete: 'tel' })}
          {txt('personal', 'location', 'Location', { placeholder: 'City, Country' })}
          {txt('personal', 'linkedin', 'LinkedIn', { placeholder: 'https://linkedin.com/in/…' })}
          {txt('personal', 'github', 'GitHub')}
          {txt('personal', 'portfolio', 'Portfolio')}
        </div>
        <Kicker className="mt-10 block border-b border-ink pb-2">Application details</Kicker>
        <div className="mt-5 grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 xl:grid-cols-3">
          {txt('application', 'work_authorization', 'Work authorization', { placeholder: 'e.g. US citizen' })}
          {txt('application', 'salary_expectation', 'Salary expectation')}
          {txt('application', 'notice_period', 'Notice period', { placeholder: 'e.g. 2 weeks' })}
          <Field label="Years of experience"><input type="number" min={0} step={0.5} className={inputCls + ' tabular-nums'} value={p.application.years_experience} onChange={e => up('application', 'years_experience', +e.target.value)} /></Field>
          <div className="flex items-end pb-2"><Check checked={p.application.needs_sponsorship} onChange={v => up('application', 'needs_sponsorship', v)}>Needs visa sponsorship</Check></div>
        </div>
        {saveBtn('Save candidate')}
      </>}
    </Section>

    <Section n="04" title="Search" note="What discovery looks for, and which company career pages to watch directly.">
      {!p ? <Skeleton /> : <>
        <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2">
          <Field label="Target roles" hint="Comma-separated."><ListInput value={p.search.role_titles} onChange={v => up('search', 'role_titles', v)} placeholder="Backend Engineer, Platform Engineer" /></Field>
          <Field label="Keywords" hint="Comma-separated."><ListInput value={p.search.keywords} onChange={v => up('search', 'keywords', v)} placeholder="Python, Kubernetes" /></Field>
          <Field label="Locations" hint="Comma-separated."><ListInput value={p.search.locations} onChange={v => up('search', 'locations', v)} placeholder="London, Remote" /></Field>
          <Field label="Excluded companies" hint="Comma-separated."><ListInput value={p.search.excluded_companies} onChange={v => up('search', 'excluded_companies', v)} /></Field>
          <Field label="Minimum match score" hint="Jobs scoring below this are skipped.">
            <div className="flex items-center gap-4">
              <input type="range" min={0} max={100} value={p.search.min_match_score} onChange={e => up('search', 'min_match_score', +e.target.value)} className="w-full accent-[var(--ink)]" />
              <span className="w-12 text-right text-[28px] font-bold leading-none tracking-[-0.03em] tabular-nums">{p.search.min_match_score}</span>
            </div>
          </Field>
          <div className="flex items-end pb-2"><Check checked={p.search.remote_only} onChange={v => up('search', 'remote_only', v)}>Remote only</Check></div>
        </div>
        <Field label="Company career pages" hint="One URL per line — Greenhouse, Lever, Ashby or Workday boards." className="mt-8">
          <textarea className={areaCls + ' font-mono text-[13px]'} rows={5} spellCheck={false}
            value={p.search.watchlist_urls.join('\n')}
            onChange={e => up('search', 'watchlist_urls', e.target.value.split('\n').map((x: string) => x.trim()).filter(Boolean))}
            placeholder={'https://boards.greenhouse.io/acme\nhttps://jobs.lever.co/acme\nhttps://jobs.ashbyhq.com/acme'} />
        </Field>
        {saveBtn('Save search')}
      </>}
    </Section>
  </>
}

/* 05 — Sources & Notion -------------------------------------------------- */
const BOARDS = [
  { key: 'watchlist', label: 'Career pages', note: 'Public ATS APIs. Safe.' },
  { key: 'linkedin', label: 'LinkedIn', note: 'Login required. ToS risk.' },
  { key: 'indeed', label: 'Indeed', note: 'Login required. ToS risk.' },
  { key: 'glassdoor', label: 'Glassdoor', note: 'Login required. ToS risk.' },
]

function SourcesSection() {
  const [s, setS] = useState<any>(null)
  const a = useAction(); const n = useAction()
  useEffect(() => { api('/settings').then(setS) }, [])
  const toggle = (k: string, on: boolean) => setS({ ...s, enabled_sources: on ? [...s.enabled_sources, k] : s.enabled_sources.filter((y: string) => y !== k) })

  return <>
    <Section n="05" title="Sources" note={<>LinkedIn, Indeed and Glassdoor restrict automation in their terms and may limit your account. <span className="text-accent">Off by default — keep volumes low.</span></>}>
      {!s ? <Skeleton /> : <>
        <ul className="border-t border-ink">
          {BOARDS.map(b => {
            const on = s.enabled_sources.includes(b.key)
            return (
              <li key={b.key} className="grid grid-cols-[1fr_auto] items-center gap-4 border-b border-hair py-4 sm:grid-cols-[14rem_1fr_auto]">
                <Check checked={on} onChange={v => toggle(b.key, v)}><span className="text-[17px] font-semibold tracking-[-0.01em]">{b.label}</span></Check>
                <span className="hidden text-[13px] text-mute sm:block">{b.note}</span>
                {b.key !== 'watchlist'
                  ? <button className={linkBtnCls} onClick={() => a.run(async () => { await api(`/pipeline/login/${b.key}`, { method: 'POST' }); return `A browser opened at ${b.label}. Sign in, then close that window.` })}>Sign in ↗</button>
                  : <span />}
              </li>
            )
          })}
        </ul>
        <div className="mt-8"><button className={btnCls} onClick={() => a.run(async () => { await api('/settings', { method: 'PUT', json: s }); return 'Sources saved.' })}>Save sources</button></div>
        <Msg m={a.msg} />
      </>}
    </Section>

    <Section n="06" title="Notion" note={<>Sync every job to your tracker through Notion’s MCP server. Share the database with your integration first.</>}
      aside={<p className="mt-4 max-w-[34ch] font-mono text-[11px] leading-relaxed text-mute">Role · Company · Source · Job URL · Match Score · Status · Tailored Summary · Applied Date</p>}>
      {!s ? <Skeleton /> : <>
        <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2">
          <Field label="Integration token"><input className={inputCls} type="password" autoComplete="off" value={s.notion_token} onChange={e => setS({ ...s, notion_token: e.target.value })} placeholder="ntn_…" /></Field>
          <Field label="Database ID"><input className={inputCls + ' font-mono text-[13px]'} value={s.notion_database_id} onChange={e => setS({ ...s, notion_database_id: e.target.value })} placeholder="32-character id" /></Field>
        </div>
        <div className="mt-8 flex flex-wrap gap-3">
          <button className={btnCls} disabled={n.busy} onClick={() => n.run(async () => {
            await api('/settings', { method: 'PUT', json: s }); const r = await api('/notion/test', { method: 'POST' })
            if (!r.ok) throw new Error(r.error ?? `Connected, but the database schema needs work.\nMissing: ${r.missing?.join(', ') || 'none'}\nWrong type: ${r.wrong_type?.map((w: any) => `${w.property} is ${w.found}, expected ${w.expected}`).join('; ') || 'none'}`)
            return `Connected — ${r.tools.length} MCP tools, schema OK.`
          })}>{n.busy ? 'Connecting…' : 'Save & test Notion'}</button>
        </div>
        <Msg m={n.msg} />
      </>}
    </Section>
  </>
}

export default function Setup() {
  return <div><ModelSection /><ResumeSection /><ProfileSections /><SourcesSection /></div>
}
