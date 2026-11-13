import { DurableObject } from "cloudflare:workers";

export class LoadActor extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS writes (slot INTEGER PRIMARY KEY, seq INTEGER NOT NULL, payload TEXT NOT NULL, updated_at INTEGER NOT NULL)");
    });
  }

  async fetch(request) {
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
      return new Response("WebSocket required", { status: 426 });
    }
    const [client, socket] = Object.values(new WebSocketPair());
    // A regular accepted socket keeps the actor resident for the measured hold.
    socket.accept();
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "retire") {
          socket.send(JSON.stringify({ type: "retiring" }));
          socket.close(1000, "One-minute workload complete");
          return;
        }
        if (message.type === "inspect") {
          const rows = this.ctx.storage.sql.exec("SELECT COUNT(*) AS rows, MAX(seq) AS last_seq FROM writes").toArray();
          socket.send(JSON.stringify({ type: "inspection", rows }));
          return;
        }
        if (message.type === "ping") {
          socket.send(JSON.stringify({ type: "pong" }));
          return;
        }
        if (message.type !== "write" || !Number.isSafeInteger(message.seq) || typeof message.payload !== "string" || message.payload.length !== 64) {
          throw new Error("Expected a numbered 64-byte write");
        }
        this.ctx.storage.sql.exec(
          "INSERT INTO writes (slot, seq, payload, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(slot) DO UPDATE SET seq=excluded.seq, payload=excluded.payload, updated_at=excluded.updated_at",
          message.seq % 16, message.seq, message.payload, Date.now(),
        );
        // celld's Durable Object output gate covers this acknowledgment.
        socket.send(JSON.stringify({ type: "write_result", seq: message.seq }));
      } catch (error) {
        socket.send(JSON.stringify({ type: "error", message: String(error) }));
      }
    });
    socket.send(JSON.stringify({ type: "connected", actorId: this.ctx.id.toString() }));
    return new Response(null, { status: 101, webSocket: client });
  }
}

export default {
  async fetch(request, env) {
    const id = new URL(request.url).pathname.slice(1);
    if (!id) return new Response("Actor name required", { status: 400 });
    return env.ACTORS.getByName(id).fetch(request);
  },
};
