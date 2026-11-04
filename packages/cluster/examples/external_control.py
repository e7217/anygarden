"""Send a message to an Anygarden room and print an agent's reply (#989).

Uses a personal API key (Personal settings > API keys) with the read and
write scope:

    export ANYGARDEN_URL=http://localhost:8001
    export ANYGARDEN_API_KEY=agu_...
    python examples/external_control.py rooms
    python examples/external_control.py ask <room_id> "Summarise today's work"

Only ``httpx`` is required. ``docs/external-control.md`` explains each call.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx


def make_client(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )


def list_rooms(client: httpx.Client) -> list[dict[str, Any]]:
    resp = client.get("/api/v1/rooms")
    resp.raise_for_status()
    return resp.json()


def agent_participants(client: httpx.Client, room_id: str) -> dict[str, dict[str, Any]]:
    """Map participant id → participant for the room's agents."""
    resp = client.get(f"/api/v1/rooms/{room_id}")
    resp.raise_for_status()
    return {p["id"]: p for p in resp.json()["participants"] if p["kind"] == "agent"}


def send_message(client: httpx.Client, room_id: str, content: str) -> dict[str, Any]:
    """Post as the key's owner. Mention an agent with ``<@user:{participant_id}>``;
    a room with a single agent needs no mention."""
    resp = client.post(f"/api/v1/rooms/{room_id}/messages", json={"content": content})
    resp.raise_for_status()
    return resp.json()


def wait_for_reply(
    client: httpx.Client,
    room_id: str,
    after_seq: int,
    *,
    timeout: float = 300,
    interval: float = 2,
) -> dict[str, Any] | None:
    """Poll the room until an agent posts after ``after_seq``; ``None`` on timeout."""
    agents = agent_participants(client, room_id)
    deadline = time.monotonic() + timeout
    while True:
        resp = client.get(
            f"/api/v1/rooms/{room_id}/messages", params={"since_seq": after_seq, "limit": 200}
        )
        resp.raise_for_status()
        for message in resp.json():
            after_seq = max(after_seq, message["seq"])
            if message["participant_id"] in agents:
                return message
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("rooms", help="list the rooms you can see")
    ask = sub.add_parser("ask", help="send a message and wait for an agent's reply")
    ask.add_argument("room_id")
    ask.add_argument("content")
    ask.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)

    base_url = os.environ.get("ANYGARDEN_URL", "http://localhost:8001")
    api_key = os.environ.get("ANYGARDEN_API_KEY")
    if not api_key:
        print("Set ANYGARDEN_API_KEY to a personal API key (agu_...).", file=sys.stderr)
        return 2

    with make_client(base_url, api_key) as client:
        if args.command == "rooms":
            for room in list_rooms(client):
                print(f"{room['id']}  {room['name']}")
            return 0
        sent = send_message(client, args.room_id, args.content)
        reply = wait_for_reply(client, args.room_id, sent["seq"], timeout=args.timeout)
        if reply is None:
            print("No agent replied before the timeout.", file=sys.stderr)
            return 1
        print(reply["content"])
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
