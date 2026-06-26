"""Settings slice: two-tier persistent preference store for the AIDO Local Control Center.

Owns a descriptor registry, a generic SQLite-backed settings store, a pure resolver
applying ``project > general > default`` precedence with inheritance labelling, Pydantic
contracts for the HTTP surface, and a FastAPI router for GET/PUT/DELETE operations.
"""
