import { useEffect, useState } from 'react'
import { api, type SavedQ } from './api'
import { Field, Kicker, Msg, Section, areaCls, btn2Cls, btnCls, inputCls, linkBtnCls, useAction } from './ui'

const enc = encodeURIComponent

/** An input shaped like the form's own control: choices for select/radio, Yes/No for a checkbox, text otherwise. */
export function QuestionInput({ q, value, onChange }: { q: SavedQ; value: string; onChange: (v: string) => void }) {
  const choices = q.options.length ? q.options : q.kind === 'checkbox' ? ['Yes', 'No'] : []
  if (choices.length && choices.length <= 6)
    return (
      <div role="radiogroup" aria-label={q.question} className="mt-1 flex flex-wrap gap-2">
        {choices.map(o => (
          <button key={o} role="radio" aria-checked={value === o} onClick={() => onChange(o)}
            className={`border px-3 py-1.5 text-[13px] ${value === o ? 'border-ink bg-ink text-paper' : 'border-hair hover:border-ink'}`}>{o}</button>
        ))}
      </div>
    )
  if (choices.length)
    return <select aria-label={q.question} className={inputCls + ' cursor-pointer'} value={value} onChange={e => onChange(e.target.value)}>
      <option value="">Choose…</option>{choices.map(o => <option key={o}>{o}</option>)}</select>
  if (q.kind === 'textarea') return <textarea aria-label={q.question} className={areaCls} rows={3} value={value} onChange={e => onChange(e.target.value)} />
  return <input aria-label={q.question} className={inputCls} value={value} onChange={e => onChange(e.target.value)} />
}

/** In a paused job's review: the questions its form could not answer. Answering them fills the form again. */
export function JobQuestions({ questions, onSaved }: { questions: SavedQ[]; onSaved: (message: string) => void }) {
  const [v, setV] = useState<Record<string, string>>(() => Object.fromEntries(questions.map(q => [q.key, q.fits ? q.answer ?? '' : ''])))
  const a = useAction()
  if (!questions.length) return null
  const save = () => a.run(async () => {
    let requeued: string[] = []
    for (const q of questions) if ((v[q.key] ?? '').trim())
      requeued = (await api<{ requeued: string[] }>(`/answers/${enc(q.key)}`, { method: 'PUT', json: { answer: v[q.key] } })).requeued
    const msg = requeued.length ? `Answers saved. ${requeued.length} paused form(s) will be filled again with them.`
      : 'Saved. Answer the rest (or fill them in the open browser and approve) to complete this form.'
    onSaved(msg)   // the panel goes away once the job is re-queued, so the review shows the message
    return msg
  })
  return (
    <section className="mt-8 border border-accent p-5" aria-label="Questions this form needs">
      <Kicker className="text-accent">This form needs {questions.length} answer{questions.length === 1 ? '' : 's'} from you</Kicker>
      <p className="mt-1 text-[12.5px] text-mute">Saved answers are reused on every future form that asks the same thing.</p>
      <div className="mt-4 space-y-5">
        {questions.map(q => (
          <div key={q.key}>
            <p className="text-[14px] font-semibold">{q.question}</p>
            {q.answer && !q.fits && <p className="text-[12px] text-warn">Your saved answer “{q.answer}” is not one of this form’s options.</p>}
            <QuestionInput q={q} value={v[q.key] ?? ''} onChange={x => setV({ ...v, [q.key]: x })} />
          </div>
        ))}
      </div>
      <div className="mt-5"><button className={btnCls} disabled={a.busy} onClick={save}>{a.busy ? 'Saving…' : 'Save answers & fill again'}</button></div>
      <Msg m={a.msg} />
    </section>
  )
}

