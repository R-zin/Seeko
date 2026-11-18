"""Graph state. Values stay JSON-native (dicts, lists, ISO date strings) so checkpoints serialize cleanly."""
from __future__ import annotations
import operator
from typing import Annotated, TypedDict


def merge_stats(a: dict | None, b: dict | None) -> dict:
    """Per-source counters from parallel branches: numbers add up, nested dicts merge, lists concatenate."""
    out: dict = {}
    for src in (a or {}), (b or {}):
        for k, v in src.items():
            cur = out.get(k)
            if isinstance(v, dict):
                out[k] = merge_stats(cur if isinstance(cur, dict) else {}, v)
            elif isinstance(v, bool) or not isinstance(v, (int, float, list)):
                out[k] = v
            elif isinstance(v, list):
                out[k] = (cur if isinstance(cur, list) else []) + v
            else:
                out[k] = (cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else 0) + v
    return out


class SearchInput(TypedDict, total=False):
    run_id: str
    spec: dict          # SearchSpec; omitted -> built from the saved profile and settings
    trigger: str


class SearchOutput(TypedDict, total=False):
    run_id: str
    new_job_ids: list[str]
    stats: dict


class SearchState(TypedDict, total=False):
    run_id: str
    trigger: str
    spec: dict
    tasks: list[dict]                                   # one per source: api / careers / board
    leads: Annotated[list[dict], operator.add]          # links found on results pages (fan-in from collect)
    known: list[dict]                                   # leads that are already stored jobs -> sightings
    pending: dict                                       # source -> leads whose detail page must be read
    postings: Annotated[list[dict], operator.add]       # full postings (API leads + fetched pages)
    accepted: list[dict]
    rejected: list[dict]
    source_stats: Annotated[dict, merge_stats]
    new_job_ids: list[str]
    stats: dict
