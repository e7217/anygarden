# doorae-machine

Machine daemon for Doorae agent orchestration. Connects to the doorae-server via WebSocket and manages agent subprocesses on the local machine.

## Installation

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Register this machine with a Doorae server
doorae-machine register --server wss://doorae.example.com --name my-machine

# Run the daemon
doorae-machine run

# Check status
doorae-machine status

# Install as systemd user service
doorae-machine install-systemd-unit
```
