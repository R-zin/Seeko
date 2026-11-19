import { useEffect, useRef, useState } from 'react'
import { api, type BackupStatus, type NotionStatus } from '../api'
import { ResumeSection } from '../resume'
import { Checklist } from '../checklist'
import { AnswersSection } from '../answers'
import { Check, Field, Kicker, Msg, Section, areaCls, btn2Cls, btnCls, inputCls, linkBtnCls, useAction } from '../ui'

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
          <div>
            <Kicker>Looking for</Kicker>
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
              {(['job', 'internship'] as const).map(t => (
                <Check key={t} checked={p.search.job_types.includes(t)}
                  onChange={v => { const next = v ? [...p.search.job_types, t] : p.search.job_types.filter((x: string) => x !== t); if (next.length) up('search', 'job_types', next) }}>
                  {t === 'job' ? 'Jobs' : 'Internships'}
                </Check>))}
            </div>
          </div>
          <Field label="Skip postings older than, days" hint="Only when the page states a date. 0 = no limit.">
            <input type="number" min={0} className={inputCls + ' tabular-nums'} value={p.search.max_age_days} onChange={e => up('search', 'max_age_days', Math.max(0, +e.target.value))} />
          </Field>
        </div>
        <Field label="Company career pages" hint="One URL per line. Greenhouse, Lever, Ashby and Workday boards are read through their APIs; any other careers page is browsed (robots.txt is respected)." className="mt-8">
          <textarea className={areaCls + ' font-mono text-[13px]'} rows={5} spellCheck={false}
            value={p.search.watchlist_urls.join('\n')}
            onChange={e => up('search', 'watchlist_urls', e.target.value.split('\n').map((x: string) => x.trim()).filter(Boolean))}
            placeholder={'https://boards.greenhouse.io/acme\nhttps://jobs.lever.co/acme\nhttps://www.acme.com/careers'} />
        </Field>
        {saveBtn('Save search')}
      </>}
    </Section>
  </>
}

/* 06 — Sources ------------------------------------------------------------ */
const BOARDS = [
  { key: 'watchlist', label: 'Career pages', note: 'ATS APIs and browsed careers pages. Safe.' },
  { key: 'linkedin', label: 'LinkedIn', note: 'Login required. ToS risk.' },
  { key: 'indeed', label: 'Indeed', note: 'Login required. ToS risk.' },
  { key: 'glassdoor', label: 'Glassdoor', note: 'Login required. ToS risk.' },
  { key: 'internshala', label: 'Internshala', note: 'Internships and fresher jobs (India). ToS risk.' },
]