/** Setup: every question forms have asked, with your answers. */
export function AnswersSection() {
  const [d, setD] = useState<{ pending: SavedQ[]; saved: SavedQ[] } | null>(null)
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [nq, setNq] = useState({ question: '', answer: '' })
  const a = useAction()
  const load = () => api('/answers').then(setD).catch(() => setD({ pending: [], saved: [] }))
  useEffect(() => { load() }, [])
  const put = (key: string, answer: string) => a.run(async () => {
    const r = await api<{ requeued: string[] }>(`/answers/${enc(key)}`, { method: 'PUT', json: { answer } }); load()
    return r.requeued.length ? `Saved — ${r.requeued.length} paused form(s) will be filled again.` : 'Saved.'
  })
  const del = (key: string) => a.run(async () => { await api(`/answers/${enc(key)}`, { method: 'DELETE' }); load(); return 'Removed.' })

  return (
    <Section n="05" title="Answers" note="Questions application forms asked that your profile and résumé do not answer. Answer once — every later form reuses it.">
      {!d ? <div className="h-24 bg-paper-2" /> : <>
        {d.pending.length > 0 && <>
          <Kicker className="text-accent">Waiting for you · {d.pending.length}</Kicker>
          <ul className="mt-2 border-t border-ink">
            {d.pending.map(q => (
              <li key={q.key} className="border-b border-hair py-4">
                <p className="text-[14px] font-semibold">{q.question}
                  <span className="ml-2 font-normal text-mute">asked {q.times_seen}×{q.jobs_waiting ? ` · ${q.jobs_waiting} job${q.jobs_waiting === 1 ? '' : 's'} waiting` : ''}</span></p>
                {q.answer && <p className="text-[12px] text-warn">“{q.answer}” is not one of the options the last form offered.</p>}
                <div className="mt-1 flex flex-wrap items-end gap-3">
                  <div className="min-w-[16rem] flex-1"><QuestionInput q={q} value={edit[q.key] ?? ''} onChange={x => setEdit({ ...edit, [q.key]: x })} /></div>
                  <button className={btn2Cls} disabled={a.busy || !(edit[q.key] ?? '').trim()} onClick={() => put(q.key, edit[q.key])}>Save</button>
                </div>
              </li>
            ))}
          </ul>
        </>}
        <Kicker className={`block ${d.pending.length ? 'mt-10' : ''}`}>Saved answers · {d.saved.length}</Kicker>
        <ul className="mt-2 border-t border-ink">
          {d.saved.map(q => (
            <li key={q.key} className="grid grid-cols-1 gap-x-6 gap-y-1 border-b border-hair py-3 sm:grid-cols-[1fr_1fr_auto] sm:items-baseline">
              <span className="text-[14px]">{q.question}<span className="ml-2 text-[12px] text-mute">{q.source === 'learned' ? 'learned from a form' : ''}{q.times_seen ? ` · used ${q.times_seen}×` : ''}</span></span>
              {edit[q.key] !== undefined
                ? <QuestionInput q={q} value={edit[q.key]} onChange={x => setEdit({ ...edit, [q.key]: x })} />
                : <span className="text-[14px] font-semibold">{q.answer}</span>}
              <span className="flex gap-3">
                {edit[q.key] !== undefined
                  ? <button className={linkBtnCls} onClick={() => { put(q.key, edit[q.key]); const { [q.key]: _, ...rest } = edit; setEdit(rest) }}>Save</button>
                  : <button className={linkBtnCls} onClick={() => setEdit({ ...edit, [q.key]: q.answer ?? '' })}>Edit</button>}
                <button className={linkBtnCls} onClick={() => del(q.key)}>Remove</button>
              </span>
            </li>
          ))}
          {!d.saved.length && <li className="py-4 text-[13.5px] text-mute">Nothing saved yet. Questions appear here as forms ask them; you can also add common ones now.</li>}
        </ul>
        <div className="mt-6 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <Field label="Question"><input className={inputCls} value={nq.question} placeholder="What is your notice period?" onChange={e => setNq({ ...nq, question: e.target.value })} /></Field>
          <Field label="Your answer"><input className={inputCls} value={nq.answer} placeholder="30 days" onChange={e => setNq({ ...nq, answer: e.target.value })} /></Field>
          <button className={btn2Cls} disabled={a.busy || !nq.question.trim() || !nq.answer.trim()} onClick={() => a.run(async () => {
            await api('/answers', { method: 'POST', json: nq }); setNq({ question: '', answer: '' }); load(); return 'Added.'
          })}>Add</button>
        </div>
        <Msg m={a.msg} />
      </>}
    </Section>
  )
}
