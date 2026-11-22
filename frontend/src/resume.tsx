import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Kicker, Msg, Section, btn2Cls, btnCls, linkBtnCls, monoAreaCls, useAction } from './ui'

/* Types mirroring backend/resume ------------------------------------------ */
interface Issue { message: string; line: number | null; file: string; context: string }
interface Compile { ok: boolean; pages: number; engine: string; seconds: number; cached: boolean; errors: Issue[]; warnings: string[]; overfull: number; summary: string }
interface Analysis {
  documentclass: string; engine_hint: string; sections: { name: string; editable: number }[]
  segments: { summary: number; skills: number; bullet: number }; editable_total: number
  bullet_macros: string[]; unsafe: string[]; missing_assets: string[]; warnings: string[]; lines: number
}
interface Draft { kind: 'tex' | 'pdf' | 'zip'; tex: string; analysis: Analysis; compile: Compile; notes: string[]; conversion: any; staged_assets: string[]; has_source_pdf: boolean }

const KIND_LABEL = { tex: 'LaTeX source', pdf: 'Converted from PDF', zip: 'Project archive' } as const

/** api() throws "422: <json detail>"; recover the structured detail when there is one. */
function detail(e: Error): { message?: string; compile?: Compile } | null {
  try { const d = JSON.parse(e.message.replace(/^\d+: /, '')); return typeof d === 'object' && d ? d : null } catch { return null }
}

