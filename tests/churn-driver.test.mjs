import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { setTimeout as pause } from 'node:timers/promises';
import { WebSocketServer } from 'ws';

async function fixture(t, alwaysRefuse = false) {
  const out = await fs.mkdtemp(path.join(os.tmpdir(), 'churn-retry-'));
  const server = new WebSocketServer({ port: 0, host: '127.0.0.1' });
  await once(server, 'listening');
  let attempts = 0;
  server.on('connection', socket => {
    if (++attempts === 1 || alwaysRefuse) { socket.close(1012); return; }
    socket.send(JSON.stringify({ type: 'connected' }));
    socket.on('message', raw => {
      const message = JSON.parse(raw);
      if (message.type === 'write') socket.send(JSON.stringify({ type: 'write_result', seq: message.seq }));
      if (message.type === 'retire') socket.close(1000);
    });
  });
  const reservation = net.createServer();
  reservation.listen(0, '127.0.0.1'); await once(reservation, 'listening');
  const port = reservation.address().port;
  await new Promise(resolve => reservation.close(resolve));
  const child = spawn(process.execPath, ['driver/churn.mjs', '--platform', 'celld', '--endpoints', `http://127.0.0.1:${server.address().port}`, '--run', 'retry-test', '--shard', '0', '--shards', '1', '--out', out, '--host', '0.0.0.0', '--port', String(port)], { stdio: 'ignore' });
  t.after(async () => {
    if (child.exitCode === null) { child.kill('SIGTERM'); await once(child, 'exit'); }
    for (const socket of server.clients) socket.terminate();
    await new Promise(resolve => server.close(resolve));
    await fs.rm(out, { recursive: true, force: true });
  });
  const status = async () => (await fetch(`http://127.0.0.1:${port}/status`)).json();
  for (let i = 0; ; i++) {
    try { await status(); break; } catch { if (i > 100) throw Error('Driver did not start'); await pause(25); }
  }
  const response = await fetch(`http://127.0.0.1:${port}/stage`, { method: 'POST', body: JSON.stringify({ target: 1, level: 1 }) });
  assert.equal(response.status, 200);
  return { status, out, attempts: () => attempts };
}

test('a counted setup retry preserves the complete one-minute lifetime', { timeout: 75000 }, async t => {
  const f = await fixture(t);
  let state;
  do { await pause(250); state = await f.status(); } while (!state.retired && !state.errors);
  assert.equal(state.errors, 0);
  assert.equal(state.connect_errors, 0);
  assert.equal(state.connect_retries, 1);
  assert.equal(state.retired, 1);
  const events = (await fs.readFile(path.join(f.out, 'events.ndjson'), 'utf8')).trim().split('\n').map(JSON.parse);
  const retired = events.find(e => e.type === 'retired');
  assert.ok(retired.lifetime_ms >= 60000 && retired.lifetime_ms < 61000);
  assert.equal(events.filter(e => e.type === 'connect_retry').length, 1);
  assert.ok(events.filter(e => e.type === 'write').length >= 4);
});

test('repeated setup refusals stop after the bounded retry budget', { timeout: 10000 }, async t => {
  const f = await fixture(t, true);
  let state;
  do { await pause(100); state = await f.status(); } while (!state.errors);
  assert.equal(f.attempts(), 6);
  assert.equal(state.connect_retries, 5);
  assert.equal(state.connect_errors, 1);
  assert.equal(state.created, 0);
  assert.equal(state.retired, 0);
});
