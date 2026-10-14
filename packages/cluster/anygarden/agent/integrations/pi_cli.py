"""Pi normal-room registration through the shared execution manager."""
from .room_execution import RoomExecutionAdapter


class PiCliAdapter(RoomExecutionAdapter):
    def __init__(self, **kwargs):
        super().__init__(engine="pi-cli", **kwargs)
