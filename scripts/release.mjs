import { spawnSync } from 'node:child_process';
import { appendFile, mkdir, mkdtemp, readFile, realpath, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const registry = 'https://registry.npmjs.org';

export function nextVersion(current, type) {
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(current)) {
    throw new Error(`Expected a stable x.y.z version, got ${current}`);
  }
  const numbers = current.split('.').map(Number);
  if (!numbers.every(Number.isSafeInteger)) throw new Error('Version exceeds safe integer range.');
  const [major, minor, patch] = numbers;
  const choices = { current, patch: `${major}.${minor}.${patch + 1}`, minor: `${major}.${minor + 1}.0`, major: `${major + 1}.0.0` };
  if (!Object.hasOwn(choices, type)) throw new Error(`Unknown release type: ${type}`);
  if (!choices[type].split('.').map(Number).every(Number.isSafeInteger)) throw new Error('Version exceeds safe integer range.');
  return choices[type];
}

export function publicationDecision(existing, integrity) {
  if (!existing) return 'publish';
  if (existing.dist?.integrity === integrity) return 'already-published';
  throw new Error('This version already exists with different contents. Choose a new version; never overwrite it.');
}

export async function readRegistry(path, fetcher = fetch) {
  const response = await fetcher(`${registry}/${path}`, { signal: AbortSignal.timeout(20000) });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`npm registry returned HTTP ${response.status}; refusing to treat it as an unused version.`);
  return response.json();
}

export function command(program, args, { capture = false, allowFailure = false, cwd } = {}) {
  const result = spawnSync(program, args, { cwd, encoding: 'utf8', stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0 && !allowFailure) {
    throw new Error(`${program} ${args.join(' ')} failed (${result.status}). ${capture ? result.stderr : ''}`);
  }
  return capture ? (result.stdout || '').trim() : result.status;
}

export function git(args, options = {}) {
  return command('git', args, { capture: true, ...options });
}

export function syncRelease(version, source, { cwd } = {}) {
  const run = args => git(args, { cwd });
  run(['fetch', 'origin', 'master', '--tags']);
  if (run(['rev-parse', 'origin/master']) !== source) {
    throw new Error('npm is published, but master advanced during release. Git sync stopped without a force push; reconcile the version commit before the next release.');
  }
  run(['add', 'package.json']);
  if (run(['diff', '--cached', '--name-only'])) run(['commit', '-m', `chore: release v${version}`]);
  const tag = `v${version}`;
  const existing = run(['tag', '--list', tag]);
  if (existing && run(['rev-parse', `${tag}^{commit}`]) !== run(['rev-parse', 'HEAD'])) {
    throw new Error(`npm is published, but ${tag} points elsewhere. No tags were replaced.`);
  }
  if (!existing) run(['tag', tag]);
  run(['push', '--atomic', 'origin', 'HEAD:refs/heads/master', `refs/tags/${tag}`]);
}

async function summary(text) {
  console.log(text);
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, `${text}\n`);
}

