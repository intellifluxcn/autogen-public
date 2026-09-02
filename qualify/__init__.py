"""Qualify stage: the 4th pipeline stage.

For each downloaded dataset unit it runs Claude Code non-interactively against a
sandboxed copy and records a strict/loose/fail/error verdict in
``artifacts.qualification_status``.
"""

from qualify.team import QualifyTeam

__all__ = ["QualifyTeam"]
