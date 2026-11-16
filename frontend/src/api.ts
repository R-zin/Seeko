export async function api<T = any>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = opts
  const r = await fetch('/api' + path, {
    ...rest,
    headers: json !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  })
  if (!r.ok) {
    let msg = r.statusText
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j) } catch {}
    throw new Error(`${r.status}: ${msg}`)
  }
  return r.json()
}

export interface Job {
  id: string; source: string; company: string; title: string; location: string; url: string
  is_easy_apply: boolean; status: string; score: number | null; tailored_summary: string
  tailored_ok: boolean; error: string; notion_page_id: string; missing_keywords: string[]; discovered_at: string; applied_at: string | null
}
export interface JobDetail extends Job { diff: string; base_tex: string; tailored_tex: string; has_pdf: boolean; has_screenshot: boolean; cover_letter: string; jd_text: string; tailor_report: TailorReport | null }

export interface TailorReport {
  segments: number; base_pages: number; pages?: number; engine?: string; fallback?: string
  accepted: { id: string; section: string; kind: string; before: string; after: string }[]
  rejected: { id: string; section?: string; reason: string; proposed?: string }[]
  reverted: { id: string; reason: string }[]
  passes: { pass: string; proposed?: number; accepted?: number; error?: string }[]
}
