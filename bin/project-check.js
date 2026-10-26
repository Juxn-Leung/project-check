#!/usr/bin/env node
import { lstat, mkdir, mkdtemp, readFile, realpath, rename, rm, copyFile, cp, writeFile } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const payload = [
  'SKILL.md', 'agents/openai.yaml',
  'assets/inventory.template.json', 'assets/project.template.json',
  'assets/model.template.json', 'assets/project-map.html',
  'assets/playwright/phase-errors.ts', 'assets/playwright/phase-rule-engine.mjs',
  'assets/playwright/phase-rule-engine.d.mts',
  'assets/playwright/project-check-reporter.ts',
  'references/build.md', 'references/run.md', 'references/browser.md', 'references/contracts.md',
  'scripts/project_check.py', 'scripts/report_adapters.py',
  'scripts/acceptance.py', 'scripts/project_model.py', 'scripts/workspace_state.py', 'scripts/completion.py',
  'scripts/execution.py', 'scripts/source_index.py', 'scripts/index_typescript.mjs', 'references/execution.md',
  'references/map.md', 'references/openspec.md',
];
const installationManifest = '.install-manifest.json';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

const help = `Project Check — Codex Skill installer

Usage:
  project-check install --user [--dry-run]
  project-check install --project <existing-project-directory> [--dry-run]
  project-check upgrade --project <directory> [--dry-run] [--adopt-existing]
  project-check upgrade --user [--dry-run] [--adopt-existing]
  project-check --version

--user installs to ~/.agents/skills/project-check.
--project installs to <project>/.agents/skills/project-check.
Install refuses existing directories. Upgrade backs up outside the skills directory,
refuses modified managed files, and requires --adopt-existing for legacy installations.
These commands install the Skill;
it does not run application tests. Use $project-check in the target project's Codex chat.
`;

