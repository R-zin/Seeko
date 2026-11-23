import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type Job } from './api'
import { Badge, cycleTheme, toast } from './ui'

export type Go = (tab: 'pipeline' | 'setup' | 'tracker') => void
type Cmd = { id: string; label: string; hint?: string; group: string; run: () => void | Promise<void> }

const post = (path: string) => api(path, { method: 'POST' })
const tryRun = (fn: () => Promise<unknown>, ok: string) => fn().then(() => toast(ok)).catch((e: any) => toast(e.message, false))

/** ⌘K / Ctrl+K / "/" : jump to any job or run any action from the keyboard. */
export function CommandPalette({ open, onClose, go, showHelp }: { open: boolean; onClose: () => void; go: Go; showHelp: () => void }) {
  const [q, setQ] = useState('')
  const [jobs, setJobs] = useState<Job[]>([])
  const [i, setI] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => { if (open) { setQ(''); setI(0) } }, [open])   // the input focuses itself as it mounts (autoFocus), so no keystroke is lost
  useEffect(() => {   // job search, debounced (full-text over title, company and description)
    if (!open) return
    const t = setTimeout(() => api<Job[]>(`/jobs?${q.trim() ? 'q=' + encodeURIComponent(q.trim()) : ''}`).then(j => setJobs(j.slice(0, 8))).catch(() => setJobs([])), 140)
    return () => clearTimeout(t)
  }, [q, open])

  const actions: Cmd[] = useMemo(() => [
    { id: 'go-p', group: 'Go to', label: 'Pipeline — review queue', hint: 'G P', run: () => go('pipeline') },
    { id: 'go-s', group: 'Go to', label: 'Setup — model, résumé, profile', hint: 'G S', run: () => go('setup') },
    { id: 'go-t', group: 'Go to', label: 'Tracker — agenda, register, searches', hint: 'G T', run: () => go('tracker') },
    { id: 'add', group: 'Actions', label: 'Add a job by link…', run: () => { go('pipeline'); setTimeout(() => window.dispatchEvent(new Event('seeko:add-job')), 50) } },
    { id: 'discover', group: 'Actions', label: 'Run discovery now', run: () => tryRun(() => post('/pipeline/discover'), 'Search started') },
    { id: 'pause', group: 'Actions', label: 'Pause worker', run: () => tryRun(() => post('/pipeline/pause'), 'Worker paused') },
    { id: 'resume', group: 'Actions', label: 'Resume worker', run: () => tryRun(() => post('/pipeline/resume'), 'Worker resumed') },
    { id: 'approve', group: 'Actions', label: 'Approve all ready applications…', run: async () => {
      if (!confirm('Submit every completely filled application now?')) return
      await post('/pipeline/approve-ready').then((r: any) => toast(`Submitted ${r.approved.length} application(s)`)).catch((e: any) => toast(e.message, false))
    } },
    { id: 'digest', group: 'Actions', label: 'Send follow-up digest now', run: () => tryRun(() => post('/agenda/digest'), 'Digest sent') },
    { id: 'notion', group: 'Actions', label: 'Pull edits from Notion', run: () => tryRun(() => post('/notion/pull'), 'Notion edits pulled') },
    { id: 'backup', group: 'Actions', label: 'Back up database now', run: () => tryRun(() => post('/backup/sync'), 'Backup copied') },
    { id: 'theme', group: 'View', label: 'Switch theme (system / dark / light)', hint: 'T', run: () => cycleTheme() },
    { id: 'help', group: 'View', label: 'Keyboard shortcuts', hint: '?', run: showHelp },
  ], [go, showHelp])

  const needle = q.trim().toLowerCase()
  const items: Cmd[] = [
    ...jobs.map(j => ({ id: 'job-' + j.id, group: 'Jobs', label: `${j.title} — ${j.company}`, job: j,
      run: () => { go('pipeline'); setTimeout(() => window.dispatchEvent(new CustomEvent('seeko:select-job', { detail: j.id })), 50) } })),
    ...actions.filter(a => !needle || a.label.toLowerCase().includes(needle) || a.group.toLowerCase().includes(needle)),
  ]
  useEffect(() => { setI(x => Math.min(x, Math.max(0, items.length - 1))) }, [items.length])
  useEffect(() => { listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [i])

  if (!open) return null
  const pick = (c: Cmd) => { onClose(); Promise.resolve(c.run()).catch(() => {}) }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { setI(x => (x + 1) % Math.max(1, items.length)); e.preventDefault() }
    else if (e.key === 'ArrowUp') { setI(x => (x - 1 + items.length) % Math.max(1, items.length)); e.preventDefault() }
    else if (e.key === 'Enter' && items[i]) { pick(items[i]); e.preventDefault() }
    else if (e.key === 'Escape') onClose()
  }
  let last = ''
  return (
    <div className="anim-fade fixed inset-0 z-40 flex items-start justify-center bg-ink/20 px-4 pt-[12vh] backdrop-blur-[2px]" onMouseDown={onClose}>
      <div role="dialog" aria-label="Command palette" className="anim-pop w-full max-w-[620px] overflow-hidden rounded-xl border border-hair bg-paper shadow-[var(--shadow)]"
        onMouseDown={e => e.stopPropagation()} onKeyDown={onKey}>
        <div className="flex items-center gap-3 border-b border-hair px-4">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="shrink-0 text-mute" aria-hidden><path d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35" /></svg>
          <input ref={input} autoFocus value={q} onChange={e => { setQ(e.target.value); setI(0) }} placeholder="Search jobs, or type a command…"
            aria-label="Search jobs or commands" className="h-12 w-full bg-transparent text-[15px] outline-none placeholder:text-mute focus-visible:outline-none" />
          <kbd>Esc</kbd>
        </div>
        <ul ref={listRef} role="listbox" className="max-h-[52vh] overflow-auto p-2">
          {items.map((c, k) => {
            const head = c.group !== last ? (last = c.group) : null
            const job = (c as any).job as Job | undefined
            return (
              <li key={c.id} role="none">
                {head && <div className="px-2 pt-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-mute">{head}</div>}
                <button role="option" aria-selected={k === i} onMouseMove={() => setI(k)} onClick={() => pick(c)}
                  className={`flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left text-[14px] transition-colors duration-100 ${k === i ? 'bg-ink text-paper' : 'text-ink'}`}>
                  <span className="min-w-0 flex-1 truncate">{c.label}</span>
                  {job && (k === i ? <span className="text-[11px] uppercase tracking-[0.08em] opacity-70">{job.status.replace('_', ' ')}</span> : <Badge s={job.status} />)}
                  {c.hint && <span className={`font-mono text-[11px] ${k === i ? 'opacity-70' : 'text-mute'}`}>{c.hint}</span>}
                </button>
              </li>
            )
          })}
          {!items.length && <li className="px-3 py-6 text-center text-[13.5px] text-mute">Nothing matches “{q}”.</li>}
        </ul>
        <div className="flex gap-4 border-t border-hair px-4 py-2 text-[11.5px] text-mute">
          <span><kbd>↑</kbd> <kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span>Jobs search titles, companies and descriptions</span>
        </div>
      </div>
    </div>
  )
}

