import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import WebSocket from "ws";

const { values } = parseArgs({ options: {
  platform: { type: "string" }, endpoints: { type: "string" }, run: { type: "string" },
  shard: { type: "string", default: "0" }, shards: { type: "string", default: "2" }, out: { type: "string", default: "results" },
  host: { type: "string", default: "127.0.0.1" }, port: { type: "string", default: "9500" },
  seed: { type: "string", default: "20261106" },
} });
if (!["rivet", "celld"].includes(values.platform) || !values.endpoints || !values.run) {
  throw new Error("Supply --platform rivet|celld --endpoints URL,URL --run NAME");
}
const endpoints = values.endpoints.split(",");
fs.mkdirSync(values.out, { recursive: true });
const events = fs.createWriteStream(path.join(values.out, "events.ndjson"));
const samples = fs.createWriteStream(path.join(values.out, "samples.ndjson"));
const actors = new Map();
let target = 0, connected = 0, writes = 0, errors = 0, connectErrors = 0, level = 0;
let adding = false, stopping = false, latencies = [];
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function event(data) { events.write(JSON.stringify({ timestamp_ms: Date.now(), platform: values.platform, shard: values.shard, level, ...data }) + "\n"); }
function rng(seed) {
  let state = seed >>> 0;
  return () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 4294967296; };
}
function endpoint(index, key) {
  const base = new URL(endpoints[(index * Number(values.shards) + Number(values.shard)) % endpoints.length]);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  if (values.platform === "rivet") {
    base.pathname = "/gateway/loadActor/websocket";
    base.search = new URLSearchParams({ "rvt-namespace": "default", "rvt-method": "getOrCreate", "rvt-pool": "default", "rvt-key": key, "rvt-token": process.env.RIVET_TOKEN || "" });
  } else {
    base.pathname = `/${encodeURIComponent(key)}`;
  }
  return base.toString();
}

async function openActor(index) {
  const key = `${values.run}-${values.shard}-${index}`;
  const random = rng(Number(values.seed) + Number(values.shard) * 100000 + index);
  const actor = { index, key, held: false, stopped: false, seq: 0, pending: null, socket: null };
  actors.set(index, actor);
  await new Promise((resolve, reject) => {
    const socket = new WebSocket(endpoint(index, key), { handshakeTimeout: 30000 });
    actor.socket = socket;
    const timeout = setTimeout(() => { socket.terminate(); reject(new Error("connect_timeout")); }, 45000);
    socket.on("error", () => { clearTimeout(timeout); reject(new Error("socket_error")); });
    socket.on("message", (raw) => {
      let reply;
      try { reply = JSON.parse(raw.toString()); } catch { errors++; event({ type: "invalid_reply", actor: index }); return; }
      if (reply.type === "connected" && !actor.held) {
        actor.held = true; connected++; clearTimeout(timeout);
        event({ type: "connected", actor: index }); resolve();
      } else if (reply.type === "write_result" && actor.pending?.seq === reply.seq) {
        const pending = actor.pending; actor.pending = null;
        clearTimeout(pending.timeout);
        const ms = performance.now() - pending.start;
        writes++; latencies.push(ms);
        event({ type: "write", actor: index, seq: reply.seq, latency_ms: ms });
        pending.resolve();
      } else if (reply.type === "error") {
        errors++; event({ type: "application_error", actor: index });
        if (actor.pending) {
          clearTimeout(actor.pending.timeout); actor.pending.reject(new Error("application_error")); actor.pending = null;
        }
      }
    });
    socket.on("close", () => {
      clearTimeout(timeout);
      if (actor.held) { actor.held = false; connected--; }
      if (!stopping && !actor.stopped) { errors++; event({ type: "unexpected_close", actor: index }); }
      actor.stopped = true;
      if (actor.pending) { clearTimeout(actor.pending.timeout); actor.pending.reject(new Error("closed")); actor.pending = null; }
      reject(new Error("closed_before_ready"));
    });
  });
  let due = performance.now() + random() * 10000;
  while (!actor.stopped && !stopping) {
    await delay(Math.max(0, due - performance.now()));
    if (actor.stopped || stopping) break;
    const seq = actor.seq++;
    const payload = `${seq}:`.padEnd(64, "x").slice(0, 64);
    try {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => { actor.pending = null; errors++; event({ type: "write_timeout", actor: index }); reject(new Error("write_timeout")); }, 30000);
        actor.pending = { seq, resolve, reject, timeout, start: performance.now() };
        actor.socket.send(JSON.stringify({ type: "write", seq, payload }));
      });
    } catch {
      actor.stopped = true; actor.socket.close(); break;
    }
    due += 10000 * (0.8 + random() * 0.4);
  }
}

async function grow() {
  if (adding) return;
  adding = true;
  try {
    while (actors.size < target && !stopping) {
      const count = Math.min(16, target - actors.size);
      for (let n = 0; n < count; n++) {
        const index = actors.size;
        openActor(index).catch(() => { connectErrors++; event({ type: "connect_error", actor: index }); });
      }
      await delay(200);
    }
  } finally { adding = false; }
}
function status() {
  return { timestamp_ms: Date.now(), platform: values.platform, shard: values.shard, level, target, connected, writes, errors, connect_errors: connectErrors };
}
const sampleTimer = setInterval(() => {
  latencies.sort((a, b) => a - b);
  const quantile = (p) => latencies.length ? latencies[Math.max(0, Math.ceil(latencies.length * p) - 1)] : null;
  samples.write(JSON.stringify({ ...status(), writes_in_sample: latencies.length, p50_ms: quantile(.5), p99_ms: quantile(.99) }) + "\n");
  latencies = [];
}, 1000);
const server = http.createServer(async (req, res) => {
  if (req.url === "/status") { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(status())); return; }
  if (req.url === "/metrics") {
    res.setHeader("Content-Type", "text/plain");
    res.end(`bench_connected_actors ${connected}\nbench_target_actors ${target}\nbench_writes_total ${writes}\nbench_errors_total ${errors + connectErrors}\n`); return;
  }
  if (req.url === "/stage" && req.method === "POST") {
    let body = "";
    for await (const part of req) { body += part; if (body.length > 2048) { res.writeHead(413); res.end(); return; } }
    try {
      const next = JSON.parse(body);
      if (!Number.isInteger(next.target) || next.target < target || next.target > 10000 || !Number.isInteger(next.level)) throw new Error("invalid_target");
      target = next.target; level = next.level; event({ type: "stage", target });
      grow(); res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(status()));
    } catch { res.writeHead(400); res.end("Invalid stage"); }
    return;
  }
  res.writeHead(404); res.end();
});
server.listen(Number(values.port), values.host, () => console.log(JSON.stringify({ type: "ready", platform: values.platform, shard: values.shard })));
async function stop() {
  if (stopping) return;
  stopping = true; clearInterval(sampleTimer);
  for (const actor of actors.values()) { actor.stopped = true; actor.socket?.close(); }
  event({ type: "stop", ...status() });
  server.close(); events.end(); samples.end();
  setTimeout(() => process.exit(0), 1000);
}
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
