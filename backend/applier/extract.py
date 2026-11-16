"""Extract visible form controls (DOM + labels + accessible names) and tag them with data-seeko-id."""
from __future__ import annotations
from pydantic import BaseModel

JS = """() => {
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
  const out = []; let n = 0; const seenGroups = new Set();
  document.querySelectorAll('input, select, textarea').forEach(e => {
    const t = (e.type || e.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(t) || !vis(e) || e.disabled) return;
    if (t === 'radio') { const g = 'r:' + e.name; if (seenGroups.has(g)) return; seenGroups.add(g); }
    const id = String(n++); e.setAttribute('data-seeko-id', id);
    let options = [];
    if (e.tagName === 'SELECT') options = [...e.options].filter(o => o.value !== '').map(o => o.text.trim());
    if (t === 'radio') options = [...document.querySelectorAll('input[type=radio][name="' + e.name + '"]')].map(r => (r.labels && r.labels[0] ? r.labels[0].innerText : r.value).trim());
    out.push({ id, kind: e.tagName === 'SELECT' ? 'select' : e.tagName === 'TEXTAREA' ? 'textarea' : t,
      label: label(e).slice(0, 300), name: e.name || '', required: e.required || e.getAttribute('aria-required') === 'true',
      options, current: (e.value || '').slice(0, 100), placeholder: e.placeholder || '' });
  });
  return out; }"""


class Field(BaseModel):
    id: str; kind: str; label: str = ""; name: str = ""; required: bool = False
    options: list[str] = []; current: str = ""; placeholder: str = ""


async def extract_fields(page) -> list[Field]:
    return [Field(**f) for f in await page.evaluate(JS)]
