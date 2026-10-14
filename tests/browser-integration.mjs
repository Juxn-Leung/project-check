#!/usr/bin/env node
// Opt-in real-browser regressions. Intentional product failures must be rejected by acceptance.
import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { cp, mkdir, mkdtemp, readFile, realpath, symlink, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDemo } from './browser/demo-server.mjs';
import { browserEnvironmentBlock } from './browser/environment.mjs';

const source = dirname(fileURLToPath(import.meta.url));
const repo = await realpath(process.env.PROJECT_CHECK_REPO_ROOT || resolve(source, '..'));
const reporterOnly = process.argv.includes('--reporter-only');
const mapOnly = process.argv.includes('--map-only');
if (reporterOnly && mapOnly) throw new Error('Select only one integration mode');
const args = process.argv.slice(2).filter(arg => !['--reporter-only', '--map-only'].includes(arg));
if (args.length && (args.length !== 2 || args[0] !== '--out')) throw new Error('Usage: node tests/browser-integration.mjs [--reporter-only | --map-only] [--out NEW_DIRECTORY]');
const requestedOutput = args.length ? resolve(args[1]) : await mkdtemp(resolve(tmpdir(), 'project-check-browser-'));
if (args.length) await mkdir(requestedOutput); // Never overwrite a previous evidence directory.
// Playwright reports source locations using real paths. Keep its root directory
// in the same form so macOS /var -> /private/var aliases do not change test IDs.
const output = await realpath(requestedOutput);
console.log(`${reporterOnly ? 'Reporter contract' : 'Isolated browser integration'} evidence: ${output}`);
const runtimeRequire = createRequire(resolve(process.env.PROJECT_CHECK_PLAYWRIGHT_ROOT || repo, 'package.json'));
let cli, modules;
try {
  cli = runtimeRequire.resolve('@playwright/test/cli');
  modules = resolve(dirname(runtimeRequire.resolve('@playwright/test/package.json')), '../..');
} catch {
  throw new Error('Install @playwright/test or set PROJECT_CHECK_PLAYWRIGHT_ROOT to an installed runtime directory. Browser mode also requires Chromium.');
}
const stage = resolve(output, 'project');
await mkdir(resolve(stage, 'tests'), { recursive: true });
await cp(resolve(source, 'browser'), resolve(stage, 'tests/browser'), { recursive: true });
await cp(resolve(repo, 'assets/playwright'), resolve(stage, 'assets/playwright'), { recursive: true });
await cp(resolve(repo, 'assets/project-map.html'), resolve(stage, 'assets/project-map.html'));
await writeFile(resolve(stage, 'package.json'), JSON.stringify({ private: true, type: 'module' }));
await symlink(modules, resolve(stage, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');

async function run(command, argv, env, logName) {
  const log = createWriteStream(resolve(output, logName));
  return await new Promise((resolveExit, reject) => {
    const child = spawn(command, argv, { cwd: stage, env, stdio: ['ignore', 'pipe', 'pipe'] });
    const timer = setTimeout(() => child.kill('SIGTERM'), 120000);
    child.stdout.on('data', chunk => { log.write(chunk); process.stdout.write(chunk); });
    child.stderr.on('data', chunk => { log.write(chunk); process.stderr.write(chunk); });
    child.once('error', error => { clearTimeout(timer); log.end(); reject(error); });
    child.once('close', (code, signal) => {
      clearTimeout(timer);
      log.end(() => signal ? reject(new Error(`${command} terminated: ${signal}`)) : resolveExit(code));
    });
  });
}

const python = process.env.PYTHON || 'python3';
const checker = resolve(stage, 'tests/browser/check-results.py');
const browserEnv = {
  ...process.env,
  PROJECT_CHECK_BROWSER_REPORT: resolve(output, 'playwright.json'),
  PROJECT_CHECK_BROWSER_ARTIFACTS: resolve(output, 'artifacts'),
};
const testArgs = [cli, 'test', '--config', resolve(stage, 'tests/browser/playwright.config.ts')];
async function recordBlock(blocked) {
  await writeFile(resolve(output, 'integration-summary.json'), JSON.stringify(blocked, null, 2) + '\n');
  console.error(`BLOCKED: ${blocked.reason}`);
  process.exitCode = 2;
}

async function verifyBrowser() {
  const prepare = await run(python, [checker, 'prepare', repo, stage, output], process.env, 'prepare.log');
  if (prepare !== 0) throw new Error(`Preparing snapshot failed: exit ${prepare}`);
  const dataFile = resolve(output, 'profile.json');
  let demo;
  try {
    demo = await startDemo(dataFile);
  } catch (error) {
    await recordBlock({ outcome: 'BLOCKED', scope: 'local demonstrator only', cases_executed: 0,
      reason: `Local integration server could not start; no browser scenario ran. ${error.message}` });
    return;
  }
  try {
    const code = await run(process.execPath, testArgs, { ...browserEnv, PROJECT_CHECK_DEMO_URL: demo.url }, 'playwright.log');
    const blocked = browserEnvironmentBlock(JSON.parse(await readFile(resolve(output, 'playwright.json'), 'utf8')));
    if (blocked) {
      await recordBlock(blocked);
      return;
    }
    if (code !== 1) throw new Error(`Expected runner exit 1 from deliberately injected runtime faults, got ${code}`);
    const persisted = JSON.parse(await readFile(dataFile, 'utf8'));
    if (persisted.name !== 'Persisted browser user') throw new Error('Happy path did not persist its value in the backend file');
    const verified = await run(python, [checker, 'verify', repo, stage, output], process.env, 'verification.log');
    if (verified !== 0) throw new Error(`Acceptance outcome regression: exit ${verified}`);
  } finally {
    await demo.close();
  }
}

if (mapOnly) {
  const code = await run(process.execPath, testArgs, { ...browserEnv, PROJECT_CHECK_MAP_ONLY: '1' }, 'playwright.log');
  const report = JSON.parse(await readFile(resolve(output, 'playwright.json'), 'utf8'));
  const blocked = browserEnvironmentBlock(report);
  if (blocked) await recordBlock(blocked);
  else {
    if (code !== 0 || report.stats?.expected !== 5 || report.stats?.unexpected !== 0) throw new Error('Map interaction regression failed; inspect playwright.json');
    await writeFile(resolve(output, 'integration-summary.json'), JSON.stringify({ outcome: 'PASSED', scope: 'map UI regression only', cases_executed: 5 }, null, 2) + '\n');
    console.log('Map UI: 5 interaction regressions passed');
  }
} else if (reporterOnly) {
  const code = await run(process.execPath, testArgs, { ...browserEnv, PROJECT_CHECK_REPORTER_ONLY: '1' }, 'playwright.log');
  if (code !== 0) throw new Error(`No-browser reporter contract tests failed: exit ${code}`);
  const verified = await run(python, [checker, 'reporter', repo, stage, output], process.env, 'verification.log');
  if (verified !== 0) throw new Error(`Reporter contract regression: exit ${verified}`);
} else {
  await verifyBrowser();
}
