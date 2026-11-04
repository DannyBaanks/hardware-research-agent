#!/usr/bin/env node

/**
 * Hardware Research Agent (IHRA) - Universal CLI Bridge
 * Orchestrates REA (Binary Forensics), GPU-Unlock-Ada (GPU Canaries),
 * and HRA (Hardware Research Protocol & Safety Sentinel) in a single unified,
 * zero-bloat CLI interface for agents without heavy MCP or TUI overhead.
 */

import { spawn } from "node:child_process";
import os from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(__dirname, "..");
const args = process.argv.slice(2);

// If called without arguments, default to discover
const effectiveArgs = args.length === 0 ? ["discover"] : args;

// Execute Python core CLI engine
const python = spawn("python3", ["-m", "core.cli", ...effectiveArgs], {
  cwd: projectRoot,
  stdio: "inherit",
  env: {
    ...process.env,
    PYTHONPATH: projectRoot,
  },
});

python.on("error", (err) => {
  console.error(
    JSON.stringify(
      {
        status: "ERROR",
        error: "Failed to spawn python3 runtime for IHRA CLI",
        details: err.message,
      },
      null,
      2
    )
  );
  process.exit(1);
});

python.on("close", (code, signal) => {
  if (signal) {
    // A killed core must never look like success: use the shell's 128+N convention.
    const signo = os.constants.signals[signal] ?? 0;
    process.exit(128 + signo);
  }
  process.exit(code ?? 1);
});
