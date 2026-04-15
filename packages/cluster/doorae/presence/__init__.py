"""Participant presence unified service (#54).

Single source of truth for "is this participant currently reachable?".
See ``doorae.presence.service`` for the ``PresenceService`` class and
``ParticipantStatus`` dataclass.
"""

from doorae.presence.service import ParticipantStatus, PresenceService

__all__ = ["ParticipantStatus", "PresenceService"]
