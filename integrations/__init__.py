"""Integrations — adapter packages that implement the universal
ingest/egress/grounding surface from ``bicameral-protocol``.

Today only ``mcp_adapter`` ships here. Future plans add Linear, Notion,
Slack, dbt etc. as separate packages — each speaks the same Protocols
so the daemon never has to know about a specific source.
"""
