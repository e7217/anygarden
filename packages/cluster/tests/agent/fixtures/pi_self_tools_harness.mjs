// Exercise the shipped extension without creating a model session.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const input = JSON.parse(readFileSync(0, 'utf8'));
const { default: register } = await import(pathToFileURL(process.argv[2]).href);
const tools = new Map();
register({ registerTool: tool => tools.set(tool.name, tool) });
const results = [];
for (const call of input.calls ?? []) {
  const controller = new AbortController();
  if (call.mock) {
    globalThis.fetch = async (_url, options) => {
      if (call.mock === 'cancel' || call.mock === 'timeout') {
        if (call.mock === 'cancel') queueMicrotask(() => controller.abort());
        return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
      }
      if (call.mock === 'network') throw new Error(`must not echo ${process.env.ANYGARDEN_AGENT_TOKEN}`);
      if (call.mock === 'oversized') return new Response('x'.repeat(300000));
      if (call.mock === 'invalid') return new Response('{}');
      if (call.mock === 'http') return new Response('{}', { status: 403 });
      const result = call.mock === 'rpc'
        ? { error: { message: `server rejected ${process.env.ANYGARDEN_AGENT_TOKEN}` } }
        : { result: { isError: true, content: [{ type: 'text', text: `forbidden ${process.env.ANYGARDEN_AGENT_TOKEN}` }] } };
      return new Response(JSON.stringify({ jsonrpc: '2.0', id: 'bridge-check', ...result }));
    };
    if (call.mock === 'timeout') {
      const timeout = AbortSignal.timeout.bind(AbortSignal);
      AbortSignal.timeout = () => timeout(5);
    }
  }
  // Keep the mocked timeout's unref'ed timer alive without a model process.
  const keepalive = setInterval(() => {}, 1000);
  try {
    results.push({ ok: true, value: await tools.get(call.name).execute('bridge-check', call.args ?? {}, controller.signal) });
  } catch (error) {
    results.push({ ok: false, error: error.message });
  } finally { clearInterval(keepalive); }
}
console.log(JSON.stringify({ names: [...tools.keys()].sort(), results }));
