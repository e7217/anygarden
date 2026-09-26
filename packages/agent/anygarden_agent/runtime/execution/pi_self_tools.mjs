/** Local-room-only bridge. Never loaded by the isolated federation Pi runtime. */
import { readFileSync } from 'node:fs';
import { isAbsolute } from 'node:path';

const names = new Set(['create_skill', 'update_skill', 'list_my_skills', 'delete_my_skill', 'claim_task', 'mark_task_status', 'create_task', 'add_task_blocker', 'clear_task_blocker']);
const limit = 262144;

export default function registerAnygardenTools(pi) {
  const token = process.env.ANYGARDEN_AGENT_TOKEN;
  const path = process.env.AG_PI_SELF_TOOLS_CONFIG;
  if (!token?.startsWith('agt_') || !path || !isAbsolute(path)) {
    throw new Error('Anygarden self tools are not configured for this agent.');
  }
  const file = readFileSync(path);
  if (file.length > limit) throw new Error('Anygarden self tools configuration is too large.');
  const config = JSON.parse(file.toString('utf8'));
  const endpoint = new URL(config.endpoint);
  if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    throw new Error('Anygarden self tools require the configured chat server.');
  }
  if (!Array.isArray(config.tools) || config.tools.length !== names.size || new Set(config.tools.map(tool => tool.name)).size !== names.size) {
    throw new Error('Anygarden self tools configuration is incomplete.');
  }
  const redact = value => String(value).replaceAll(token, '[redacted]').slice(0, 500);
  for (const tool of config.tools) {
    if (!names.has(tool.name) || typeof tool.description !== 'string' || tool.inputSchema?.type !== 'object') {
      throw new Error('Anygarden self tools configuration contains an invalid tool.');
    }
    pi.registerTool({
      name: tool.name,
      label: `Anygarden: ${tool.name}`,
      description: tool.description,
      promptSnippet: tool.description,
      parameters: tool.inputSchema,
      async execute(toolCallId, params, signal) {
        const timeout = AbortSignal.timeout(15000);
        const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
        let response;
        let payload;
        try {
          response = await fetch(endpoint, {
            method: 'POST', redirect: 'error', signal: combined,
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ jsonrpc: '2.0', id: toolCallId, method: 'tools/call', params: { name: tool.name, arguments: params } }),
          });
          if (!response.ok) {
            await response.body?.cancel();
            throw new Error(`HTTP ${response.status}`);
          }
          const reader = response.body?.getReader();
          const chunks = [];
          let length = 0;
          if (!reader) throw new Error('Empty response');
          try {
            for (;;) {
              const { value, done } = await reader.read();
              if (done) break;
              length += value.length;
              if (length > limit) throw new Error('Response too large');
              chunks.push(value);
            }
          } finally { await reader.cancel(); }
          payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        } catch {
          if (signal?.aborted) throw new Error('Anygarden tool request cancelled.');
          if (timeout.aborted) throw new Error('Anygarden tool request timed out.');
          if (response && !response.ok) throw new Error(`Anygarden ${tool.name} failed (HTTP ${response.status}). Check this agent\'s access.`);
          throw new Error('Anygarden tool request failed. Check the server connection and retry.');
        }
        if (!payload || payload.jsonrpc !== '2.0' || payload.id !== toolCallId) throw new Error('Anygarden returned an invalid tool response.');
        if (payload.error) throw new Error(redact(payload.error.message || 'Anygarden rejected the tool request.'));
        const result = payload.result;
        if (!result || !Array.isArray(result.content)) throw new Error('Anygarden returned an invalid tool result.');
        const content = result.content.filter(item => item?.type === 'text' && typeof item.text === 'string').map(item => ({ type: 'text', text: item.text }));
        if (result.isError) throw new Error(redact(content.map(item => item.text).join('\n') || 'Anygarden could not complete this operation.'));
        return { content, details: result.structuredContent ?? {} };
      },
    });
  }
}
