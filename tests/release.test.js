import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { nextVersion, publicationDecision, readRegistry, git, syncRelease } from '../scripts/release.mjs';

test('stable version choices reset lower components correctly', () => {
  assert.equal(nextVersion('1.2.3', 'patch'), '1.2.4');
  assert.equal(nextVersion('1.2.3', 'minor'), '1.3.0');
  assert.equal(nextVersion('1.2.3', 'major'), '2.0.0');
  assert.equal(nextVersion('1.2.3', 'current'), '1.2.3');
  for (const version of ['1.2', 'v1.2.3', '01.2.3', '1.2.3-next.1', '1.2.3;echo bad']) {
    assert.throws(() => nextVersion(version, 'patch'));
  }
  assert.throws(() => nextVersion('1.2.3', 'unknown'));
});

test('retries skip only a byte-identical published package', () => {
  assert.equal(publicationDecision(null, 'sha512-abc'), 'publish');
  assert.equal(publicationDecision({ dist: { integrity: 'sha512-abc' } }, 'sha512-abc'), 'already-published');
  assert.throws(() => publicationDecision({ dist: { integrity: 'sha512-other' } }, 'sha512-abc'), /different contents/);
  assert.throws(() => publicationDecision({}, 'sha512-abc'), /different contents/);
});

test('only registry 404 means absent; authentication and network failures stop release', async () => {
  assert.equal(await readRegistry('fixture', async () => ({ status: 404 })), null);
  for (const status of [401, 403, 429, 500]) {
    await assert.rejects(readRegistry('fixture', async () => ({ status, ok: false })), /refusing/);
  }
  await assert.rejects(readRegistry('fixture', async () => { throw new Error('network down'); }), /network down/);
  assert.deepEqual(await readRegistry('fixture', async () => ({ ok: true, status: 200, json: async () => ({ name: 'fixture' }) })), { name: 'fixture' });
});

async function repository(t) {
  const root = await mkdtemp(join(tmpdir(), 'project-check-release-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const cwd = join(root, 'checkout');
  const remote = join(root, 'remote.git');
  await mkdir(cwd);
  git(['init', '--bare', remote], { cwd });
  git(['init', '-b', 'master'], { cwd });
  git(['config', 'user.name', 'Release Test'], { cwd });
  git(['config', 'user.email', 'release-test@example.invalid'], { cwd });
  await writeFile(join(cwd, 'package.json'), JSON.stringify({ name: 'fixture', version: '0.1.0' }) + '\n');
  git(['add', 'package.json'], { cwd });
  git(['commit', '-m', 'fixture'], { cwd });
  git(['remote', 'add', 'origin', remote], { cwd });
  git(['push', '-u', 'origin', 'master'], { cwd });
  const source = git(['rev-parse', 'HEAD'], { cwd });
  await writeFile(join(cwd, 'package.json'), JSON.stringify({ name: 'fixture', version: '0.1.1' }) + '\n');
  return { root, cwd, remote, source };
}

test('successful release sync pushes version and tag atomically and can be verified again', async t => {
  const { cwd, remote, source } = await repository(t);
  syncRelease('0.1.1', source, { cwd });
  const head = git(['rev-parse', 'HEAD'], { cwd });
  assert.equal(git(['--git-dir', remote, 'rev-parse', 'refs/heads/master'], { cwd }), head);
  assert.equal(git(['--git-dir', remote, 'rev-parse', 'refs/tags/v0.1.1'], { cwd }), head);
  assert.equal(JSON.parse(git(['--git-dir', remote, 'show', 'master:package.json'], { cwd })).version, '0.1.1');
  syncRelease('0.1.1', head, { cwd });
  assert.equal(git(['rev-parse', 'HEAD'], { cwd }), head);
  assert.equal(git(['status', '--porcelain'], { cwd }), '');
});

test('concurrent master changes are preserved; release never force pushes', async t => {
  const { root, cwd, remote, source } = await repository(t);
  const other = join(root, 'other');
  git(['clone', '--branch', 'master', remote, other], { cwd });
  git(['config', 'user.name', 'Other Test'], { cwd: other });
  git(['config', 'user.email', 'other@example.invalid'], { cwd: other });
  await writeFile(join(other, 'other.txt'), 'concurrent change');
  git(['add', 'other.txt'], { cwd: other });
  git(['commit', '-m', 'concurrent change'], { cwd: other });
  git(['push', 'origin', 'master'], { cwd: other });
  const otherHead = git(['rev-parse', 'HEAD'], { cwd: other });
  assert.throws(() => syncRelease('0.1.1', source, { cwd }), /master advanced/);
  assert.equal(git(['--git-dir', remote, 'rev-parse', 'master'], { cwd }), otherHead);
  assert.equal(git(['--git-dir', remote, 'tag', '--list', 'v0.1.1'], { cwd }), '');
});

test('conflicting release tags are never replaced', async t => {
  const { cwd, remote, source } = await repository(t);
  git(['tag', 'v0.1.1'], { cwd });
  git(['push', 'origin', 'v0.1.1'], { cwd });
  assert.throws(() => syncRelease('0.1.1', source, { cwd }), /points elsewhere/);
  assert.equal(git(['--git-dir', remote, 'rev-parse', 'refs/tags/v0.1.1'], { cwd }), source);
  assert.equal(git(['--git-dir', remote, 'rev-parse', 'refs/heads/master'], { cwd }), source);
});
