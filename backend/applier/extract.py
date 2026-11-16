"""Extract visible form controls (DOM + labels + accessible names) and tag them with data-seeko-id."""
from __future__ import annotations
from pydantic import BaseModel

JS = """(start) => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || e.type === 'file'); };
  const label = e => {
    if (e.type === 'radio') { const fs0 = e.closest('fieldset'); if (fs0 && fs0.querySelector('legend')) return fs0.querySelector('legend').innerText.trim(); }
    if (e.labels && e.labels.length) return [...e.labels].map(l => l.innerText).join(' ').trim();
    const al = e.getAttribute('aria-label'); if (al) return al;
    const lb = e.getAttribute('aria-labelledby'); if (lb) { const x = document.getElementById(lb); if (x) return x.innerText.trim(); }
    const fs = e.closest('fieldset'); if (fs && fs.querySelector('legend')) return fs.querySelector('legend').innerText.trim();
    const p = e.closest('label, .field, .form-group, [class*="field"], div'); return p ? p.innerText.split('\\n')[0].trim() : '';
  };
  const out = []; let n = start; const seenGroups = new Set();
  document.querySelectorAll('input, select, textarea').forEach(e => {
    const t = (e.type || e.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(t) || !vis(e) || e.disabled) return;
    if (t === 'radio') { const g = 'r:' + e.name; if (seenGroups.has(g)) return; seenGroups.add(g); }
    const id = String(n++); e.setAttribute('data-seeko-id', id);
    let options = [];
    if (e.tagName === 'SELECT') options = [...e.options].filter(o => o.value !== '').map(o => o.text.trim());
    if (t === 'radio') options = [...document.querySelectorAll('input[type=radio][name="' + e.name + '"]')].map(r => (r.labels && r.labels[0] ? r.labels[0].innerText : r.value).trim());
    out.push({ id, kind: e.tagName === 'SELECT' ? 'select' : e.tagName === 'TEXTAREA' ? 'textarea' : t,
      label: label(e).replace(/[\u2731*\u2217]/g, '').replace(/\s+/g, ' ').trim().slice(0, 300), name: e.name || '', dom_id: e.id || '', required: e.required || e.getAttribute('aria-required') === 'true',
      options, current: (e.value || '').slice(0, 100), placeholder: e.placeholder || '' });
  });
  return out; }"""


class Field(BaseModel):
    id: str; frame: int = 0; kind: str; label: str = ""; name: str = ""; dom_id: str = ""; required: bool = False
    options: list[str] = []; current: str = ""; placeholder: str = ""


def frame_of(page, f: "Field"):
    return page.frames[f.frame]


def loc(page, f: "Field"):
    return frame_of(page, f).locator(f'[data-seeko-id="{f.id}"]')


async def extract_fields(page) -> list[Field]:
    """Extract from the main page AND every iframe (embedded Greenhouse/Ashby/etc. forms)."""
    out: list[Field] = []
    for i, fr in enumerate(page.frames):
        try: raw = await fr.evaluate(JS, len(out))
        except Exception: continue          # detached / cross-origin-blocked frame
        out += [Field(**f, frame=i) for f in raw]
    return out


COOKIE_SEL = ("button:has-text('Accept all'), button:has-text('Accept All'), button:has-text('Accept'), "
              "button:has-text('I agree'), button:has-text('Got it'), button[aria-label*='close' i]")
REVEAL_SEL = ("a:has-text('Apply for this job'), button:has-text('Apply for this job'), a:has-text('Apply now'), "
              "button:has-text('Apply now'), [role=tab]:has-text('Application'), a:has-text('Application'), "
              "button:has-text('Application'), a:has-text('Apply'), button:has-text('Apply')")


async def reveal_form(page, min_fields: int = 3) -> list[Field]:
    """Dismiss cookie banners and open the application form/tab if it isn't showing yet."""
    fields = await extract_fields(page)
    if sum(f.kind not in ("hidden",) and f.label.lower() not in ("search",) for f in fields) >= min_fields: return fields
    for sel in (COOKIE_SEL, REVEAL_SEL):
        for fr in page.frames:
            el = fr.locator(sel).first
            try:
                if await el.count() and await el.is_visible():
                    await el.click(timeout=2500); await page.wait_for_timeout(1500); break
            except Exception: continue
        fields = await extract_fields(page)
        if len(fields) >= min_fields: break
    return fields
