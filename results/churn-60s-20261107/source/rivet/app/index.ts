import { actor, setup } from "rivetkit";
import { db } from "rivetkit/db";

const loadActor = actor({
  options: { canHibernateWebSocket: false, sleepGracePeriod: 5000 },
  db: db({
    onMigrate: async (database) => {
      await database.execute("CREATE TABLE IF NOT EXISTS writes (slot INTEGER PRIMARY KEY, seq INTEGER NOT NULL, payload TEXT NOT NULL, updated_at INTEGER NOT NULL)");
    },
  }),
  onWebSocket: (c, socket) => {
    socket.send(JSON.stringify({ type: "connected", actorId: c.actorId }));
    socket.addEventListener("message", (event) => {
      const handle = async () => {
        const request = JSON.parse(String(event.data));
        if (request.type === "retire") {
          socket.send(JSON.stringify({ type: "retiring" }));
          c.sleep();
          return;
        }
        if (request.type === "inspect") {
          const rows = await c.db.execute("SELECT COUNT(*) AS rows, MAX(seq) AS last_seq FROM writes");
          socket.send(JSON.stringify({ type: "inspection", rows }));
          return;
        }
        if (request.type === "ping") {
          socket.send(JSON.stringify({ type: "pong" }));
          return;
        }
        if (request.type !== "write" || !Number.isSafeInteger(request.seq) || typeof request.payload !== "string" || request.payload.length !== 64) {
          throw new Error("Expected a numbered 64-byte write");
        }
        await c.db.execute(
          "INSERT INTO writes (slot, seq, payload, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(slot) DO UPDATE SET seq=excluded.seq, payload=excluded.payload, updated_at=excluded.updated_at",
          request.seq % 16, request.seq, request.payload, Date.now(),
        );
        socket.send(JSON.stringify({ type: "write_result", seq: request.seq }));
      };
      handle().catch((error) => socket.send(JSON.stringify({ type: "error", message: String(error) })));
    });
  },
});

const registry = setup({ use: { loadActor } });
registry.start();
