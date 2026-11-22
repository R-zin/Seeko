import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { applyTheme } from './ui'

applyTheme()   // before first paint: no flash of the wrong theme

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