async function exists(path) {
  try { await lstat(path); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}

export async function installSkill({ project, user = false, dryRun = false, userHome = homedir(), upgrade = false, adoptExisting = false }) {
  if (user === Boolean(project)) throw new Error('Choose exactly one of --user or --project <directory>.');
  const root = resolve(user ? userHome : project);
  const rootStat = await lstat(root);
  if (!rootStat.isDirectory()) throw new Error(`Not a directory: ${root}`);
  const parent = join(root, '.agents', 'skills');
  const destination = join(parent, 'project-check');
  // Refuse an existing entry including a dangling symlink; never delete user work.
  const alreadyExists = await exists(destination);
  if (alreadyExists && !upgrade) throw new Error(`Installation already exists: ${destination}. No files changed.`);
  if (upgrade && !alreadyExists) throw new Error(`No installation to upgrade: ${destination}`);
  if (adoptExisting && !upgrade) throw new Error('--adopt-existing is upgrade-only.');
  const pkg = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'));
  const files = {};
  for (const relative of payload) {
    const stat = await lstat(join(packageRoot, relative));
    if (!stat.isFile()) throw new Error(`Package payload is missing or invalid: ${relative}`);
    files[relative] = hash(await readFile(join(packageRoot, relative)));
  }
  let previous;
  if (alreadyExists) {
    if (!(await lstat(destination)).isDirectory()) throw new Error('Upgrade destination must be a real directory.');
    const manifestPath = join(destination, installationManifest);
    if (await exists(manifestPath)) {
      if (!(await lstat(manifestPath)).isFile()) throw new Error('Invalid installation manifest.');
      previous = JSON.parse(await readFile(manifestPath, 'utf8'));
      if (previous.name !== pkg.name || !previous.files || Array.isArray(previous.files)) throw new Error('Invalid installation manifest.');
      for (const [relative, expected] of Object.entries(previous.files)) {
        if (relative.startsWith('/') || relative.split('/').some(part => !part || part === '..' || part === '.')) throw new Error('Unsafe managed resource path.');
        const path = join(destination, relative);
        if (!(await exists(path)) || !(await lstat(path)).isFile() || hash(await readFile(path)) !== expected) {
          throw new Error(`Locally modified managed resource: ${relative}. Merge it before upgrading; no files changed.`);
        }
      }
    } else if (!adoptExisting) throw new Error('Legacy installation has no manifest. Preview and use --adopt-existing to retain a complete backup.');
    for (const relative of [...payload, installationManifest]) {
      const parts = relative.split('/');
      for (let i = 1; i <= parts.length; i++) {
        const path = join(destination, ...parts.slice(0, i));
        if (await exists(path)) {
          const stat = await lstat(path);
          if (stat.isSymbolicLink() || (i < parts.length ? !stat.isDirectory() : !stat.isFile())) throw new Error(`Unsafe upgrade resource: ${relative}`);
        }
      }
    }
    if (previous?.version === pkg.version && JSON.stringify(previous.files) === JSON.stringify(files)) {
      return { destination, files: payload.length, version: pkg.version, dryRun, unchanged: true };
    }
  }
  if (dryRun) return { destination, files: payload.length, version: pkg.version, dryRun: true };
  await mkdir(parent, { recursive: true });
  // A lock coordinates simultaneous installs from this CLI; no lock stealing.
  const lock = join(parent, '.project-check-install.lock');
  await mkdir(lock);
  let staging;
  let backup;
  try {
    if ((await exists(destination)) !== alreadyExists) throw new Error('Installation changed during preparation; no files changed.');
    staging = await mkdtemp(join(parent, '.project-check-stage-'));
    if (upgrade) await cp(destination, staging, { recursive: true, dereference: false });
    for (const relative of payload) {
      const target = join(staging, relative);
      await mkdir(dirname(target), { recursive: true });
      await copyFile(join(packageRoot, relative), target);
    }
    if (previous) for (const relative of Object.keys(previous.files)) {
      if (!files[relative]) await rm(join(staging, relative));
    }
    await writeFile(join(staging, installationManifest), JSON.stringify({ format: 1, name: pkg.name, version: pkg.version, files }, null, 2) + '\n');
    if (upgrade) {
      backup = join(root, '.agents', 'skill-backups', `project-check-${Date.now()}-${randomUUID()}`);
      await mkdir(dirname(backup), { recursive: true });
      await rename(destination, backup);
    } else if (await exists(destination)) throw new Error(`Destination appeared during install: ${destination}. No files replaced.`);
    await rename(staging, destination);
    staging = undefined;
    return { destination, files: payload.length, version: pkg.version, backup, dryRun: false };
  } catch (error) {
    if (backup && !(await exists(destination))) await rename(backup, destination);
    throw error;
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
  const command = args.shift();
  if (!['install', 'upgrade'].includes(command)) throw new Error('Unknown command. Run project-check --help.');
  const options = { upgrade: command === 'upgrade' };
  while (args.length) {
    const flag = args.shift();
    if (flag === '--user' && options.user === undefined) options.user = true;
    else if (flag === '--dry-run' && options.dryRun === undefined) options.dryRun = true;
    else if (flag === '--adopt-existing' && options.adoptExisting === undefined) options.adoptExisting = true;
    else if (flag === '--project' && options.project === undefined && args[0] && !args[0].startsWith('--')) options.project = args.shift();
    else throw new Error(`Unknown, repeated, or incomplete option: ${flag}`);
  }
  const result = await installSkill(options);
  console.log(`${result.unchanged ? 'Already current:' : result.dryRun ? 'Would install' : 'Installed'} ${result.files} Skill files (${result.version}) at ${result.destination}`);
  if (result.backup) console.log(`Previous installation preserved at ${result.backup}`);
  if (!result.dryRun) console.log('In the target project’s Codex chat: use $project-check to change a feature, explain it, and verify it.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(await realpath(process.argv[1])).href) {
  main(process.argv.slice(2)).catch(error => {
    console.error(`project-check: ${error.message}`);
    process.exitCode = 1;
  });
}
