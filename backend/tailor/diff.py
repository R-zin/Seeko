import difflib

def unified(base: str, new: str) -> str:
    return "".join(difflib.unified_diff(base.splitlines(True), new.splitlines(True), "base_resume.tex", "tailored_resume.tex"))
