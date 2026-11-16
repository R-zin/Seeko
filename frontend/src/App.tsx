import { useState } from 'react'
import Setup from './pages/Setup'
import Pipeline from './pages/Pipeline'
import Tracker from './pages/Tracker'

const TABS = { 'Setup & Profile': Setup, 'Pipeline & Review': Pipeline, 'Tracker & Logs': Tracker } as const

export default function App() {
  const [tab, setTab] = useState<keyof typeof TABS>('Pipeline & Review'); const Page = TABS[tab]
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-6 px-4 py-3">
          <h1 className="text-lg font-bold tracking-tight">Seeko</h1>
          <nav className="flex gap-1">{(Object.keys(TABS) as (keyof typeof TABS)[]).map(t => (
            <button key={t} onClick={() => setTab(t)} className={`rounded px-3 py-1.5 text-sm ${tab === t ? 'bg-indigo-600 text-white' : 'hover:bg-slate-100 dark:hover:bg-slate-800'}`}>{t}</button>))}</nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl p-4"><Page /></main>
    </div>)
}
