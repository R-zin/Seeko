import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import Setup from './pages/Setup'
import Pipeline, { typing } from './pages/Pipeline'
import Tracker from './pages/Tracker'
import { api } from './api'
import { readCapture } from './addjob'
import { CommandPalette, ShortcutsHelp } from './palette'
import { Toaster, cycleTheme, useTheme, withTransition } from './ui'

const TABS = [
  { key: 'pipeline', label: 'Pipeline', C: Pipeline },
  { key: 'setup', label: 'Setup', C: Setup },
  { key: 'tracker', label: 'Tracker', C: Tracker },
] as const
type Key = (typeof TABS)[number]['key']

const Icon = ({ d, label }: { d: string; label?: string }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden={!label} aria-label={label}><path d={d} /></svg>
)
export const SEARCH = 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35'
const SUN = 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4'
const MOON = 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z'
const AUTO = 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 3v18'
const isMac = typeof navigator !== 'undefined' && /Mac|iP(hone|ad)/.test(navigator.platform)

/** Segmented control whose black pill slides to the active tab. */
function Tabs({ tab, go }: { tab: Key; go: (k: Key) => void }) {
  const wrap = useRef<HTMLDivElement>(null)
  const [pill, setPill] = useState({ left: 0, width: 0 })
  useLayoutEffect(() => {
    const measure = () => {
      const el = wrap.current?.querySelector<HTMLElement>(`[data-tab="${tab}"]`)
      if (el) setPill({ left: el.offsetLeft, width: el.offsetWidth })
    }
    measure(); window.addEventListener('resize', measure); return () => window.removeEventListener('resize', measure)
  }, [tab])
  return (
    <nav aria-label="Sections" ref={wrap} className="relative flex rounded-lg border border-hair bg-paper-2 p-1">
      <span aria-hidden className="absolute top-1 bottom-1 rounded-md bg-ink shadow-[var(--shadow)] transition-[left,width] duration-300 ease-[cubic-bezier(.22,1,.36,1)]"
        style={{ left: pill.left, width: pill.width }} />
      {TABS.map(t => (
        <button key={t.key} data-tab={t.key} onClick={() => go(t.key)} aria-current={tab === t.key ? 'page' : undefined}
          className={`relative z-10 rounded-md px-3.5 py-1.5 text-[13.5px] font-medium transition-colors duration-300 sm:px-4 ${tab === t.key ? 'text-paper' : 'text-ink-2 hover:text-ink'}`}>
          {t.label}
        </button>
      ))}
    </nav>
  )
}

