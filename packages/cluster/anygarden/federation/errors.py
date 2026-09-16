"""Closed error vocabulary. Exception strings never include submitted material."""


class PeerError(Exception):
    def __init__(self, code: str, status: int = 403):
        self.code = code
        self.status = status
        super().__init__(code)
