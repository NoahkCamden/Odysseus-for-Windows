const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const cwd = process.cwd();

function fileCandidate(relativePath) {
  return path.join(cwd, relativePath);
}

function buildCandidates() {
  const candidates = [];
  if (process.env.ODYSSEUS_PYTHON) {
    candidates.push({ cmd: process.env.ODYSSEUS_PYTHON, args: [] });
  }

  const localPaths = [
    fileCandidate(".venv/Scripts/python.exe"),
    fileCandidate("venv/Scripts/python.exe"),
    fileCandidate(".venv/bin/python"),
    fileCandidate("venv/bin/python"),
  ];

  for (const candidate of localPaths) {
    if (fs.existsSync(candidate)) {
      candidates.push({ cmd: candidate, args: [] });
    }
  }

  candidates.push({ cmd: "python", args: [] });
  candidates.push({ cmd: "python3", args: [] });
  if (process.platform === "win32") {
    candidates.push({ cmd: "py", args: ["-3"] });
  }

  return candidates;
}

function runCandidate(index, candidates) {
  if (index >= candidates.length) {
    console.error("Failed to locate a Python interpreter for Odysseus backend.");
    console.error("Set ODYSSEUS_PYTHON to a valid interpreter path.");
    process.exit(1);
  }

  const candidate = candidates[index];
  const args = [
    ...candidate.args,
    "-m",
    "uvicorn",
    "app:app",
    "--host",
    "127.0.0.1",
    "--port",
    "7000",
  ];

  const child = spawn(candidate.cmd, args, {
    cwd,
    stdio: "inherit",
    env: process.env,
  });

  let resolved = false;
  const timer = setTimeout(() => {
    resolved = true;
    console.log(`Odysseus backend started with: ${candidate.cmd}`);
  }, 1500);

  child.on("error", () => {
    clearTimeout(timer);
    if (!resolved) {
      runCandidate(index + 1, candidates);
    }
  });

  child.on("exit", (code) => {
    clearTimeout(timer);
    if (!resolved && code !== 0) {
      runCandidate(index + 1, candidates);
      return;
    }
    process.exit(code ?? 0);
  });

  process.on("SIGINT", () => child.kill("SIGINT"));
  process.on("SIGTERM", () => child.kill("SIGTERM"));
}

runCandidate(0, buildCandidates());