export default function App() {
  const [capture, setCapture] = useState(readCapture)   // a job sent by the bookmarklet (#add=...)
  const initial = (location.hash.slice(1) as Key) || 'pipeline'
  const [tab, setTabRaw] = useState<Key>(TABS.some(t => t.key === initial) ? initial : 'pipeline')
  const [st, setSt] = useState<any>(null)
  const [palette, setPalette] = useState(false)
  const [help, setHelp] = useState(false)
  const theme = useTheme()

  const go = useCallback((k: Key) => withTransition(() => setTabRaw(k)), [])
  useEffect(() => { if (location.hash !== '#' + tab) history.replaceState(null, '', '#' + tab); window.scrollTo({ top: 0 }) }, [tab])
  useEffect(() => {
    const h = () => {
      if (location.hash.startsWith('#add=')) { const c = readCapture(); if (c) { setCapture(c); setTabRaw('pipeline') } return }
      const k = location.hash.slice(1) as Key; if (TABS.some(t => t.key === k)) setTabRaw(k)
    }
    window.addEventListener('hashchange', h); return () => window.removeEventListener('hashchange', h)
  }, [])
  useEffect(() => {
    const load = () => api('/pipeline/status').then(setSt).catch(() => setSt(null))
    load(); const t = setInterval(load, 5000); return () => clearInterval(t)
  }, [])

  // Global shortcuts: ⌘K / Ctrl+K / "/" palette · ? help · G then P/S/T · T theme
  useEffect(() => {
    let g = 0
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette(p => !p); return }
      if (typing(e) || palette || help) return
      const k = e.key.toLowerCase()
      if (e.key === '/') { e.preventDefault(); setPalette(true) }
      else if (e.key === '?') { e.preventDefault(); setHelp(true) }
      else if (Date.now() - g < 900 && ['p', 's', 't'].includes(k)) { g = 0; e.preventDefault(); go(k === 'p' ? 'pipeline' : k === 's' ? 'setup' : 'tracker') }
      else if (k === 'g') g = Date.now()
      else if (k === 't') cycleTheme()
    }
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h)
  }, [palette, help, go])

  const Page = TABS.find(t => t.key === tab)!.C
  const dot = !st ? 'bg-mute' : !st.llm_configured ? 'border border-ink' : st.paused ? 'border border-mute' : 'bg-ink pulse'

  return (
    <div className="min-h-screen bg-paper text-ink">
      {/* Top bar */}
      <header className="sticky top-0 z-30 border-b border-hair bg-paper/85 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-[1360px] items-center gap-3 px-4 sm:gap-5 sm:px-8 lg:px-12">
          <a href="#pipeline" onClick={e => { e.preventDefault(); go('pipeline') }} className="text-[22px] font-semibold tracking-[-0.05em]">Seeko<span className="text-mute">.</span></a>
          <Tabs tab={tab} go={go} />
          <button onClick={() => setPalette(true)} aria-label="Search jobs or run a command"
            className="ml-auto hidden h-9 min-w-[16rem] items-center gap-2 rounded-lg border border-hair px-3 text-[13px] text-mute transition-colors duration-200 hover:border-ink hover:text-ink md:flex">
            <Icon d={SEARCH} /><span className="flex-1 text-left">Search jobs or run a command</span><kbd>{isMac ? '⌘' : 'Ctrl'} K</kbd>
          </button>
          <button onClick={() => setPalette(true)} aria-label="Command palette" className="ml-auto flex h-9 w-9 items-center justify-center rounded-lg border border-hair md:hidden"><Icon d={SEARCH} /></button>
          <button onClick={cycleTheme} title={`Theme: ${theme} (T)`} aria-label={`Theme: ${theme}. Switch theme`}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-hair transition-[border-color,transform] duration-200 hover:border-ink hover:rotate-12 active:scale-95">
            <Icon d={theme === 'dark' ? MOON : theme === 'light' ? SUN : AUTO} />
          </button>
        </div>
      </header>

      <div className="mx-auto max-w-[1360px] px-4 sm:px-8 lg:px-12">
        {/* Status line */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-6 font-mono text-[12px] text-mute">
          <span className="flex items-center gap-2">
            <i className={`inline-block h-2 w-2 rounded-full ${dot}`} aria-hidden />
            {!st ? 'Server offline'
              : !st.llm_configured ? <a href="#setup" className="text-ink underline underline-offset-4">Idle — no model configured</a>
              : st.paused ? 'Worker paused' : 'Worker running'}
          </span>
          {st && <span>{st.approval_mode ? 'Approval gate' : <span className="font-semibold text-ink">Full-auto</span>}</span>}
          {st && <span>{st.applied_today}/{st.daily_cap} submitted today</span>}
          {st?.searching && <span className="text-ink">Searching…</span>}
          <button onClick={() => setHelp(true)} className="ml-auto hidden hover:text-ink sm:block"><kbd>?</kbd> shortcuts</button>
        </div>

        <main key={tab} className="anim-in pt-8 pb-24">{tab === 'pipeline' ? <Pipeline capture={capture} /> : <Page />}</main>

        <footer className="flex flex-wrap justify-between gap-2 border-t border-hair py-6 text-[12px] text-mute">
          <span>Seeko — local job-application assistant</span>
          <span className="font-mono">nothing is submitted without approval in gate mode</span>
        </footer>
      </div>

      <CommandPalette open={palette} onClose={() => setPalette(false)} go={go} showHelp={() => setHelp(true)} />
      <ShortcutsHelp open={help} onClose={() => setHelp(false)} />
      <Toaster />
    </div>
  )
}
