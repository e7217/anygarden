"""Pure helper that assembles the memory / ephemeral block injected
into each engine adapter's ``system_prompt`` (#237).

Design decisions (see plan §3.2 decisions 2 & 3):
- File-convention driven cross-engine memory → all engines get the same
  markdown block regardless of SDK-specific tool support.
- Trust-model ephemeral → system-prompt directive, no FS hard guard.

Keeping this as a pure function (no I/O, no engine imports) makes it
trivial to unit-test and lets adapters compose it into whatever prompt
shape they emit.
"""

from __future__ import annotations


_EMPTY_MEMORY_PLACEHOLDER = "(아직 기억이 비어 있습니다. 필요한 내용을 자유롭게 작성하세요.)"

_MEMORY_POLICY = (
    "장기 기억은 에이전트 작업 디렉터리의 `memory/notes.md` 파일에 append 하세요.\n"
    "기존 섹션을 재활용해도 됩니다. 너무 길어지면 직접 요약/정리(prune)하세요.\n"
    "세션이 시작될 때 위 `<memory>` 블록에 현재 파일 내용이 주입됩니다."
)

_EPHEMERAL_DIRECTIVE = (
    "이 세션은 **임시(ephemeral)** 입니다.\n"
    "`memory/notes.md` 파일에 절대 기록하지 마세요. "
    "사용자는 이 대화가 장기 기억에 남지 않기를 원합니다."
)


def compose_memory_block(memory_md: str | None, ephemeral: bool) -> str:
    """Return the markdown block to append to the engine's system prompt.

    The block is always present (even when memory is empty) so the
    agent's instructions about how to use the memory file are shipped
    uniformly. Structure::

        <memory>
        ...current notes.md content or placeholder...
        </memory>
        <memory-policy>
        ...how to use the memory file...
        </memory-policy>
        [<ephemeral-session/> ...]

    Args:
        memory_md: DB snapshot of the agent's ``memory/notes.md`` file.
            ``None`` or empty renders a human-friendly placeholder
            instead of a blank ``<memory>`` block (which some engines
            might collapse away visually).
        ephemeral: When True an ``<ephemeral-session/>`` section is
            appended telling the agent not to write to the memory file
            this session. The cluster sets this from the room-level
            ``ephemeral`` flag on the WS welcome frame.

    Returns:
        Markdown block, ready to concatenate to a system_prompt. Always
        ends with a trailing newline so adapters can prepend/append
        without extra bookkeeping.
    """
    body = memory_md.strip() if memory_md else ""
    if not body:
        body = _EMPTY_MEMORY_PLACEHOLDER

    parts = [
        "<memory>",
        body,
        "</memory>",
        "<memory-policy>",
        _MEMORY_POLICY,
        "</memory-policy>",
    ]
    if ephemeral:
        parts.extend(
            [
                "<ephemeral-session>",
                _EPHEMERAL_DIRECTIVE,
                "</ephemeral-session>",
            ]
        )
    return "\n".join(parts) + "\n"
