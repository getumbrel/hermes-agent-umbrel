const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const pty = require("/app/node_modules/node-pty");
const WebSocket = require("/app/node_modules/ws");

const PORT = parseInt(process.env.PORT || "18789");
// Anti-CSWSH token: browsers don't enforce same-origin on WebSocket upgrades,
// so without this any site could connect to /umbrel/api/terminal and hijack the PTY.
// APP_SEED (per-app secret from umbrelOS) is embedded in the served HTML.
const SETUP_TOKEN = process.env.APP_SEED || "";
const DATA_DIR = "/opt/data";
const DASHBOARD_HOST = "127.0.0.1";
const DASHBOARD_PORT = 9119;

let ptyProcess = null;

const TERMINAL_HTML = fs.readFileSync("/app/terminal.html", "utf8");
const LOGO = fs.readFileSync(path.join(__dirname, "logo.png"));

// Proxy HTTP requests to the dashboard container
function proxyToDashboard(req, res) {
  const options = {
    hostname: DASHBOARD_HOST,
    port: DASHBOARD_PORT,
    path: req.url,
    method: req.method,
    headers: req.headers,
  };

  const proxy = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });

  proxy.on("error", () => {
    res.writeHead(502, { "Content-Type": "text/html", "Cache-Control": "no-store" });
    res.end("<html><body><h2>Dashboard is starting...</h2><p>Please wait a moment and refresh.</p></body></html>");
  });

  req.pipe(proxy);
}

const server = http.createServer((req, res) => {
  // Serve terminal page at root
  if (req.url === "/" || req.url === "/index.html") {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(TERMINAL_HTML.replace("__SETUP_TOKEN__", SETUP_TOKEN));
    return;
  }

  // Serve static assets for terminal UI
  if (req.url === "/logo.png") {
    res.writeHead(200, { "Content-Type": "image/png", "Cache-Control": "public, max-age=86400" });
    res.end(LOGO);
    return;
  }

  // Everything else proxied to dashboard (behind umbrelOS auth)
  proxyToDashboard(req, res);
});

// WebSocket server for the interactive terminal
const wss = new WebSocket.Server({ noServer: true });

wss.on("connection", (ws) => {
  console.log("Terminal WebSocket connected");

  // One session at a time — kill any existing PTY (e.g. stale tab)
  if (ptyProcess) {
    try { ptyProcess.kill(); } catch (e) {}
    ptyProcess = null;
  }

  ptyProcess = pty.spawn("/app/start-hermes.sh", [], {
    name: "xterm-256color",
    cols: 80,
    rows: 24,
    cwd: DATA_DIR,
    env: {
      ...process.env,
      TERM: "xterm-256color",
      HOME: DATA_DIR,
    },
  });

  ptyProcess.onData((data) => {
    try {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "output", data }));
      }
    } catch (e) {
      console.error("Error sending PTY output:", e.message);
    }
  });

  ptyProcess.onExit(({ exitCode }) => {
    console.log(`PTY exited with code ${exitCode}`);
    try {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "exit", code: exitCode }));
      }
    } catch (e) {
      console.error("Error sending exit message:", e.message);
    }
    ptyProcess = null;
  });

  ws.on("message", (msg) => {
    try {
      const parsed = JSON.parse(msg);
      if (parsed.type === "input" && ptyProcess) {
        ptyProcess.write(parsed.data);
      } else if (parsed.type === "resize" && ptyProcess) {
        ptyProcess.resize(parsed.cols, parsed.rows);
      }
    } catch (e) {
      console.error("Error processing terminal input:", e.message);
    }
  });

  ws.on("close", () => {
    console.log("Terminal WebSocket closed");
    if (ptyProcess) {
      try { ptyProcess.kill(); } catch (e) {}
      ptyProcess = null;
    }
  });
});

server.on("upgrade", (req, socket, head) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  if (url.pathname === "/umbrel/api/terminal") {
    // Validate anti-CSWSH token
    const provided = url.searchParams.get("token") || "";
    if (!SETUP_TOKEN || provided.length !== SETUP_TOKEN.length ||
        !crypto.timingSafeEqual(Buffer.from(provided), Buffer.from(SETUP_TOKEN))) {
      socket.write("HTTP/1.1 403 Forbidden\r\n\r\n");
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      wss.emit("connection", ws, req);
    });
    return;
  }

  socket.end("HTTP/1.1 404 Not Found\r\n\r\n");
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Hermes terminal server listening on port ${PORT}`);
});

// Graceful shutdown
process.on("SIGTERM", () => {
  console.log("Received SIGTERM, shutting down...");
  if (ptyProcess) {
    try { ptyProcess.kill(); } catch (e) {}
  }
  server.close();
});
