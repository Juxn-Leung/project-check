#!/usr/bin/env node
import { lstat, mkdir, mkdtemp, readFile, realpath, rename, rm, copyFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const payload = [
  'SKILL.md', 'agents/openai.yaml',
  'assets/inventory.template.json', 'assets/project.template.json',
  'assets/playwright/phase-errors.ts', 'assets/playwright/phase-rule-engine.mjs',
  'assets/playwright/phase-rule-engine.d.mts',
  'references/build.md', 'references/run.md', 'references/browser.md', 'references/contracts.md',
  'scripts/project_check.py', 'scripts/report_adapters.py',
];

const help = `Project Check — Codex Skill installer

Usage:
  project-check install --user [--dry-run]
  project-check install --project <existing-project-directory> [--dry-run]
  project-check --version

--user installs to ~/.agents/skills/project-check.
--project installs to <project>/.agents/skills/project-check.
Existing installations are never overwritten. This command installs the Skill;
it does not run application tests. Use $project-check in the target project's Codex chat.
`;

async function exists(path) {
  try { await lstat(path); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}

export async function installSkill({ project, user = false, dryRun = false, userHome = homedir() }) {
  if (user === Boolean(project)) throw new Error('Choose exactly one of --user or --project <directory>.');
  const root = resolve(user ? userHome : project);
  const rootStat = await lstat(root);
  if (!rootStat.isDirectory()) throw new Error(`Not a directory: ${root}`);
  const parent = join(root, '.agents', 'skills');
  const destination = join(parent, 'project-check');
  // Refuse an existing entry including a dangling symlink; never delete user work.
  if (await exists(destination)) throw new Error(`Installation already exists: ${destination}. No files changed.`);
  for (const relative of payload) {
    const stat = await lstat(join(packageRoot, relative));
    if (!stat.isFile()) throw new Error(`Package payload is missing or invalid: ${relative}`);
  }
  if (dryRun) return { destination, files: payload.length, dryRun: true };
  await mkdir(parent, { recursive: true });
  // A lock coordinates simultaneous installs from this CLI; no lock stealing.
  const lock = join(parent, '.project-check-install.lock');
  await mkdir(lock);
  let staging;
  try {
    if (await exists(destination)) throw new Error(`Installation already exists: ${destination}. No files changed.`);
    staging = await mkdtemp(join(parent, '.project-check-stage-'));
    for (const relative of payload) {
      const target = join(staging, relative);
      await mkdir(dirname(target), { recursive: true });
      await copyFile(join(packageRoot, relative), target);
    }
    if (await exists(destination)) throw new Error(`Destination appeared during install: ${destination}. No files replaced.`);
    await rename(staging, destination);
    staging = undefined;
    return { destination, files: payload.length, dryRun: false };
  } finally {
    if (staging) await rm(staging, { recursive: true, force: true });
    await rm(lock, { recursive: true, force: true });
  }
}

export async function main(args) {
  if (args.length === 0 || (args.length === 1 && ['--help', '-h'].includes(args[0]))) {
    console.log(help);
    return;
  }
  if (args.length === 1 && args[0] === '--version') {
    const pkg = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'));
    console.log(`${pkg.name}@${pkg.version}`);
    return;
  }
  if (args.shift() !== 'install') throw new Error('Unknown command. Run project-check --help.');
  const options = {};
  while (args.length) {
    const flag = args.shift();
    if (flag === '--user' && options.user === undefined) options.user = true;
    else if (flag === '--dry-run' && options.dryRun === undefined) options.dryRun = true;
    else if (flag === '--project' && options.project === undefined && args[0] && !args[0].startsWith('--')) options.project = args.shift();
    else throw new Error(`Unknown, repeated, or incomplete option: ${flag}`);
  }
  const result = await installSkill(options);
  console.log(`${result.dryRun ? 'Would install' : 'Installed'} ${result.files} Skill files at ${result.destination}`);
  if (!result.dryRun) console.log('In the target project’s Codex chat: use $project-check build.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(await realpath(process.argv[1])).href) {
  main(process.argv.slice(2)).catch(error => {
    console.error(`project-check: ${error.message}`);
    process.exitCode = 1;
  });
}
