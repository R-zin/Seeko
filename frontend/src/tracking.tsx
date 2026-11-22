import { useEffect, useState } from 'react'
import { APPLICATION_STATUSES, api, appLabel, type Job, type Tracking } from './api'
import { Field, Kicker, Msg, areaCls, btnCls, inputCls, useAction } from './ui'

const selectCls = inputCls + ' cursor-pointer pr-6'

/** Application status picker. A custom status created in Notion stays selectable. */
export function ApplicationSelect({ value, onChange, className = '', label }: { value: string; onChange: (v: string) => void; className?: string; label?: string }) {
  const known = APPLICATION_STATUSES.some(([k]) => k === value)
  return (
    <select aria-label={label ?? 'Application status'} className={`${selectCls} ${className}`} value={value} onChange={e => onChange(e.target.value)}>
      {!known && <option value={value}>{value}</option>}
      {APPLICATION_STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  )
}

/** Is this job's Notion page up to date? */
export function NotionMark({ j }: { j: Pick<Job, 'notion_state' | 'notion_error'> }) {
  const tone: Record<Job['notion_state'], [string, string]> = {
    synced: ['synced', 'text-ink-2'], pending: ['pending', 'text-mute'], error: ['error', 'text-accent'],
    deleted: ['deleted', 'text-warn'], off: ['—', 'text-mute'],
  }
  const [t, c] = tone[j.notion_state] ?? ['—', 'text-mute']
  const title = j.notion_state === 'error' ? j.notion_error
    : j.notion_state === 'deleted' ? 'The page was deleted in Notion. Setup → Notion → Resync all recreates it.'
    : j.notion_state === 'pending' ? 'Waiting to be pushed to Notion' : undefined
  return <span className={`font-mono text-[12px] ${c}`} title={title}>{t}</span>
}

/** Your own tracking of one job: status after applying, dates, next step, notes. Saved changes reach Notion within seconds. */
export function TrackingPanel({ job, onSaved }: { job: Job; onSaved?: (j: Job) => void }) {
  const init = () => ({ application_status: job.application_status, applied_at: job.applied_at?.slice(0, 10) ?? '',
    next_step: job.next_step, next_step_date: job.next_step_date ?? '', notes: job.notes })
  const [t, setT] = useState(init)
  const a = useAction()
  useEffect(() => setT(init()), [job.id])   // eslint-disable-line react-hooks/exhaustive-deps

  const save = () => a.run(async () => {
    const body: Tracking = { application_status: t.application_status, next_step: t.next_step, notes: t.notes,
      next_step_date: t.next_step_date || null, applied_at: t.applied_at || null }
    const j = await api<Job>(`/jobs/${job.id}`, { method: 'PATCH', json: body })
    setT({ ...t, applied_at: j.applied_at?.slice(0, 10) ?? '' }); onSaved?.(j)
    return `Saved — ${appLabel(j.application_status).toLowerCase()}. ${j.notion_state === 'off' ? '' : 'Notion updates in a few seconds.'}`
  })

  return (
    <section className="mt-10 border-t border-ink pt-3" aria-label="Your tracking">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Kicker>Your tracking</Kicker>
        <span className="text-[12px] text-mute">Also editable in Notion — edits there come back here.</span>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2 xl:grid-cols-4">
        <Field label="Application"><ApplicationSelect value={t.application_status} onChange={v => setT({ ...t, application_status: v })} /></Field>
        <Field label="Applied on"><input type="date" className={inputCls + ' tabular-nums'} value={t.applied_at} onChange={e => setT({ ...t, applied_at: e.target.value })} /></Field>
        <Field label="Next step"><input className={inputCls} value={t.next_step} maxLength={300} placeholder="e.g. Technical interview" onChange={e => setT({ ...t, next_step: e.target.value })} /></Field>
        <Field label="Next step date"><input type="date" className={inputCls + ' tabular-nums'} value={t.next_step_date} onChange={e => setT({ ...t, next_step_date: e.target.value })} /></Field>
      </div>
      <Field label="Notes" className="mt-5">
        <textarea className={areaCls} rows={3} maxLength={20000} value={t.notes} placeholder="Recruiter, referral, salary range, what was asked…" onChange={e => setT({ ...t, notes: e.target.value })} />
      </Field>
      <div className="mt-4 flex flex-wrap items-center gap-4">
        <button className={btnCls} disabled={a.busy} onClick={save}>{a.busy ? 'Saving…' : 'Save tracking'}</button>
        <span className="text-[12px] text-mute">Marking a job applied, interviewing or rejected also stops Seeko from applying to it.</span>
      </div>
      <Msg m={a.msg} />
    </section>
  )
}
