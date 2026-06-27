"""Team Activity slice: read-only aggregation of live agent work for the shell UI.

Joins agent runs with their assignment, handoff, review, model/tool calls and produced
artifact into per-run activity entries so the web shell can answer who is working, in
which role and runtime, on what assignment, why they are blocked, what artifact they
delivered, who reviewed it, for how long and at what cost. This package only reads
platform state; it never mutates it.

@author Rodrigo Mason
"""
