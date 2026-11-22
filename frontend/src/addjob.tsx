import { useEffect, useRef, useState } from 'react'
import { api, type Job } from './api'
import { Field, Kicker, Msg, areaCls, btn2Cls, btnCls, inputCls, linkBtnCls, useAction } from './ui'

export type Capture = { url: string; title?: string; h1?: string; site?: string; text?: string; jsonld?: string[] }

/** A capture sent by the bookmarklet in the URL fragment (#add=...), consumed once. */
export function readCapture(): Capture | null {
  const m = location.hash.match(/^#add=(.*)$/s)
  if (!m) return null
  history.replaceState(null, '', '#pipeline')
  try { const c = JSON.parse(decodeURIComponent(m[1])); return c && typeof c.url === 'string' ? c : null } catch { return null }
}

type Preview = { title: string; company: string; location: string; url: string; source: string; job_type: string; is_remote: boolean
  jd_text: string; jd_chars: number; jd_source: string; valid_through: string | null }

function Bookmarklet() {
  const ref = useRef<HTMLAnchorElement>(null)
  useEffect(() => {
    api<{ href: string }>(`/jobs/bookmarklet?origin=${encodeURIComponent(location.origin)}`)
      .then(r => ref.current?.setAttribute('href', r.href)).catch(() => {})   // set directly: React blocks javascript: links
  }, [])
  return (
    <p className="text-[12.5px] leading-relaxed text-mute">
      <a ref={ref} onClick={e => e.preventDefault()} className="mr-2 inline-flex items-center gap-1 border border-ink px-2 py-0.5 font-semibold text-ink no-underline" title="Drag me to your bookmarks bar">＋ Seeko</a>
      Drag this to your bookmarks bar. On any job page — even ones you are signed in to, like LinkedIn — click it to send
      that posting here. Seeko only reads what your tab shows; select the description first if the page is busy.
    </p>
  )
}

/** Add one job you found yourself: by link, by pasting it, or from the bookmarklet. */
export function AddJob({ capture, onAdded }: { capture: Capture | null; onAdded: (id: string) => void }) {
  const [open, setOpen] = useState(!!capture)
  const [how, setHow] = useState<'link' | 'paste'>('link')
  const [f, setF] = useState({ url: capture?.url ?? '', title: '', company: '', location: '', description: '' })
  const [mode, setMode] = useState<'apply' | 'applied'>('apply')
  const [pv, setPv] = useState<Preview | null>(null)
  const a = useAction()
  const body = (preview: boolean) => capture && f.url === capture.url
    ? { capture, mode, preview, title: f.title, company: f.company, location: f.location }
    : how === 'link' ? { url: f.url, mode, preview } : { ...f, mode, preview }

  const preview = () => a.run(async () => {
    const r = await api('/jobs/add', { method: 'POST', json: body(true) }); setPv(r.job)
    return 'Read it — check the details, then Add job.'
  })
  const add = () => a.run(async () => {
    const r = await api<{ job: Job; created: boolean }>('/jobs/add', { method: 'POST', json: body(false) })
    onAdded(r.job.id); setPv(null); setF({ url: '', title: '', company: '', location: '', description: '' })
    return r.created
      ? (mode === 'apply' ? `Added “${r.job.title}” — Seeko will score, tailor and fill it.` : `Added “${r.job.title}” as applied.`)
      : `Already known: “${r.job.title}” at ${r.job.company}.`
  })
  useEffect(() => { if (capture) preview() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return <button className={linkBtnCls} onClick={() => setOpen(true)}>＋ Add a job by link</button>
  return (
    <section aria-label="Add a job" className="mt-6 border border-ink p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <Kicker>Add a job you found</Kicker>
        <button className={linkBtnCls} onClick={() => { setOpen(false); setPv(null) }}>Close</button>
      </div>
      {capture && f.url === capture.url
        ? <p className="mt-3 text-[13.5px]">From your browser: <span className="font-mono text-[12px] break-all">{capture.url}</span></p>
        : <>
          <div role="tablist" className="mt-3 flex gap-5">
            {(['link', 'paste'] as const).map(k => (
              <button key={k} role="tab" aria-selected={how === k} onClick={() => { setHow(k); setPv(null) }}
                className={`border-b-2 pb-1 text-[12px] font-semibold uppercase tracking-[0.1em] ${how === k ? 'border-accent' : 'border-transparent text-mute'}`}>
                {k === 'link' ? 'Link' : 'Paste it'}</button>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
            <Field label={how === 'link' ? 'Posting link' : 'Link (needed for Seeko to apply)'} className="sm:col-span-2">
              <input className={inputCls + ' font-mono text-[13px]'} value={f.url} placeholder="https://boards.greenhouse.io/acme/jobs/123" onChange={e => { setF({ ...f, url: e.target.value }); setPv(null) }} />
            </Field>
            {how === 'paste' && <>
              <Field label="Title"><input className={inputCls} value={f.title} onChange={e => setF({ ...f, title: e.target.value })} /></Field>
              <Field label="Company"><input className={inputCls} value={f.company} onChange={e => setF({ ...f, company: e.target.value })} /></Field>
              <Field label="Location"><input className={inputCls} value={f.location} onChange={e => setF({ ...f, location: e.target.value })} /></Field>
              <Field label="Description" className="sm:col-span-2"><textarea className={areaCls} rows={5} value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></Field>
            </>}
          </div>
        </>}
      <div role="radiogroup" aria-label="What should Seeko do" className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-[14px]">
        <label className="inline-flex items-center gap-2"><input type="radio" checked={mode === 'apply'} onChange={() => setMode('apply')} />Tailor and fill it (waits for your approval)</label>
        <label className="inline-flex items-center gap-2"><input type="radio" checked={mode === 'applied'} onChange={() => setMode('applied')} />I applied already — just track it</label>
      </div>
      {pv && (
        <div className="mt-5 border-l-2 border-ink pl-4">
          <p className="text-[19px] font-bold leading-tight tracking-[-0.01em]">{pv.title}</p>
          <p className="mt-1 text-[13px] text-mute">{pv.company} · {pv.source}{pv.location ? ` · ${pv.location}` : ''}{pv.is_remote ? ' · remote' : ''}{pv.job_type === 'internship' ? ' · internship' : ''}{pv.valid_through ? ` · apply by ${pv.valid_through.slice(0, 10)}` : ''}</p>
          <p className="mt-2 line-clamp-4 max-w-[80ch] whitespace-pre-line text-[13px] leading-relaxed text-ink-2">{pv.jd_text || 'No description found.'}</p>
          <p className="mt-1 font-mono text-[11px] text-mute">{pv.jd_chars} characters of description · read from {pv.jd_source}</p>
        </div>
      )}
      <div className="mt-5 flex flex-wrap items-center gap-3">
        {!pv && <button className={btn2Cls} disabled={a.busy || !(f.url || f.title)} onClick={preview}>{a.busy ? 'Reading…' : 'Preview'}</button>}
        <button className={btnCls} disabled={a.busy || !(f.url || f.title)} onClick={add}>{a.busy && pv ? 'Adding…' : 'Add job'}</button>
      </div>
      <Msg m={a.msg} />
      <div className="mt-5 border-t border-hair pt-3"><Bookmarklet /></div>
    </section>
  )
}
