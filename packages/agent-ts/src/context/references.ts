import type { MessageOut } from "../protocol/frames.js";

function cleanReferenceField(value: string): string {
  return value.replace(/[\r\n]/g, " ").trim().replace(/\s+/g, " ");
}

function xmlEscapeText(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function formatReferencedFilesHint(metadata: MessageOut["metadata"]): string {
  if (!metadata) return "";

  const references = metadata.references;
  if (!Array.isArray(references)) return "";

  const lines: string[] = [];
  const seen = new Set<string>();

  for (const ref of references) {
    if (!ref || typeof ref !== "object") continue;
    const record = ref as Record<string, unknown>;
    if (record.type !== "shared_file") continue;

    if (typeof record.name !== "string" || typeof record.storage_name !== "string") {
      continue;
    }

    const name = cleanReferenceField(record.name);
    const storageName = cleanReferenceField(record.storage_name);
    if (!name || !storageName) continue;
    if (storageName.includes("/") || storageName.includes("\\")) continue;

    const path = `memory/shared/${storageName}`;
    if (seen.has(path)) continue;
    seen.add(path);
    lines.push(`- ${xmlEscapeText(name)}: ${xmlEscapeText(path)}`);
  }

  if (lines.length === 0) return "";
  return `<referenced-files>\n${lines.join("\n")}\n</referenced-files>`;
}

export function withReferencedFilesHint(msg: MessageOut): string {
  const hint = formatReferencedFilesHint(msg.metadata);
  return hint ? `${hint}\n\n${msg.content}` : msg.content;
}
