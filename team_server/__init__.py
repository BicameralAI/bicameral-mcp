"""Bicameral team-server — self-managing customer-self-hosted backend for
multi-dev decision-continuity at organizational scale.

Per `docs/CONCEPT.md` literal-keyword parsing (`docs/SHADOW_GENOME.md`
Failure Entry #6 addendum): "no managed backend" forbids vendor SaaS and
human-ops-tax architectures, NOT self-managing customer-deployable
backends. This package is the self-managing backend.
"""

from team_server.app import create_app

__all__ = ["create_app"]