const SHORTCUTS: [string, string[][]][] = [
  ['Anywhere', [['⌘K', 'Command palette (also Ctrl+K or /)'], ['G P', 'Go to Pipeline'], ['G S', 'Go to Setup'], ['G T', 'Go to Tracker'], ['T', 'Switch theme'], ['?', 'This sheet']]],
  ['Pipeline', [['J / K', 'Next / previous job'], ['A', 'Approve & submit the selected job'], ['S', 'Skip the selected job'], ['O', 'Open the posting'], ['1–5', 'Queue filters']]],
]

export function ShortcutsHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape' || e.key === '?') { e.preventDefault(); onClose() } }
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="anim-fade fixed inset-0 z-40 flex items-center justify-center bg-ink/20 px-4 backdrop-blur-[2px]" onMouseDown={onClose}>
      <div role="dialog" aria-label="Keyboard shortcuts" className="anim-pop w-full max-w-[520px] rounded-xl border border-hair bg-paper p-6 shadow-[var(--shadow)]" onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between"><h2 className="text-[18px] font-semibold tracking-[-0.01em]">Keyboard shortcuts</h2><kbd>Esc</kbd></div>
        {SHORTCUTS.map(([group, rows]) => (
          <div key={group} className="mt-5">
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-mute">{group}</div>
            <ul className="mt-2 divide-y divide-hair">
              {rows.map(([k, d]) => <li key={k} className="flex items-center justify-between py-2 text-[13.5px]"><span>{d}</span><span className="flex gap-1">{k.split(' ').map(x => <kbd key={x}>{x}</kbd>)}</span></li>)}
            </ul>
          </div>
        ))}
      </div>
    </div>
  )
}
