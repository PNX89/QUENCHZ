/**
 * Start the Python server, run the TypeScript proofs against it, stop it.
 *
 * One command, no manual steps, and nothing left running. The tokens are written to a file in
 * the system temp directory that the Python side refuses to place inside the repository, and
 * it is deleted here whatever happens.
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const REPO = resolve(import.meta.dirname, '..', '..', '..');
const PORT = process.env.QUENCHZ_PORT ?? '8931';
const workdir = mkdtempSync(join(tmpdir(), 'quenchz-interop-'));
const manifest = join(workdir, 'manifest.json');

let server;

function stopServer() {
  if (server && server.exitCode === null) server.kill('SIGTERM');
  rmSync(workdir, { recursive: true, force: true });
}

process.on('exit', stopServer);
process.on('SIGINT', () => { stopServer(); process.exit(130); });

async function waitForManifest(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (existsSync(manifest)) return true;
    if (server.exitCode !== null) return false;
    await sleep(150);
  }
  return false;
}

async function waitForServer(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  const url = `http://127.0.0.1:${PORT}/.well-known/oauth-protected-resource/mcp`;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return true;
    } catch { /* not listening yet */ }
    if (server.exitCode !== null) return false;
    await sleep(150);
  }
  return false;
}

function run(command, args, options = {}) {
  return new Promise((resolveRun) => {
    const child = spawn(command, args, { stdio: 'inherit', ...options });
    child.on('exit', (code) => resolveRun(code ?? 1));
  });
}

console.log(`Starting the Python server on port ${PORT}`);
server = spawn(
  'uv',
  ['run', 'python', '-m', 'quenchz.interop_server', '--port', PORT, '--tokens-file', manifest],
  { cwd: REPO, stdio: ['ignore', 'inherit', 'inherit'] },
);

if (!(await waitForManifest(60_000))) {
  console.error('the server never wrote its token manifest');
  process.exit(2);
}
if (!(await waitForServer(60_000))) {
  console.error('the server never started listening');
  process.exit(2);
}

const code = await run(process.execPath, ['dist/prove.js', manifest], {
  cwd: resolve(import.meta.dirname, '..'),
});
stopServer();
process.exit(code);
