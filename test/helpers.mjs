import { createServer } from 'node:http';
import { createServer as createNetServer } from 'node:net';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

export const serverPath = fileURLToPath(new URL('../server.mjs', import.meta.url));
export const tool = {
  name: 'call_endpoint', description: 'Live description; prices belong to the service.',
  inputSchema: { type: 'object', properties: { platform: { type: 'string' }, idempotency_key: { type: 'string' } } },
  annotations: { readOnlyHint: true, openWorldHint: true },
};
export function normalReply(message) {
  if (message.method === 'initialize') return {
    protocolVersion: '2025-11-25', capabilities: { tools: {} },
    serverInfo: { name: 'monocrawl', version: '9.9.9' }, instructions: 'Current hosted instructions.',
  };
  if (message.method === 'tools/list') return { tools: [tool] };
  if (message.method === 'ping') return {};
  return { content: [{ type: 'text', text: 'fixture' }], structuredContent: { success: true, credits_used: 3 }, isError: false };
}
export async function fixture(t, handler = () => undefined) {
  const calls = [];
  const server = createServer(async (req, res) => {
    try {
      let text = ''; for await (const chunk of req) text += chunk;
      const message = JSON.parse(text);
      calls.push({ message, headers: req.headers });
      const custom = await handler(message, req, res);
      if (res.destroyed || res.writableEnded) return;
      if (custom?.drop) { req.socket.destroy(); return; }
      res.writeHead(custom?.status || (message.id === undefined ? 202 : 200), { 'content-type': 'application/json', ...custom?.headers });
      if (message.id === undefined) { res.end(); return; }
      res.end(custom?.raw ?? JSON.stringify(custom?.body ?? { jsonrpc: '2.0', id: message.id, result: normalReply(message) }));
    } catch { res.destroy(); }
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); });
  return { calls, url: 'http://127.0.0.1:' + server.address().port + '/mcp' };
}

// Minimal HTTP/1.1 server that holds at most `maxConnections` sockets at once:
// further TCP connections are accepted but left unread, so requests on them
// never reach the request log until a held socket closes. An admitted request
// only gets its response once release() is called, keeping the socket occupied
// for the lifetime of the hold. This models a hard upstream connection ceiling
// that the bridge cannot bypass with pipelining or socket reuse.
export async function connectionLimitedFixture(t, maxConnections) {
  const calls = [];
  const open = new Set();
  const queue = [];
  const releaseWaiters = [];
  const server = createNetServer(socket => {
    if (open.size >= maxConnections) { queue.push(socket); return; }
    admit(socket);
  });
  function admit(socket) {
    open.add(socket);
    let buffer = '';
    const process = () => {
      const end = buffer.indexOf('\r\n\r\n');
      if (end === -1) return;
      const head = buffer.slice(0, end);
      const length = Number(head.split('\r\n').find(line => line.toLowerCase().startsWith('content-length:'))?.split(':')[1] || 0);
      if (buffer.length < end + 4 + length) return;
      const body = buffer.slice(end + 4, end + 4 + length);
      buffer = buffer.slice(end + 4 + length);
      let message;
      try { message = JSON.parse(body); } catch { socket.destroy(); return; }
      calls.push({ message, headers: {} });
      const payload = message.id === undefined ? '' : JSON.stringify({ jsonrpc: '2.0', id: message.id, result: normalReply(message) });
      const write = () => {
        socket.write(
          'HTTP/1.1 ' + (message.id === undefined ? '202' : '200') + ' Accepted\r\n' +
          'content-type: application/json\r\ncontent-length: ' + Buffer.byteLength(payload) + '\r\nconnection: keep-alive\r\n\r\n' + payload);
        setImmediate(process);
      };
      if (message.id === undefined) write();
      else releaseWaiters.push(write);
    };
    socket.on('data', chunk => { buffer += chunk.toString('utf8'); process(); });
    socket.on('close', () => {
      open.delete(socket);
      const next = queue.shift();
      if (next && !next.destroyed) admit(next);
    });
    socket.on('error', () => { socket.destroy(); });
  }
  const releaseAll = () => { while (releaseWaiters.length) releaseWaiters.shift()(); };
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { for (const socket of open) socket.destroy(); for (const socket of queue) socket.destroy();
    return new Promise(resolve => server.close(resolve)); });
  return { calls, url: 'http://127.0.0.1:' + server.address().port + '/mcp', releaseAll };
}
export function transportFor(url, { entry = serverPath, env = {}, command = process.execPath, args = [entry], cwd } = {}) {
  const transport = new StdioClientTransport({ command, args, cwd, env: {
    MONOCRAWL_MCP_URL: url, ...env,
  }, stderr: 'pipe' });
  let stderr = '';
  transport.stderr.on('data', chunk => { stderr += chunk; });
  return { transport, stderr: () => stderr };
}
export async function sdkClient(t, url, options) {
  const wire = transportFor(url, options);
  const client = new Client({ name: 'monocrawl-package-test', version: '1.0.0' });
  t.after(() => client.close());
  await client.connect(wire.transport);
  return { client, ...wire };
}
export async function rawClient(t, url, options) {
  const wire = transportFor(url, options);
  const pending = new Map();
  const received = [];
  wire.transport.onmessage = message => { received.push(message); pending.get(message.id)?.(message); pending.delete(message.id); };
  wire.transport.onerror = () => {};
  t.after(() => wire.transport.close());
  await wire.transport.start();
  return { ...wire, received, request: message => new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(message.id); reject(new Error('Test response timeout')); }, 5000);
    pending.set(message.id, response => { clearTimeout(timer); resolve(response); });
    wire.transport.send(message).catch(reject);
  }) };
}
