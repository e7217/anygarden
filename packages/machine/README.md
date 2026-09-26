# anygarden-machine

Machine daemon for Anygarden agent orchestration. Connects to the Anygarden
server via WebSocket and manages agent subprocesses on the local machine.

## Installation

```bash
uv tool install "anygarden[machine]"
```

## Usage

```bash
# Register this machine with an Anygarden server (prompts for account credentials)
anygarden machine register --server https://anygarden.example.com --name my-machine

# Run the daemon
anygarden machine run

# Check status
anygarden machine status

# Install as systemd user service
anygarden machine install-systemd-unit
```
