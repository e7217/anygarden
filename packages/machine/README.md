# drmachine

Machine daemon for Anygarden agent orchestration. Connects to the drhub server via WebSocket and manages agent subprocesses on the local machine.

## Installation

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Register this machine with a Anygarden server
anygarden-machine register --server wss://anygarden.example.com --name my-machine

# Run the daemon
anygarden-machine run

# Check status
anygarden-machine status

# Install as systemd user service
anygarden-machine install-systemd-unit
```