function SourcesSection() {
  const [s, setS] = useState<any>(null)
  const a = useAction()
  useEffect(() => { api('/settings').then(setS) }, [])
  const toggle = (k: string, on: boolean) => setS({ ...s, enabled_sources: on ? [...s.enabled_sources, k] : s.enabled_sources.filter((y: string) => y !== k) })

  return <>
    <Section n="06" title="Sources" note={<>LinkedIn, Indeed, Glassdoor and Internshala restrict automation in their terms and may limit your account. <span className="text-accent">Off by default — keep volumes low.</span></>}>
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
        <Kicker className="mt-10 block border-b border-ink pb-2">Browsing budget, per run</Kicker>
        <div className="mt-5 grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 xl:grid-cols-3">
          <Field label="Postings opened per source" hint="The rest wait for the next run.">
            <input type="number" min={1} max={500} className={inputCls + ' tabular-nums'} value={s.search_max_details} onChange={e => setS({ ...s, search_max_details: +e.target.value })} />
          </Field>
          <Field label="Result pages per search">
            <input type="number" min={1} max={20} className={inputCls + ' tabular-nums'} value={s.search_max_pages} onChange={e => setS({ ...s, search_max_pages: +e.target.value })} />
          </Field>
          <Field label="Searches per board" hint="Roles × locations × jobs/internships, capped.">
            <input type="number" min={1} max={50} className={inputCls + ' tabular-nums'} value={s.search_max_queries} onChange={e => setS({ ...s, search_max_queries: +e.target.value })} />
          </Field>
          <Field label="Delay between pages, seconds" hint="Random within this range.">
            <div className="flex items-center gap-3">
              <input type="number" min={0} step={0.5} aria-label="Minimum delay" className={inputCls + ' tabular-nums'} value={s.search_delay_min_s} onChange={e => setS({ ...s, search_delay_min_s: +e.target.value })} />
              <span className="text-mute">to</span>
              <input type="number" min={0} step={0.5} aria-label="Maximum delay" className={inputCls + ' tabular-nums'} value={s.search_delay_max_s} onChange={e => setS({ ...s, search_delay_max_s: +e.target.value })} />
            </div>
          </Field>
          <Field label="Page loads per site, per 24 h" hint="Across all runs, so scheduled searches stay gentle.">
            <input type="number" min={1} max={5000} className={inputCls + ' tabular-nums'} value={s.search_daily_pages} onChange={e => setS({ ...s, search_daily_pages: +e.target.value })} />
          </Field>
          <Field label="Pause a site after a block, hours" hint="After a CAPTCHA, login wall or rate limit. 0 = off.">
            <input type="number" min={0} max={720} className={inputCls + ' tabular-nums'} value={s.search_block_cooldown_h} onChange={e => setS({ ...s, search_block_cooldown_h: +e.target.value })} />
          </Field>
          <Field label="Indeed site" hint="Country site, e.g. in.indeed.com.">
            <input className={inputCls + ' font-mono text-[13px]'} value={s.indeed_host} onChange={e => setS({ ...s, indeed_host: e.target.value })} />
          </Field>
          <div className="flex flex-col justify-end gap-2 pb-2">
            <Check checked={s.search_respect_robots} onChange={v => setS({ ...s, search_respect_robots: v })}>Respect robots.txt on careers pages</Check>
            <Check checked={s.search_llm_review} onChange={v => setS({ ...s, search_llm_review: v })}>Let the model double-check unstructured pages</Check>
          </div>
        </div>
        <div className="mt-8"><button className={btnCls} onClick={() => a.run(async () => { await api('/settings', { method: 'PUT', json: s }); return 'Sources saved.' })}>Save sources</button></div>
        <Msg m={a.msg} />
      </>}
    </Section>

  </>
}

/* 07 — Notion ------------------------------------------------------------- */
const ago = (iso: string | null) => {
  if (!iso) return 'never'
  const s = Math.max(0, (Date.now() - Date.parse(iso + (iso.endsWith('Z') ? '' : 'Z'))) / 1000)
  return s < 60 ? `${Math.round(s)} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`
}

const PROPS = ['Role', 'Company', 'Status', 'Application Status', 'Applied Date', 'Next Step', 'Next Step Date', 'Notes', 'Job Type',
  'Location', 'Remote', 'Source', 'Job URL', 'Match Score', 'Employment Type', 'Discovered', 'Posted', 'Apply By', 'Closed',
  'Tailored Summary', 'Seeko ID']

