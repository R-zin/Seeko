"""The Notion tracker schema: which properties Seeko writes, how values are encoded, and how a database compares.

Every property except the title is optional: a property missing from the user's database is simply not written
(sending an unknown property name would make Notion reject the whole page). `Set up database` adds the missing ones
and never changes or removes existing properties. Two-way properties are also read back (workers/notion_service.py),
so edits made in Notion reach Seeko.
"""
from __future__ import annotations
from dataclasses import dataclass, field

TEXT_LIMIT = 2000          # Notion: characters per rich-text object
RICH_TEXT_ITEMS = 100      # Notion: rich-text objects per property / block

STATUS_LABELS = {"scraped": "Scraped", "scored": "Scored", "tailoring": "Tailoring", "tailored": "Tailored",
                 "applying": "Applying", "needs_review": "Needs Review", "applied": "Applied", "failed": "Failed",
                 "skipped": "Skipped"}
APPLICATION_COLORS = {"Not applied": "default", "Applied": "blue", "Assessment": "purple", "Interviewing": "yellow",
                      "Offer": "green", "Accepted": "green", "Rejected": "red", "Withdrawn": "gray",
                      "No response": "brown"}
STATUS_COLORS = {"Scraped": "default", "Scored": "gray", "Tailoring": "brown", "Tailored": "blue", "Applying": "purple",
                 "Needs Review": "orange", "Applied": "green", "Failed": "red", "Skipped": "gray"}


@dataclass(frozen=True)
class Prop:
    name: str                       # property name in Notion
    type: str                       # type it is created with
    field: str                      # key in the values dict built from a job
    accepts: frozenset = frozenset()  # types Seeko can write into (defaults to {type})
    two_way: bool = False           # read back from Notion
    options: tuple = ()             # (name, color) for select properties created by setup

    def writable(self, notion_type: str) -> bool:
        return notion_type in (self.accepts or {self.type})


def _opts(colors: dict) -> tuple:
    return tuple(colors.items())


PROPS: tuple[Prop, ...] = (
    Prop("Role", "title", "title"),
    Prop("Company", "rich_text", "company", frozenset({"rich_text", "select"})),
    Prop("Status", "select", "status", frozenset({"select", "status"}), options=_opts(STATUS_COLORS)),
    Prop("Application Status", "select", "application_status", frozenset({"select", "status"}), two_way=True,
         options=_opts(APPLICATION_COLORS)),
    Prop("Applied Date", "date", "applied_on", two_way=True),
    Prop("Next Step", "rich_text", "next_step", two_way=True),
    Prop("Next Step Date", "date", "next_step_date", two_way=True),
    Prop("Notes", "rich_text", "notes", two_way=True),
    Prop("Job Type", "select", "job_type", frozenset({"select", "rich_text"}), options=(("Job", "default"), ("Internship", "purple"))),
    Prop("Location", "rich_text", "location"),
    Prop("Remote", "checkbox", "remote"),
    Prop("Source", "select", "source", frozenset({"select", "rich_text"})),
    Prop("Job URL", "url", "url"),
    Prop("Match Score", "number", "score"),
    Prop("Employment Type", "rich_text", "employment_type", frozenset({"rich_text", "select"})),
    Prop("Discovered", "date", "discovered_on"),
    Prop("Posted", "date", "posted_on"),
    Prop("Apply By", "date", "apply_by"),
    Prop("Closed", "date", "closed_on"),
    Prop("Tailored Summary", "rich_text", "summary"),
    Prop("Seeko ID", "rich_text", "seeko_id"),
)
TITLE = PROPS[0]
BY_NAME = {p.name: p for p in PROPS}
TWO_WAY = tuple(p for p in PROPS if p.two_way)


@dataclass
class SchemaReport:
    """How a Notion database's properties line up with PROPS. `mapping` is what Seeko will actually write."""
    mapping: dict[str, str] = field(default_factory=dict)          # Seeko property name -> its Notion type
    title_property: str = ""                                       # the database's title property (may not be "Role")
    missing: list[str] = field(default_factory=list)
    wrong_type: list[dict] = field(default_factory=list)
    status_options: dict[str, list[str]] = field(default_factory=dict)   # status-type properties: their option names

    @property
    def ok(self) -> bool:
        return bool(self.title_property)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "title_property": self.title_property, "mapped": sorted(self.mapping),
                "missing": self.missing, "wrong_type": self.wrong_type, "status_options": self.status_options,
                "two_way": [p.name for p in TWO_WAY if p.name in self.mapping]}


