import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { installSkill } from '../bin/project-check.js';

const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));

async function temporary(t) {
  const root = await mkdtemp(join(tmpdir(), 'project-check-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test('project installation includes usable Skill resources without business configuration', async t => {
  const root = await temporary(t);
  const result = await installSkill({ project: root });
  assert.equal(result.destination, join(root, '.agents', 'skills', 'project-check'));
  assert.match(await readFile(join(result.destination, 'SKILL.md'), 'utf8'), /name: project-check/);
  assert.match(await readFile(join(result.destination, 'scripts/project_check.py'), 'utf8'), /def summarize/);
  assert.match(await readFile(join(result.destination, 'scripts/report_adapters.py'), 'utf8'), /def adapt_report/);
  assert.match(await readFile(join(result.destination, 'assets/playwright/phase-errors.ts'), 'utf8'), /phase\.run|base\.extend/);
  assert.ok(JSON.parse(await readFile(join(result.destination, 'assets/project.template.json'), 'utf8')));
  for (const resource of ['scripts/acceptance.py', 'scripts/workspace_state.py', 'scripts/project_model.py', 'scripts/completion.py', 'references/map.md', 'references/openspec.md', 'assets/project-map.html', 'assets/playwright/project-check-reporter.ts']) {
    assert.ok((await readFile(join(result.destination, resource))).length, resource);
  }
  assert.deepEqual(await readdir(root), ['.agents']);
  assert.ok(!(await readdir(result.destination)).includes('tests'));
});

test('user installation targets the supplied home, not the project', async t => {
  const root = await temporary(t);
  const result = await installSkill({ user: true, userHome: root });
  assert.equal(result.destination, join(root, '.agents', 'skills', 'project-check'));
});

test('dry run creates no files', async t => {
  const root = await temporary(t);
  const result = await installSkill({ project: root, dryRun: true });
  assert.equal(result.dryRun, true);
  assert.deepEqual(await readdir(root), []);
});

test('existing installation and local modifications are preserved', async t => {
  const root = await temporary(t);
  const result = await installSkill({ project: root });
  const path = join(result.destination, 'SKILL.md');
  await writeFile(path, 'local edits');
  await assert.rejects(installSkill({ project: root }), /already exists/);
  assert.equal(await readFile(path, 'utf8'), 'local edits');
});

test('dangling destination symlink is not replaced', async t => {
  const root = await temporary(t);
  const result = await installSkill({ project: root });
  await rm(result.destination, { recursive: true });
  await symlink(join(root, 'missing'), result.destination);
  await assert.rejects(installSkill({ project: root }), /already exists/);
});

test('simultaneous installs have exactly one winner and clean temporary state', async t => {
  const root = await temporary(t);
  const results = await Promise.allSettled([installSkill({ project: root }), installSkill({ project: root })]);
  assert.equal(results.filter(x => x.status === 'fulfilled').length, 1);
  assert.deepEqual(await readdir(join(root, '.agents', 'skills')), ['project-check']);
});

test('ambiguous or missing scope is rejected', async t => {
  const root = await temporary(t);
  await assert.rejects(installSkill({}), /exactly one/);
  await assert.rejects(installSkill({ user: true, project: root }), /exactly one/);
  await assert.rejects(installSkill({ project: join(root, 'absent') }), /ENOENT/);
  assert.deepEqual(await readdir(root), []);
});

test('CLI reports version and rejects invalid options', () => {
  const script = fileURLToPath(new URL('../bin/project-check.js', import.meta.url));
  const result = spawnSync(process.execPath, [script, '--version'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), `${manifest.name}@${manifest.version}`);
  for (const args of [['run'], ['install'], ['install', '--project'], ['install', '--user', '--user']]) {
    const bad = spawnSync(process.execPath, [script, ...args], { encoding: 'utf8' });
    assert.equal(bad.status, 1, args.join(' '));
  }
});

test('CLI starts through an npm-style executable symlink', async t => {
  const root = await temporary(t);
  const script = fileURLToPath(new URL('../bin/project-check.js', import.meta.url));
  const link = join(root, 'project-check');
  await symlink(script, link);
  const result = spawnSync(process.execPath, [link, '--version'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), `${manifest.name}@${manifest.version}`);
});
