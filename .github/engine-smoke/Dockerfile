FROM node:24.14.0-bookworm-slim

# The canary runner invokes this file with `python` and `codex`.  Keep the
# image purpose-built: it contains no repository checkout, user config, or
# provider credential.  Those are supplied only by the protected workflow.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates python3 python-is-python3 \
    && npm install --global @openai/codex@0.146.0 \
    && rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /work
