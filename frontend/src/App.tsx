import { useEffect, useState } from 'react'
import Setup from './pages/Setup'
import Pipeline from './pages/Pipeline'
import Tracker from './pages/Tracker'
import { api } from './api'
import { readCapture } from './addjob'

const TABS = [
  { key: 'pipeline', n: '01', label: 'Pipeline', sub: 'Review queue', C: Pipeline },
  { key: 'setup', n: '02', label: 'Setup', sub: 'Model, résumé, profile', C: Setup },
  { key: 'tracker', n: '03', label: 'Tracker', sub: 'Register & log', C: Tracker },
] as const
type Key = (typeof TABS)[number]['key']

const today = new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })

export default function App() {
  const [capture, setCapture] = useState(readCapture)   // a job sent by the bookmarklet (#add=...)
  const initial = (location.hash.slice(1) as Key) || 'pipeline'
  const [tab, setTab] = useState<Key>(TABS.some(t => t.key === initial) ? initial : 'pipeline')
  const [st, setSt] = useState<any>(null)
  useEffect(() => { if (location.hash !== '#' + tab) history.replaceState(null, '', '#' + tab); window.scrollTo({ top: 0 }) }, [tab])
  useEffect(() => {
    const h = () => {
      if (location.hash.startsWith('#add=')) { const c = readCapture(); if (c) { setCapture(c); setTab('pipeline') } return }
      const k = location.hash.slice(1) as Key; if (TABS.some(t => t.key === k)) setTab(k)
    }
    window.addEventListener('hashchange', h); return () => window.removeEventListener('hashchange', h)
  }, [])
  useEffect(() => {
    const load = () => api('/pipeline/status').then(setSt).catch(() => setSt(null))
    load(); const t = setInterval(load, 5000); return () => clearInterval(t)
  }, [])
  const Page = TABS.find(t => t.key === tab)!.C

  return (
    <div className="min-h-screen bg-paper text-ink">
      <div className="mx-auto max-w-[1360px] px-4 sm:px-8 lg:px-12">
        {/* Masthead */}
        <header className="pt-6 sm:pt-10">
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-hair pb-3 text-[12px] text-mute">
            <span className="font-medium uppercase tracking-[0.14em]">{today}</span>
            <span className="flex items-center gap-2 font-mono">
              <i className={`inline-block h-2 w-2 ${!st ? 'bg-mute' : !st.llm_configured ? 'bg-accent' : st.paused ? 'border border-ink' : 'bg-ok pulse'}`} aria-hidden />
              {!st ? 'Server offline'
                : !st.llm_configured ? <a href="#setup" className="text-accent underline underline-offset-4">Idle — no model configured</a>
                : st.paused ? 'Worker paused' : 'Worker running'}
              {st && <> · {st.approval_mode ? 'Approval gate' : <span className="text-accent">Full-auto</span>} · {st.applied_today}/{st.daily_cap} today</>}
            </span>
          </div>
          <div className="grid grid-cols-12 items-end gap-x-6 pt-5 sm:pt-8">
            <h1 className="col-span-12 text-[clamp(64px,13vw,184px)] font-extrabold leading-[0.8] tracking-[-0.055em] md:col-span-8">
              Seeko<span className="text-accent">.</span>
            </h1>
            <p className="col-span-12 mt-5 max-w-[38ch] text-[15px] leading-snug text-ink-2 md:col-span-4 md:mt-0 md:pb-3">
              Finds roles, tailors your LaTeX résumé without inventing a word, fills the form — and waits for your nod before it submits.
            </p>
          </div>
        </header>

        {/* Navigation */}
        <nav aria-label="Sections" className="mt-8 grid grid-cols-3 border-y-2 border-rule sm:mt-12">
          {TABS.map(t => {
            const on = tab === t.key
            return (
              <button key={t.key} onClick={() => setTab(t.key)} aria-current={on ? 'page' : undefined}
                className={`group relative flex flex-col items-start gap-0.5 border-r border-hair px-3 py-3 text-left last:border-r-0 sm:flex-row sm:items-baseline sm:gap-3 sm:px-4 sm:py-4 ${on ? 'bg-ink text-paper' : 'hover:bg-paper-2'}`}>
                <span className={`font-mono text-[12px] ${on ? 'text-accent' : 'text-mute'}`}>{t.n}</span>
                <span className="text-[17px] font-bold tracking-[-0.01em] sm:text-[20px]">{t.label}</span>
                <span className={`hidden text-[12px] lg:inline ${on ? 'text-paper/60' : 'text-mute'}`}>{t.sub}</span>
              </button>
            )
          })}
        </nav>

        <main className="pt-10 pb-24">{tab === 'pipeline' ? <Pipeline capture={capture} /> : <Page />}</main>

        <footer className="flex flex-wrap justify-between gap-2 border-t border-hair py-6 text-[12px] text-mute">
          <span>Seeko — local job-application assistant</span>
          <span className="font-mono">127.0.0.1 · nothing is submitted without approval in gate mode</span>
        </footer>
      </div>
    </div>
  )
}