function NotionSection() {
  const [s, setS] = useState<any>(null)
  const [st, setSt] = useState<NotionStatus | null>(null)
  const n = useAction()
  useEffect(() => { api('/settings').then(setS) }, [])
  useEffect(() => {
    const load = () => api<NotionStatus>('/notion/status').then(setSt).catch(() => {})
    load(); const t = setInterval(load, 4000); return () => clearInterval(t)
  }, [])
  const saveSettings = () => api('/settings', { method: 'PUT', json: { notion_token: s.notion_token, notion_database_id: s.notion_database_id, notion_pull_interval_s: s.notion_pull_interval_s } })
  const describe = (r: any) => [
    r.missing?.length ? `Not in your database yet (skipped until added): ${r.missing.join(', ')}` : 'Every tracker property is present.',
    r.wrong_type?.length ? `Different type, left alone: ${r.wrong_type.map((w: any) => `${w.property} is ${w.found}, expected ${w.expected.join(' or ')}`).join('; ')}` : '',
  ].filter(Boolean).join('\n')
  const schema = st?.schema

  return (
    <Section n="07" title="Notion" note={<>Every job, its status and your application tracking, kept in your Notion database through Notion’s MCP server. Changes reach Notion within seconds; edits to the tracking columns in Notion come back.</>}
      aside={<p className="mt-4 max-w-[34ch] font-mono text-[11px] leading-relaxed text-mute">{PROPS.join(' · ')}<br /><span className="text-ink-2">Page body: the full job description.</span></p>}>
      {!s ? <Skeleton /> : <>
        <ol className="mb-6 list-decimal space-y-1 pl-5 text-[13.5px] leading-relaxed text-ink-2">
          <li>Create an internal integration at notion.so/profile/integrations and copy its token.</li>
          <li>Create a database (an empty one is fine), open <span className="font-mono text-[12px]">•••</span> → Connections and add the integration.</li>
          <li>Paste the token and the database ID (from its link), then <b>Set up database</b> to add the tracker properties.</li>
        </ol>
        <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 xl:grid-cols-3">
          <Field label="Integration token"><input className={inputCls} type="password" autoComplete="off" value={s.notion_token} onChange={e => setS({ ...s, notion_token: e.target.value })} placeholder="ntn_…" /></Field>
          <Field label="Database ID"><input className={inputCls + ' font-mono text-[13px]'} value={s.notion_database_id} onChange={e => setS({ ...s, notion_database_id: e.target.value })} placeholder="32-character id" /></Field>
          <Field label="Read edits back every, seconds" hint="0 = one-way (Seeko → Notion only).">
            <input type="number" min={0} className={inputCls + ' tabular-nums'} value={s.notion_pull_interval_s} onChange={e => setS({ ...s, notion_pull_interval_s: +e.target.value })} />
          </Field>
        </div>
        <div className="mt-8 flex flex-wrap gap-3">
          <button className={btnCls} disabled={n.busy} onClick={() => n.run(async () => {
            await saveSettings(); const r = await api('/notion/test', { method: 'POST' })
            if (!r.ok) throw new Error(r.error ?? 'The database has no title property.')
            return `Connected — ${r.tools.length} MCP tools, ${r.mapped.length} of ${PROPS.length} properties mapped.\n${describe(r)}`
          })}>{n.busy ? 'Working…' : 'Save & test'}</button>
          <button className={btn2Cls} disabled={n.busy || !s.notion_database_id} onClick={() => n.run(async () => {
            await saveSettings(); const r = await api('/notion/setup', { method: 'POST' })
            if (!r.ok) throw new Error(r.error ?? 'Setup failed.')
            return `${r.added.length ? `Added ${r.added.length} properties: ${r.added.join(', ')}.` : 'Nothing to add.'} Every job is being re-synced.\n${describe(r)}`
          })}>Set up database</button>
          <button className={btn2Cls} disabled={n.busy || !st?.configured} onClick={() => n.run(async () => {
            const r = await api('/notion/resync', { method: 'POST' }); return `${r.queued} jobs queued; pages deleted in Notion will be recreated.`
          })}>Resync all</button>
          <button className={btn2Cls} disabled={n.busy || !st?.configured} onClick={() => n.run(async () => {
            const r = await api('/notion/pull', { method: 'POST' }); if (!r.ok) throw new Error(r.error); return `${r.changed} job(s) updated from Notion.`
          })}>Pull edits now</button>
        </div>
        <Msg m={n.msg} />
        {st?.configured && (
          <dl className="mt-8 grid grid-cols-2 gap-x-8 gap-y-4 border-t border-hair pt-4 text-[13px] sm:grid-cols-4">
            <div><dt><Kicker>Pages</Kicker></dt><dd className="mt-1 text-[22px] font-bold tabular-nums">{st.synced}</dd></div>
            <div><dt><Kicker>Waiting</Kicker></dt><dd className="mt-1 text-[22px] font-bold tabular-nums">{st.pending}</dd></div>
            <div><dt><Kicker>Errors</Kicker></dt><dd className={`mt-1 text-[22px] font-bold tabular-nums ${st.errors ? 'text-accent' : ''}`}>{st.errors}</dd></div>
            <div><dt><Kicker>Last push / pull</Kicker></dt><dd className="mt-1 font-mono text-[12px] leading-relaxed">{ago(st.last_push_at)}<br />{ago(st.last_pull_at)}</dd></div>
            {schema && !!schema.missing.length && <p className="col-span-full text-[12.5px] text-warn">Missing from the database (not synced until added): {schema.missing.join(', ')}. Use Set up database.</p>}
            {st.last_error && <p className="col-span-full font-mono text-[12px] text-accent">{st.last_error}{st.backoff_until ? ` — retrying after ${st.backoff_until.slice(11, 19)} UTC` : ''}</p>}
          </dl>
        )}
      </>}
    </Section>
  )
}

