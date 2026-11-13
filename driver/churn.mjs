import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import WebSocket from 'ws';

const { values: args } = parseArgs({ options: Object.fromEntries(Object.entries({
  platform: undefined, endpoints: undefined, run: undefined, shard: '0', shards: '2',
  out: 'results', host: '127.0.0.1', port: '9500', seed: '20261106', 'lifetime-ms': '60000',
}).map(([name, value]) => [name, { type: 'string', ...(value === undefined ? {} : { default: value }) }])) });
if (!['rivet', 'celld'].includes(args.platform) || !args.endpoints || !args.run) throw Error('Missing platform, endpoints or run');
const lifetime = Number(args['lifetime-ms']);
if (lifetime !== 60000) throw Error('This scenario requires a 60-second lifetime');
const endpoints = args.endpoints.split(',');
fs.mkdirSync(args.out, { recursive: true });
const events = fs.createWriteStream(path.join(args.out, 'events.ndjson'));
const samples = fs.createWriteStream(path.join(args.out, 'samples.ndjson'));
const slots = new Map();
let target = 0, level = 0, connected = 0, writes = 0, errors = 0, connectErrors = 0;
let created = 0, retired = 0, opening = 0, retiring = 0, retrying = 0, connectRetries = 0, stopping = false, latencies = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, Math.ceil(Math.max(0, ms))));
function event(data) { if (!events.writableEnded) events.write(JSON.stringify({ timestamp_ms: Date.now(), platform: args.platform, shard: args.shard, level, ...data }) + '\n'); }
function random(seed) { let x = seed >>> 0; return () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return x / 4294967296; }; }
function endpoint(slot, key) {
  const base = new URL(endpoints[(slot * Number(args.shards) + Number(args.shard)) % endpoints.length]);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  if (args.platform === 'rivet') {
    base.pathname = '/gateway/loadActor/websocket';
    base.search = new URLSearchParams({ 'rvt-namespace': 'default', 'rvt-method': 'getOrCreate', 'rvt-pool': 'default', 'rvt-key': key, 'rvt-token': process.env.RIVET_TOKEN || '' });
  } else base.pathname = '/' + encodeURIComponent(key);
  return base.toString();
}
async function cycle(slot, generation) {
  const key = `${args.run}-${args.shard}-${slot.index}-${generation}`;
  const rng = random(Number(args.seed) + Number(args.shard) * 100000 + slot.index * 7919 + generation);
  const socket = new WebSocket(endpoint(slot.index, key), { handshakeTimeout: 30000 });
  slot.socket = socket;
  opening++;
  let held = false, established = false, isRetiring = false, born = 0, openedAt = 0, closed = false, pending = null;
  let readyResolve, readyReject, closeResolve;
  const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  const closure = new Promise(resolve => { closeResolve = resolve; });
  const connectTimer = setTimeout(() => { readyReject(Error('connect_timeout')); socket.terminate(); }, 45000);
  socket.on('error', () => { readyReject(Error('socket_error')); if (pending) pending.reject(Error('socket_error')); });
  socket.on('message', raw => {
    let reply;
    try { reply = JSON.parse(raw.toString()); } catch { errors++; event({ type: 'invalid_reply', actor: key }); return; }
    if (reply.type === 'connected' && !held) {
      clearTimeout(connectTimer); held = true; established = true; connected++; created++; opening--;
      born = performance.now(); openedAt = Date.now(); slot.started = true;
      event({ type: 'connected', actor: key, slot: slot.index, generation }); readyResolve();
    } else if (reply.type === 'write_result' && pending?.seq === reply.seq) {
      const p = pending; pending = null; clearTimeout(p.timer);
      const ms = performance.now() - p.start; writes++; latencies.push(ms);
      event({ type: 'write', actor: key, seq: reply.seq, latency_ms: ms }); p.resolve();
    } else if (reply.type === 'error') {
      errors++; event({ type: 'application_error', actor: key });
      if (pending) { clearTimeout(pending.timer); pending.reject(Error('application_error')); pending = null; }
    }
  });
  socket.on('close', code => {
    clearTimeout(connectTimer); closed = true;
    if (!established && code === 1012 && !stopping) {
      readyReject(Object.assign(Error('temporary_setup_refusal'), { retryable: true }));
      closeResolve(code); return;
    }
    if (held) { held = false; connected--; }
    if (!stopping && !isRetiring) { errors++; event({ type: 'unexpected_close', actor: key, code }); }
    if (pending) { clearTimeout(pending.timer); pending.reject(Error('closed')); pending = null; }
    readyReject(Error('closed_before_ready')); closeResolve(code);
  });
  try { await ready; } catch (error) { opening--; if (!error.retryable) connectErrors++; socket.terminate(); throw error; }
  let due = born + rng() * 10000, seq = 0;
  try {
    while (!stopping && !closed && due < born + lifetime) {
      await pause(due - performance.now());
      if (stopping || closed || performance.now() >= born + lifetime) break;
      const current = seq++;
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { pending = null; reject(Error('write_timeout')); }, 30000);
        pending = { seq: current, start: performance.now(), timer, resolve, reject };
        socket.send(JSON.stringify({ type: 'write', seq: current, payload: `${current}:`.padEnd(64, 'x').slice(0, 64) }));
      });
      due += 10000 * (.8 + rng() * .4);
    }
    while (!stopping && performance.now() < born + lifetime) await pause(born + lifetime - performance.now());
    if (stopping) return;
    if (closed) throw Error('closed_before_retirement');
    const lifetimeMs = performance.now() - born;
    if (lifetimeMs > lifetime + 1000) throw Error('lifetime_deadline_missed');
    isRetiring = true; retiring++;
    event({ type: 'retire', actor: key, slot: slot.index, generation, connected_ms: openedAt, lifetime_ms: lifetimeMs });
    socket.send(JSON.stringify({ type: 'retire' }));
    let timer;
    try {
      const code = await Promise.race([closure, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('retire_timeout')), 15000); })]);
      retired++;
      event({ type: 'retired', actor: key, slot: slot.index, generation, code, lifetime_ms: lifetimeMs, close_latency_ms: performance.now() - born - lifetimeMs });
    } finally { clearTimeout(timer); retiring--; }
  } finally { if (!closed) socket.close(); }
}
async function runSlot(slot, delay) {
  await pause(delay);
  try {
    for (let generation = 0; !stopping; generation++) {
      for (let attempt = 0; !stopping; attempt++) {
        try { await cycle(slot, generation); break; }
        catch (error) {
          if (!error.retryable) throw error;
          if (attempt >= 5) { connectErrors++; throw Error('connection_retries_exhausted'); }
          connectRetries++; retrying++;
          event({ type: 'connect_retry', actor: `${args.run}-${args.shard}-${slot.index}-${generation}`, slot: slot.index, generation, code: 1012, attempt: attempt + 1 });
          try { await pause(100 * 2 ** attempt); } finally { retrying--; }
        }
      }
    }
  } catch (error) {
    if (!stopping) { errors++; event({ type: 'cycle_error', slot: slot.index, reason: error.message }); }
  }
}
function grow(next) {
  const previous = slots.size, added = next - previous;
  for (let i = previous; i < next; i++) {
    const slot = { index: i, started: false, socket: null }; slots.set(i, slot);
    // Spread each new cohort across a minute. Every agent still gets its full minute alive.
    const offset = ((i - previous) * Number(args.shards) + Number(args.shard)) / (added * Number(args.shards)) * lifetime;
    runSlot(slot, offset);
  }
}
function status() { return { timestamp_ms: Date.now(), platform: args.platform, shard: args.shard, level, target, connected, writes, errors, connect_errors: connectErrors, connect_retries: connectRetries, created, retired, opening, retiring, retrying, ready_slots: [...slots.values()].filter(s => s.started).length }; }
const sampleTimer = setInterval(() => {
  latencies.sort((a,b) => a-b);
  samples.write(JSON.stringify({ ...status(), writes_in_sample: latencies.length, p99_ms: latencies.length ? latencies[Math.ceil(latencies.length * .99) - 1] : null }) + '\n'); latencies = [];
}, 1000);
const server = http.createServer(async (req, res) => {
  if (req.url === '/status') { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(status())); return; }
  if (req.url === '/metrics') { res.end(`bench_connected_actors ${connected}\nbench_target_actors ${target}\nbench_writes_total ${writes}\nbench_errors_total ${errors + connectErrors}\nbench_created_total ${created}\nbench_retired_total ${retired}\n`); return; }
  if (req.url === '/stage' && req.method === 'POST') {
    let body = ''; for await (const part of req) { body += part; if (body.length > 2048) { res.writeHead(413); res.end(); return; } }
    try {
      const next = JSON.parse(body);
      if (!Number.isInteger(next.target) || next.target < target || next.target > 25000 || !Number.isInteger(next.level)) throw Error('Invalid stage');
      target = next.target; level = next.level; event({ type: 'stage', target }); grow(target);
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(status()));
    } catch { res.writeHead(400); res.end('Invalid stage'); } return;
  }
  res.writeHead(404); res.end();
});
server.listen(Number(args.port), args.host);
function stop() {
  if (stopping) return; stopping = true; clearInterval(sampleTimer); event({ type: 'stop', ...status() });
  for (const slot of slots.values()) slot.socket?.close();
  server.close(); events.end(); samples.end(); setTimeout(() => process.exit(0), 1000);
}
process.on('SIGTERM', stop); process.on('SIGINT', stop);
