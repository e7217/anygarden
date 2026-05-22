"""Participant presence unified service (#54).

Single source of truth for "is this participant currently reachable?".
See ``anygarden.presence.service`` for the ``PresenceService`` class and
``ParticipantStatus`` dataclass.
"""

from anygarden.presence.service import ParticipantStatus, PresenceService

__all__ = ["ParticipantStatus", "PresenceService"]