/* 08 — Backup ------------------------------------------------------------- */
function BackupSection() {
  const [b, setB] = useState<BackupStatus | null>(null)
  const a = useAction()
  const load = () => api<BackupStatus>('/backup').then(setB).catch(() => {})
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [])

  return (
    <Section n="08" title="Backup" note="A second PostgreSQL server that keeps a live copy of every job, run and log. It can take over if the main database is lost.">
      {!b ? <Skeleton /> : !b.configured ? (
        <div className="max-w-[70ch] space-y-3 text-[14px] leading-relaxed text-ink-2">
          <p>Off. Set <span className="font-mono text-[12.5px]">BACKUP_DATABASE_URL</span> in <span className="font-mono text-[12.5px]">.env</span> and restart:</p>
          <ul className="list-disc space-y-1 pl-5 font-mono text-[12.5px]">
            <li>docker compose up -d db-backup → postgresql://seeko:seeko@localhost:5433/seeko</li>
            <li>any hosted PostgreSQL (another machine is safest)</li>
            <li>embedded — a second local server in data/postgres-backup</li>
          </ul>
        </div>
      ) : <>
        <dl className="grid grid-cols-2 gap-x-8 gap-y-4 text-[13px] sm:grid-cols-4">
          <div className="col-span-2"><dt><Kicker>Server</Kicker></dt><dd className="mt-1 break-all font-mono text-[12px]">{b.target}</dd></div>
          <div><dt><Kicker>Last copy</Kicker></dt><dd className="mt-1 font-mono text-[12px]">{ago(b.last_sync_at)}</dd></div>
          <div><dt><Kicker>Last full check</Kicker></dt><dd className="mt-1 font-mono text-[12px]">{ago(b.last_full_at)}</dd></div>
          {Object.entries(b.rows).map(([t, n]) => (
            <div key={t}><dt><Kicker>{t.replace('_', ' ')}</Kicker></dt><dd className="mt-1 text-[22px] font-bold tabular-nums">{n}</dd></div>
          ))}
        </dl>
        {b.last_error && <p className="mt-4 font-mono text-[12px] text-accent">{b.last_error}</p>}
        <div className="mt-8 flex flex-wrap items-center gap-4">
          <button className={btnCls} disabled={a.busy} onClick={() => a.run(async () => {
            const r = await api('/backup/sync', { method: 'POST' }); load()
            const copied = Object.values(r.tables as Record<string, { copied: number }>).reduce((t, x) => t + x.copied, 0)
            return `Backup checked — ${copied} row(s) copied.`
          })}>{a.busy ? 'Copying…' : 'Back up now'}</button>
          <span className="text-[12px] text-mute">Copies changes every {b.interval_s} s. To switch over, point DATABASE_URL at the backup; to copy it back, run <span className="font-mono">python -m jobsearch restore-backup</span>.</span>
        </div>
        <Msg m={a.msg} />
      </>}
    </Section>
  )
}

/* 09 — Notifications & follow-ups ------------------------------------------ */
const EVENTS: [string, string][] = [
  ['needs_review', 'A form is waiting for your approval or answers'], ['applied', 'Seeko submitted on its own (full-auto)'],
  ['failed', 'A job failed'], ['new_jobs', 'A search found new jobs'], ['digest', 'Daily digest of follow-ups and deadlines'],
]