def check(properties: dict) -> SchemaReport:
    r = SchemaReport()
    titles = [n for n, p in properties.items() if p.get("type") == "title"]
    if "Role" in titles: r.title_property = "Role"
    elif titles: r.title_property = titles[0]
    if r.title_property: r.mapping[TITLE.name] = "title"
    for p in PROPS[1:]:
        have = properties.get(p.name)
        if have is None:
            r.missing.append(p.name); continue
        t = have.get("type", "")
        if not p.writable(t):
            r.wrong_type.append({"property": p.name, "found": t, "expected": sorted(p.accepts or {p.type})}); continue
        r.mapping[p.name] = t
        if t == "status":
            r.status_options[p.name] = [o.get("name", "") for o in (have.get("status") or {}).get("options", [])]
    return r


def setup_patch(report: SchemaReport) -> dict:
    """Schema update that adds every missing property (existing properties are left exactly as they are)."""
    out = {}
    for name in report.missing:
        p = BY_NAME[name]
        if p.type == "select":
            out[name] = {"select": {"options": [{"name": n, "color": c} for n, c in p.options]}}
        else:
            out[name] = {p.type: {}}
    return out


# ── values ───────────────────────────────────────────────────────────────────────────────────────────────────────
_SKIP = object()


def rich_text(text: str) -> list[dict]:
    text = text or ""
    chunks = [text[i:i + TEXT_LIMIT] for i in range(0, len(text), TEXT_LIMIT)][:RICH_TEXT_ITEMS]
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def _option(value: str) -> str:
    return value.replace(",", " ").strip()[:100]   # Notion select options cannot contain commas


def encode(notion_type: str, value, status_options: list[str] | None = None):
    """Notion property value for `value`, or _SKIP when it cannot be written (e.g. unknown status option)."""
    if notion_type == "title": return {"title": rich_text(str(value or ""))}
    if notion_type == "rich_text": return {"rich_text": rich_text("" if value is None else str(value))}
    if notion_type == "select": return {"select": {"name": _option(str(value))} if value not in (None, "") else None}
    if notion_type == "status":
        if not value: return _SKIP
        name = next((o for o in status_options or [] if o.lower() == str(value).lower()), None)
        return {"status": {"name": name}} if name else _SKIP
    if notion_type == "url": return {"url": value or None}
    if notion_type == "number": return {"number": value}
    if notion_type == "checkbox": return {"checkbox": bool(value)}
    if notion_type == "date": return {"date": {"start": value} if value else None}
    return _SKIP


def encode_properties(values: dict, report: SchemaReport) -> dict:
    """Notion `properties` for a page, limited to what this database has."""
    out = {}
    for p in PROPS:
        t = report.mapping.get(p.name)
        if not t: continue
        v = encode(t, values.get(p.field), report.status_options.get(p.name))
        if v is not _SKIP:
            out[report.title_property if p is TITLE else p.name] = v
    return out


def decode(prop: dict | None) -> str:
    """A page property (API response form) as the plain string Seeko compares two-way values with."""
    if not prop: return ""
    t = prop.get("type") or next((k for k in ("title", "rich_text", "select", "status", "date", "url", "number",
                                                "checkbox") if k in prop), "")
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text") or (x.get("text") or {}).get("content", "") for x in v or []).strip()
    if t in ("select", "status"): return ((v or {}).get("name") or "").strip()
    if t == "date": return ((v or {}).get("start") or "")[:10]
    if t == "checkbox": return "true" if v else ""
    return "" if v is None else str(v)


# ── page body: the job description ───────────────────────────────────────────────────────────────────────────────
BLOCK_BATCH = 100           # Notion: children per request
MAX_BODY_CHARS = 100_000


def _block(kind: str, text: str) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text)}}


def description_blocks(text: str) -> list[dict]:
    """The job description as Notion blocks: a heading, then one paragraph / bullet per line. Long descriptions are
    packed into fewer, larger paragraphs so a page needs at most a few requests."""
    text = (text or "").strip()[:MAX_BODY_CHARS]
    if not text: return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items: list[tuple[str, str]] = []
    for ln in lines:
        if ln[:2] in ("- ", "* ", "• ") or ln[:1] == "•":
            items.append(("bulleted_list_item", ln.lstrip("-*• ").strip()))
        else:
            items.append(("paragraph", ln))
    if len(items) > 2 * BLOCK_BATCH:    # very long: pack lines into ~TEXT_LIMIT-sized paragraphs
        items, buf = [], ""
        for ln in lines:
            if buf and len(buf) + len(ln) + 1 > TEXT_LIMIT:
                items.append(("paragraph", buf)); buf = ""
            buf = f"{buf}\n{ln}" if buf else ln
        if buf: items.append(("paragraph", buf))
    return [_block("heading_2", "Job description")] + [_block(k, t) for k, t in items]
