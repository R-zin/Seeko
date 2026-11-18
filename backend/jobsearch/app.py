"""Graph export for the LangGraph CLI / Studio (`langgraph dev` in backend/, see langgraph.json).

The platform supplies its own checkpointer; job data still goes to the app database (DATABASE_URL).
Invoke with `{}` to search with the saved profile, or `{"spec": {...SearchSpec fields...}}`.
"""
from __future__ import annotations
import db
from .graph import build_graph

db.init_db()
graph = build_graph().compile()
