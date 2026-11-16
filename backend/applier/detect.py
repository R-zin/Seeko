from __future__ import annotations
from urllib.parse import urlsplit


def detect_ats_from_url(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    if "greenhouse.io" in h: return "greenhouse"
    if "lever.co" in h: return "lever"
    if "ashbyhq.com" in h: return "ashby"
    if "myworkdayjobs.com" in h: return "workday"
    if "linkedin.com" in h: return "linkedin_easy_apply"
    return "generic"


async def detect_page(page) -> str:
    kind = detect_ats_from_url(page.url)
    if kind != "generic": return kind
    for fr in page.frames[1:]:                      # form embedded in an iframe on the company's own site
        k = detect_ats_from_url(fr.url)
        if k not in ("generic", "linkedin_easy_apply"): return k
    markers = await page.evaluate("""() => ({
      gh: !!document.querySelector('#application_form, #grnhse_app, form[action*="greenhouse"]'),
      lever: !!document.querySelector('.application-form, form[action*="lever.co"]'),
      ashby: !!document.querySelector('[class*="ashby-application"], form[class*="_applicationForm"]'),
      wd: !!document.querySelector('[data-automation-id]')})""")
    return next((n for k, n in (("gh", "greenhouse"), ("lever", "lever"), ("ashby", "ashby"), ("wd", "workday")) if markers[k]), "generic")


async def detect_blockers(page) -> list[str]:
    """CAPTCHA / 2FA / login walls that must pause the run for a human."""
    return await page.evaluate("""() => {
      const b = [];
      if (document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], .g-recaptcha, .h-captcha, [class*="cf-turnstile"], iframe[src*="turnstile"]')) b.push('captcha');
      const t = (document.body.innerText || '').toLowerCase();
      if (/verification code|two-factor|2fa|one-time (pass)?code|enter the code/.test(t) && document.querySelector('input[autocomplete="one-time-code"], input[name*="code" i]')) b.push('2fa');
      if (document.querySelector('input[type=password]') && /sign in|log in/.test(t)) b.push('login');
      return b; }""")
