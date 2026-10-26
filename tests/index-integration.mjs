// Optional locally; required in CI with a pinned existing TypeScript runtime.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const runtime = process.env.PROJECT_CHECK_TYPESCRIPT_ROOT;
if (!runtime) throw new Error('Set PROJECT_CHECK_TYPESCRIPT_ROOT to an existing TypeScript runtime directory');
const root = await mkdtemp(resolve(tmpdir(), 'project-check-index-'));
const script = fileURLToPath(new URL('../scripts/project_check.py', import.meta.url));
function run(args) {
  const result = spawnSync(process.env.PYTHON || 'python3', [script, ...args, '--root', root], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
}
try {
  await mkdir(resolve(root, 'src'));
  await writeFile(resolve(root, 'src/service.ts'), [
    'export function validate(value: number): number { return value; }',
    'export function save(value: number) { return validate(value); }',
    'export class Store {',
    '  constructor(public name: string) { save(1); }',
    '  get size(): number { return 1; }',
    '  set size(next: number) { save(next); }',
    "  drop() { this.name = ''; }",
    '}',
    'export const named = (value: number) => save(value);',
    'export function shadowed(validate: Function) { return validate(0); }',
  ].join('\n'));
  await mkdir(resolve(root, 'tests'));
  await writeFile(resolve(root, 'tests/service.test.ts'), 'function testExisting() {}');
  await mkdir(resolve(root, 'dist'));
  await writeFile(resolve(root, 'dist/bundle.js'), 'function bundled() {}');
  run(['init']);
  run(['index', '--typescript-root', runtime, '--path', 'src']);
  const index = JSON.parse(await readFile(resolve(root, '.project-check/source-index.json'), 'utf8'));
  const names = index.nodes.map(n => n.symbol);
  assert.deepEqual(names.sort(), ['Store.constructor', 'Store.drop', 'Store.size [get]', 'Store.size [set]', 'named', 'save', 'shadowed', 'validate'].sort());
  assert.equal(index.nodes.find(n => n.symbol === 'Store.size [get]').inputs.length, 0);
  assert.equal(index.nodes.find(n => n.symbol === 'Store.size [set]').inputs[0].name, 'next');
  assert.notEqual(index.nodes.find(n => n.symbol === 'Store.size [get]').sources[0].line,
    index.nodes.find(n => n.symbol === 'Store.size [set]').sources[0].line);
  assert.ok(index.nodes.every(n => n.sources[0].end_line >= n.sources[0].line));
  assert.ok(index.unresolved_calls.some(c => c.expression === 'validate' && names.includes('shadowed')));
  assert.equal(index.parse_errors.length, 0);
  assert.ok(index.edges.length >= 2);
  const model = await readFile(resolve(root, '.project-check/model.json'), 'utf8');
  assert.equal(JSON.parse(model).nodes.length, index.nodes.length);
  run(['index', '--typescript-root', runtime, '--path', 'src']);
  assert.equal(await readFile(resolve(root, '.project-check/model.json'), 'utf8'), model);
  console.log('Python CLI + TypeScript AST integration passed: declarations, constructor, ranges, calls, shadowing and stable merge.');
} finally { await rm(root, { recursive: true, force: true }); }