function NotifySection() {
  const [s, setS] = useState<any>(null)
  const a = useAction()
  useEffect(() => { api('/settings').then(setS) }, [])
  const keys = ['notify_webhook_url', 'notify_events', 'digest_hour', 'followup_after_days', 'auto_ghost_days', 'auto_retry', 'app_url']
  const save = () => api('/settings', { method: 'PUT', json: Object.fromEntries(keys.map(k => [k, s[k]])) })
  const toggle = (k: string, on: boolean) => setS({ ...s, notify_events: on ? [...s.notify_events, k] : s.notify_events.filter((x: string) => x !== k) })
  return (
    <Section n="09" title="Notifications" note="Hear about forms waiting for you without keeping Seeko open: Slack, Discord, ntfy (phone push) or any webhook.">
      {!s ? <Skeleton /> : <>
        <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2">
          <Field label="Webhook URL" hint="Slack / Discord incoming webhook, https://ntfy.sh/your-topic, or any URL taking JSON.">
            <input className={inputCls + ' font-mono text-[13px]'} type="password" autoComplete="off" value={s.notify_webhook_url} placeholder="https://ntfy.sh/my-seeko-alerts" onChange={e => setS({ ...s, notify_webhook_url: e.target.value })} />
          </Field>
          <Field label="Seeko address in links" hint="Where you open Seeko (http://127.0.0.1:8000 when serving the build).">
            <input className={inputCls + ' font-mono text-[13px]'} value={s.app_url} onChange={e => setS({ ...s, app_url: e.target.value })} />
          </Field>
        </div>
        <fieldset className="mt-6"><legend><Kicker>Tell me when</Kicker></legend>
          <div className="mt-2 grid grid-cols-1 gap-y-2 sm:grid-cols-2">
            {EVENTS.map(([k, l]) => <Check key={k} checked={s.notify_events.includes(k)} onChange={v => toggle(k, v)}>{l}</Check>)}
          </div>
        </fieldset>
        <Kicker className="mt-10 block border-b border-ink pb-2">Follow-ups</Kicker>
        <div className="mt-5 grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 xl:grid-cols-4">
          <Field label="Digest at, local hour" hint="-1 = no digest.">
            <input type="number" min={-1} max={23} className={inputCls + ' tabular-nums'} value={s.digest_hour} onChange={e => setS({ ...s, digest_hour: +e.target.value })} />
          </Field>
          <Field label="Follow up after, days" hint="An application with no news this long shows up as due.">
            <input type="number" min={1} max={90} className={inputCls + ' tabular-nums'} value={s.followup_after_days} onChange={e => setS({ ...s, followup_after_days: +e.target.value })} />
          </Field>
          <Field label="Mark “No response” after, days" hint="0 = never mark it for you.">
            <input type="number" min={0} max={365} className={inputCls + ' tabular-nums'} value={s.auto_ghost_days} onChange={e => setS({ ...s, auto_ghost_days: +e.target.value })} />
          </Field>
          <Field label="Retries after a timeout" hint="Rate limits and timeouts are retried with a growing pause.">
            <input type="number" min={0} max={10} className={inputCls + ' tabular-nums'} value={s.auto_retry} onChange={e => setS({ ...s, auto_retry: +e.target.value })} />
          </Field>
        </div>
        <div className="mt-8 flex flex-wrap gap-3">
          <button className={btnCls} disabled={a.busy} onClick={() => a.run(async () => { await save(); return 'Saved.' })}>Save</button>
          <button className={btn2Cls} disabled={a.busy || !s.notify_webhook_url} onClick={() => a.run(async () => {
            await save(); const r = await api('/notify/test', { method: 'POST' }); return `Test sent to your ${r.kind} webhook.`
          })}>Save & send test</button>
        </div>
        <Msg m={a.msg} />
      </>}
    </Section>
  )
}

export default function Setup() {
  return <div><Checklist /><ModelSection /><ResumeSection /><ProfileSections /><AnswersSection /><SourcesSection /><NotionSection /><BackupSection /><NotifySection /></div>
}