export async function release(type, dryRun) {
  if (!['true', 'false'].includes(dryRun)) throw new Error('dry_run must be true or false.');
  const preview = dryRun === 'true';
  if (!preview && (process.env.GITHUB_ACTIONS !== 'true' || process.env.GITHUB_REF !== 'refs/heads/master')) {
    throw new Error('Live releases run only through the master GitHub Actions workflow. Use dry-run locally.');
  }
  if (git(['status', '--porcelain'])) throw new Error('Release requires a clean checkout.');
  const source = git(['rev-parse', 'HEAD']);
  if (!preview) {
    if (source !== process.env.GITHUB_SHA) throw new Error('Checkout does not match the workflow source commit.');
    git(['fetch', 'origin', 'master', '--tags']);
    if (git(['rev-parse', 'origin/master']) !== source) {
      throw new Error('master changed since this run was started. Start a new run from the latest master.');
    }
  }
  const original = await readFile('package.json', 'utf8');
  const pkg = JSON.parse(original);
  const version = nextVersion(pkg.version, type);
  const output = process.env.RUNNER_TEMP
    ? join(process.env.RUNNER_TEMP, 'project-check-release')
    : await mkdtemp(join(tmpdir(), 'project-check-release-'));
  await mkdir(output, { recursive: true });
  let registryPublished = false;
  let synchronized = false;
  try {
    pkg.version = version;
    await writeFile('package.json', `${JSON.stringify(pkg, null, 2)}\n`);
    // Version changes must not invalidate tests by relying on a hardcoded version.
    command('npm', ['test']);
    command('npm', ['run', 'test:python']);
    const packed = JSON.parse(command('npm', ['pack', '--json', '--ignore-scripts', '--pack-destination', output], { capture: true }));
    if (packed.length !== 1 || packed[0].name !== pkg.name || packed[0].version !== version) throw new Error('Unexpected package artifact.');
    const artifact = packed[0];
    const tarball = join(output, artifact.filename);
    const smoke = await mkdtemp(join(tmpdir(), 'project-check-release-smoke-'));
    command('npm', ['exec', '--offline', '--yes', '--package', tarball, '--', 'project-check', 'install', '--project', smoke], { cwd: smoke });
    const plan = { name: pkg.name, version, type, source, integrity: artifact.integrity, filename: artifact.filename, dryRun: preview };
    await writeFile(join(output, 'release-plan.json'), JSON.stringify(plan, null, 2) + '\n');
    if (preview) {
      await summary(`## Preview passed: ${pkg.name}@${version}\n\nTests, packing and installation passed. No npm publish or Git push occurred. npm identity and Trusted Publisher permissions were not tested.\n\nArtifact: ${artifact.filename}`);
      return;
    }
    const encodedName = encodeURIComponent(pkg.name);
    const packageMetadata = await readRegistry(encodedName);
    if (!packageMetadata) throw new Error('First publish is required: publish this package once locally, then add the npm Trusted Publisher described in docs/publishing.md.');
    const existing = await readRegistry(`${encodedName}/${version}`);
    const decision = publicationDecision(existing, artifact.integrity);
    // Refuse tag conflicts before an irreversible npm publication.
    git(['add', 'package.json']);
    const plannedTree = git(['write-tree']);
    if (git(['tag', '--list', `v${version}`]) && git(['rev-parse', `v${version}^{tree}`]) !== plannedTree) {
      throw new Error(`Tag v${version} already represents different contents.`);
    }
    if (decision === 'publish') {
      command('npm', ['publish', tarball, '--access', 'public', '--tag', 'latest', '--provenance', '--ignore-scripts']);
    }
    registryPublished = true;
    // Verify registry bytes before updating Git. A delayed registry response is not a successful verification.
    let verified = false;
    for (let attempt = 0; attempt < 5; attempt++) {
      const current = await readRegistry(`${encodedName}/${version}`);
      if (current?.dist?.integrity === artifact.integrity) { verified = true; break; }
      if (current) publicationDecision(current, artifact.integrity);
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    if (!verified) throw new Error('Publish returned, but registry integrity could not yet be verified. Re-run this same workflow run; do not bump the version again.');
    syncRelease(version, source);
    synchronized = true;
    await summary(`## Published ${pkg.name}@${version}\n\n- npm: https://www.npmjs.com/package/${pkg.name}/v/${version}\n- Git branch: master\n- Git tag: v${version}\n- Integrity: verified against the tested tarball\n\nInstall: \`npx ${pkg.name}@${version} install --user\``);
  } catch (error) {
    await summary(`## Release did not complete\n\n${error.message}\n\n${registryPublished ? 'npm publication was observed, but follow-up verification or Git sync may be incomplete. Preserve this run and its artifact; retry the same run before starting a new version.' : 'Do not assume a failed or interrupted publish means no package was uploaded. The same run can check the candidate version and tarball integrity on retry.'}`);
    throw error;
  } finally {
    if (!synchronized) {
      // Only restore the manifest changed by this isolated release command.
      await writeFile('package.json', original);
      git(['reset', '--quiet', '--', 'package.json']);
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(await realpath(process.argv[1])).href) {
  release(process.argv[2], process.argv[3]).catch(error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