function CompileLine({ c, onJump }: { c: Compile | null; onJump: (line: number) => void }) {
  if (!c) return null
  return (
    <div className={`border-l-2 py-1 pl-3 ${c.ok ? 'border-ok' : 'border-accent'}`}>
      <p className={`text-[14px] font-semibold ${c.ok ? 'text-ok' : 'text-accent'}`}>
        {c.ok ? `Compiles — ${c.pages} page${c.pages === 1 ? '' : 's'}` : 'Does not compile'}
        <span className="ml-2 font-mono text-[11px] font-normal text-mute">{c.engine}{c.seconds ? ` · ${c.seconds}s` : ''}{c.cached ? ' · cached' : ''}{c.overfull ? ` · ${c.overfull} overfull box${c.overfull > 1 ? 'es' : ''}` : ''}</span>
      </p>
      {!c.ok && (
        <ul className="mt-2 space-y-1 font-mono text-[12px]">
          {(c.errors.length ? c.errors : [{ message: c.summary, line: null } as Issue]).slice(0, 6).map((e, i) => (
            <li key={i}>
              {e.line ? <button className="mr-2 text-accent underline underline-offset-2" onClick={() => onJump(e.line!)}>line {e.line}</button> : null}
              <span className="text-ink-2">{e.message}</span>{e.context && <span className="text-mute"> — {e.context}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AnalysisPanel({ a }: { a: Analysis | null }) {
  if (!a) return null
  const problems = [...a.unsafe.map(u => `Blocked: ${u}`), ...a.missing_assets.map(m => `Missing: ${m}`), ...a.warnings]
  return (
    <div>
      <div className="grid grid-cols-3 gap-4 border-t border-ink pt-3">
        {([['bullet', 'Bullets'], ['skills', 'Skill lines'], ['summary', 'Summary']] as const).map(([k, l]) => (
          <div key={k}>
            <div className={`text-[34px] font-semibold leading-none tracking-[-0.03em] tabular-nums ${a.segments[k] ? '' : 'text-mute'}`}>{a.segments[k]}</div>
            <Kicker className="mt-1.5 block">{l}</Kicker>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[13px] leading-relaxed text-mute">
        Only these {a.editable_total} pieces can be rephrased for a job. Headings, companies, dates and education are frozen.
      </p>
      <ul className="mt-3 border-t border-hair text-[13px]">
        {a.sections.map(s => (
          <li key={s.name} className="flex justify-between border-b border-hair py-1.5">
            <span>{s.name}</span>
            <span className={`font-mono text-[11px] ${s.editable ? 'text-ink' : 'text-mute'}`}>{s.editable ? `${s.editable} editable` : 'frozen'}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 font-mono text-[11px] text-mute">
        \documentclass{'{'}{a.documentclass || '?'}{'}'} · {a.lines} lines · engine: {a.engine_hint}{a.bullet_macros.length ? ` · bullet macros: ${a.bullet_macros.map(m => '\\' + m).join(', ')}` : ''}
      </p>
      {problems.length > 0 && (
        <ul className="mt-3 space-y-1 text-[13px]">
          {problems.map(p => <li key={p} className={p.startsWith('Blocked') ? 'text-accent' : 'text-warn'}>{p}</li>)}
        </ul>
      )}
    </div>
  )
}

export function ResumeSection() {
  const [tex, setTex] = useState<string | null>(null)
  const [saved, setSaved] = useState('')
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [compile, setCompile] = useState<Compile | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [hasPdf, setHasPdf] = useState(false)
  const [preview, setPreview] = useState<'compiled' | 'source'>('compiled')
  const [bust, setBust] = useState(0)
  const [drag, setDrag] = useState(false)
  const area = useRef<HTMLTextAreaElement>(null)
  const up = useAction(); const chk = useAction(); const save = useAction()

  useEffect(() => {
    api('/resume').then(r => { setTex(r.tex); setSaved(r.tex); setAnalysis(r.analysis); setHasPdf(r.has_pdf) })
  }, [])

  const dirty = tex !== null && tex !== saved
  const jump = (line: number) => {
    const el = area.current; if (!el || tex === null) return
    const lines = tex.split('\n'); const start = lines.slice(0, line - 1).join('\n').length + (line > 1 ? 1 : 0)
    el.focus(); el.setSelectionRange(start, start + (lines[line - 1]?.length ?? 0))
    el.scrollTop = Math.max(0, (line - 4) * 19.4)
  }

  const upload = (f: File) => up.run(async () => {
    const fd = new FormData(); fd.append('file', f)
    const d: Draft = await api('/resume/upload', { method: 'POST', body: fd })
    setDraft(d); setTex(d.tex); setAnalysis(d.analysis); setCompile(d.compile); setPreview('compiled'); setBust(Date.now())
    return `${KIND_LABEL[d.kind]} loaded as a draft — nothing is saved until you press “Save as base”.`
  })

  const check = () => chk.run(async () => {
    const r = await api('/resume/analyze?compile=true', { method: 'POST', json: { tex, promote_staged: !!draft } })
    setAnalysis(r.analysis); setCompile(r.compile)
    if (!r.compile.ok) throw new Error('Fix the errors above, then check again.')
    return 'Checked.'
  })

  const commit = () => save.run(async () => {
    try {
      const r = await api('/resume', { method: 'PUT', json: { tex, promote_staged: !!draft } })
      setSaved(tex!); setAnalysis(r.analysis); setCompile(r.compile); setDraft(null); setHasPdf(true); setPreview('compiled'); setBust(Date.now())
      return `Saved — ${r.compile.pages} page${r.compile.pages === 1 ? '' : 's'}, ${r.analysis.editable_total} editable pieces.`
    } catch (e: any) {
      const d = detail(e)                     // 422 body: {message, compile}
      if (d?.compile) setCompile(d.compile)
      throw new Error(d?.message ?? e.message)
    }
  })

  const pdfSrc = preview === 'source' ? `/api/resume/source.pdf?staged=1&b=${bust}` : draft ? `/api/resume/draft.pdf?b=${bust}` : `/api/resume/pdf?b=${bust}`
  const showPreview = draft ? (preview === 'source' ? draft.has_source_pdf : draft.compile.ok) : hasPdf

  return (
    <Section n="02" title="Résumé"
      note={<>Upload a <span className="font-mono text-[12.5px]">.tex</span>, a <span className="font-mono text-[12.5px]">.pdf</span> or an Overleaf <span className="font-mono text-[12.5px]">.zip</span>. PDFs are converted to clean LaTeX using only their own text. Tailoring may only rephrase what is here.</>}>
      {tex === null ? <div className="h-40 bg-paper-2" /> : <>
        {/* Drop zone */}
        <label
          onDragOver={e => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
          onDrop={e => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) upload(f) }}
          className={`flex cursor-pointer flex-wrap items-baseline justify-between gap-3 border-2 border-dashed px-5 py-6 transition-colors ${drag ? 'border-accent bg-paper-2' : 'border-hair hover:border-ink'}`}>
          <span>
            <span className="block text-[20px] font-semibold tracking-[-0.02em]">{up.busy ? 'Reading…' : 'Drop your résumé here'}</span>
            <span className="mt-1 block text-[13px] text-mute">.tex · .pdf · .zip — or click to choose. Max 15 MB.</span>
          </span>
          <span className={btn2Cls}>Choose file</span>
          <input type="file" accept=".tex,.ltx,.pdf,.zip,application/pdf,application/zip" className="sr-only" disabled={up.busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
        </label>
        <Msg m={up.msg} />

        {draft && (
          <div className="mt-6 border-y-2 border-accent py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-[15px] font-semibold">Draft · {KIND_LABEL[draft.kind]}</span>
              <button className={linkBtnCls} onClick={() => { setDraft(null); setTex(saved); setCompile(null); api('/resume').then(r => setAnalysis(r.analysis)) }}>Discard draft</button>
            </div>
            {draft.notes.length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[13px] text-ink-2">{draft.notes.map(n => <li key={n}>{n}</li>)}</ul>}
            {draft.conversion && <p className="mt-2 font-mono text-[11px] text-mute">Detected: {draft.conversion.name || 'no name'} · {draft.conversion.sections.map((s: any) => `${s.name} (${s.bullets}•)`).join(' · ')}</p>}
          </div>
        )}

        <div className="mt-8 grid grid-cols-12 gap-x-6 gap-y-8">
          {/* Editor */}
          <div className="col-span-12 xl:col-span-7">
            <div className="mb-2 flex items-baseline justify-between border-b border-ink pb-2">
              <Kicker>Source</Kicker>
              <span className="font-mono text-[11px] text-mute">{tex.split('\n').length} lines{dirty && <span className="text-accent"> · unsaved</span>}</span>
            </div>
            <textarea ref={area} aria-label="LaTeX source" className={monoAreaCls} rows={24} spellCheck={false} value={tex} onChange={e => setTex(e.target.value)}
              placeholder={'\\documentclass{article}\n\\begin{document}\n…\n\\end{document}'} />
            <div className="mt-4"><CompileLine c={compile} onJump={jump} /></div>
            <div className="mt-5 flex flex-wrap gap-3">
              <button className={btnCls} disabled={save.busy || !tex || (!dirty && !draft)} onClick={commit}>{save.busy ? 'Compiling…' : 'Save as base'}</button>
              <button className={btn2Cls} disabled={chk.busy || !tex} onClick={check}>{chk.busy ? 'Checking…' : 'Check & compile'}</button>
            </div>
            <Msg m={save.msg} /><Msg m={chk.msg} />
          </div>

          {/* Analysis + preview */}
          <aside className="col-span-12 xl:col-span-5">
            <div className="mb-2 border-b border-ink pb-2"><Kicker>What can be tailored</Kicker></div>
            {analysis ? <AnalysisPanel a={analysis} /> : <p className="text-[14px] text-mute">Upload or paste a résumé to see its structure.</p>}

            <div className="mb-2 mt-10 flex items-baseline justify-between border-b border-ink pb-2">
              <Kicker>{draft ? 'Draft preview' : 'Saved PDF'}</Kicker>
              {draft?.has_source_pdf && (
                <span className="flex gap-3 text-[11px] font-semibold uppercase tracking-[0.1em]">
                  {(['compiled', 'source'] as const).map(p => (
                    <button key={p} onClick={() => setPreview(p)} className={preview === p ? 'text-ink underline decoration-ink decoration-2 underline-offset-4' : 'text-mute hover:text-ink'}>{p === 'compiled' ? 'Converted' : 'Your PDF'}</button>
                  ))}
                </span>
              )}
            </div>
            {showPreview
              ? <object key={pdfSrc} data={pdfSrc + '#toolbar=0&view=FitH'} type="application/pdf" className="h-[520px] w-full border border-hair bg-paper-2" aria-label="Résumé preview">
                  <div className="flex h-full flex-col items-start justify-end gap-3 p-6">
                    <span className="text-[13px] text-mute">This browser can’t preview PDFs inline.</span>
                    <a className={btn2Cls} href={pdfSrc} target="_blank" rel="noreferrer">Open the PDF ↗</a>
                  </div>
                </object>
              : <div className="flex h-[200px] items-center justify-center border border-dashed border-hair text-[14px] text-mute">{draft ? 'Fix compile errors to preview' : 'No saved résumé yet'}</div>}
          </aside>
        </div>
      </>}
    </Section>
  )
}
