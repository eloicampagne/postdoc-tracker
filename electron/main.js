const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

// ── Config ────────────────────────────────────────────────────────────────────
const PORT = 3742;
const URL  = `http://localhost:${PORT}`;

// ── Spawn Python server ───────────────────────────────────────────────────────
let pyProcess = null;
let pyStarted = false;

function startPython() {
  if (pyStarted) return;
  pyStarted = true;

  const projectRoot = path.resolve(__dirname, "..");
  pyProcess = spawn("python3", ["-m", "postdoc_tracker", "--no-browser", "--http", "--port", String(PORT)], {
    cwd: projectRoot,
    env: { ...process.env },
    stdio: ["ignore", "pipe", "pipe"],
  });

  pyProcess.stdout.on("data", (d) => process.stdout.write(d));
  pyProcess.stderr.on("data", (d) => process.stderr.write(d));

  pyProcess.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      console.error(`Python server exited with code ${code}`);
    }
  });
}

// ── Poll until server is ready ─────────────────────────────────────────────
function waitForServer(url, timeout = 15000, interval = 200) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    function attempt() {
      http.get(url, (res) => {
        res.resume();
        resolve();
      }).on("error", () => {
        if (Date.now() - start > timeout) {
          reject(new Error("Python server did not start in time"));
        } else {
          setTimeout(attempt, interval);
        }
      });
    }
    attempt();
  });
}

// ── Create window ─────────────────────────────────────────────────────────────
let win = null;

async function createWindow() {
  startPython();

  try {
    await waitForServer(URL);
  } catch (e) {
    console.error(e.message);
    app.quit();
    return;
  }

  win = new BrowserWindow({
    width: 1280,
    height: 900,
    title: "Postdoc Tracker",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  win.loadURL(URL);

  // Open external links in the system browser, not in the app window
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  win.on("closed", () => { win = null; });
}

// ── App lifecycle ─────────────────────────────────────────────────────────────
app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (pyProcess) pyProcess.kill();
  app.quit();
});

app.on("activate", () => {
  if (win === null) createWindow();
});
