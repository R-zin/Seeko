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
  job_type: 'job' | 'internship'; employment_type: string; is_remote: boolean; jd_source: string; relevance: number | null
  times_seen: number; posted_at: string | null; last_seen_at: string | null; closed_at: string | null
  valid_through: string | null
  /* Your own tracking (editable here and in Notion; synced both ways) */
  application_status: string; next_step: string; next_step_date: string | null; notes: string
  notion_state: 'synced' | 'pending' | 'error' | 'deleted' | 'off'; notion_error: string
}

export type Tracking = Partial<Pick<Job, 'application_status' | 'next_step' | 'next_step_date' | 'notes'>> & { applied_at?: string | null }

/** Application statuses (keys as stored, labels as shown here and in Notion). A custom label made in Notion is kept as-is. */
export const APPLICATION_STATUSES: [string, string][] = [
  ['not_applied', 'Not applied'], ['applied', 'Applied'], ['assessment', 'Assessment'], ['interviewing', 'Interviewing'],
  ['offer', 'Offer'], ['accepted', 'Accepted'], ['rejected', 'Rejected'], ['withdrawn', 'Withdrawn'], ['ghosted', 'No response'],
]
export const appLabel = (k: string) => APPLICATION_STATUSES.find(([x]) => x === k)?.[1] ?? k

export interface NotionStatus {
  configured: boolean; connected: boolean; running: boolean; synced: number; pending: number; errors: number
  last_push_at: string | null; last_pull_at: string | null; last_error: string; backoff_until: string | null
  schema: { ok: boolean; title_property: string; mapped: string[]; missing: string[]; two_way: string[]
            wrong_type: { property: string; found: string; expected: string[] }[] } | null
}

export interface BackupStatus {
  configured: boolean; target: string; interval_s: number; running: boolean
  last_sync_at: string | null; last_full_at: string | null; last_error: string; last_copied: number; rows: Record<string, number>
}

export interface SearchRun {
  id: string; status: 'running' | 'done' | 'failed' | 'interrupted'; trigger: string
  started_at: string; finished_at: string | null; error: string
  spec: { role_titles?: string[]; sources?: string[]; job_types?: string[] }
  stats: {
    leads?: number; known?: number; new?: number; new_jobs?: number; new_internships?: number; read?: number
    deferred?: number; errors?: number; rejected?: Record<string, number>; blocked?: Record<string, string>
    closed?: number; reopened?: number; skipped_sources?: Record<string, string>
    progress?: { step: string; leads: number; read: number; sources_done: string[]; at?: string }
  }
}
export interface JobDetail extends Job { diff: string; base_tex: string; tailored_tex: string; has_pdf: boolean; has_screenshot: boolean; cover_letter: string; jd_text: string; tailor_report: TailorReport | null }

export interface TailorReport {
  segments: number; base_pages: number; pages?: number; engine?: string; fallback?: string
  accepted: { id: string; section: string; kind: string; before: string; after: string }[]
  rejected: { id: string; section?: string; reason: string; proposed?: string }[]
  reverted: { id: string; reason: string }[]
  passes: { pass: string; proposed?: number; accepted?: number; error?: string }[]
}
