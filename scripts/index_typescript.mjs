// Optional parser: reuse an existing project's TypeScript installation. Never install implicitly.
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
let ts;
try { ts = createRequire(resolve(process.argv[2], 'package.json'))('typescript'); }
catch { process.stderr.write('Existing TypeScript runtime required; pass --typescript-root <directory> or review JS/TS manually.\n'); process.exit(2); }
let input = '';
for await (const chunk of process.stdin) input += chunk;
const output = { nodes: [], edges: [], unresolved_calls: [], parse_errors: [] };
for (const file of JSON.parse(input)) {
  const source = ts.createSourceFile(file.path, file.text, ts.ScriptTarget.Latest, true);
  if (source.parseDiagnostics.length) {
    output.parse_errors.push({ path: file.path, reason: ts.flattenDiagnosticMessageText(source.parseDiagnostics[0].messageText, '\n') });
    continue;
  }
  const functions = [];
  function walk(node, prefix = '') {
    if (ts.isClassDeclaration(node)) {
      const name = node.name?.text;
      if (name) node.members.forEach(member => walk(member, prefix + name + '.'));
      return;
    }
    const constructor = ts.isConstructorDeclaration(node);
    const declaration = ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || constructor ||
      ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node);
    const variable = ts.isVariableDeclaration(node) && node.initializer &&
      (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer));
    if ((declaration || variable) && (constructor || node.name &&
        (ts.isIdentifier(node.name) || ts.isPrivateIdentifier(node.name) || ts.isStringLiteral(node.name)))) {
      const fn = variable ? node.initializer : node;
      const accessor = ts.isGetAccessorDeclaration(node) ? ' [get]' : ts.isSetAccessorDeclaration(node) ? ' [set]' : '';
      const symbol = prefix + (constructor ? 'constructor' : node.name.text) + accessor;
      functions.push({ symbol, fn, node });
      fn.body?.forEachChild(child => walk(child, symbol + '.<locals>.'));
      return;
    }
    node.forEachChild(child => walk(child, prefix));
  }
  walk(source);
  const counts = new Map();
  for (const row of functions) counts.set(row.symbol, (counts.get(row.symbol) || 0) + 1);
  for (const row of functions) {
    const line = source.getLineAndCharacterOfPosition(row.node.getStart(source)).line + 1;
    const identity = counts.get(row.symbol) === 1 ? row.symbol : row.symbol + '@' + line;
    row.id = 'symbol:' + createHash('sha256').update(file.path + '#' + identity).digest('hex').slice(0, 20);
    output.nodes.push({ id: row.id, kind: 'function', name: row.symbol, symbol: row.symbol,
      description: '源码中的函数声明；业务用途尚未复核。', provenance: 'observed', index_origin: 'typescript-ast',
      semantic_review: 'required', sources: [{ path: file.path, sha256: file.sha256, line,
        end_line: source.getLineAndCharacterOfPosition(row.node.end).line + 1 }],
      inputs: row.fn.parameters.map(p => ({ name: p.name.getText(source), type: p.type?.getText(source) || '未标注',
        description: '源码参数；业务含义尚未复核。' })),
      outputs: [{ name: 'return', type: row.fn.type?.getText(source) || '未标注', description: '返回值业务含义尚未复核。' }] });
  }
  for (const row of functions) {
    const shadowed = new Set(row.fn.parameters.map(p => p.name.getText(source)));
    const calls = [];
    function inspect(node) {
      if (node !== row.fn && (ts.isFunctionLike(node) || ts.isClassDeclaration(node))) return;
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) shadowed.add(node.name.text);
      if (ts.isCallExpression(node)) calls.push(node);
      node.forEachChild(inspect);
    }
    inspect(row.fn);
    for (const call of calls) {
      const expression = call.expression.getText(source);
      const matches = ts.isIdentifier(call.expression) && !shadowed.has(expression)
        ? functions.filter(f => f.symbol === expression && !f.symbol.includes('.')) : [];
      const line = source.getLineAndCharacterOfPosition(call.getStart(source)).line + 1;
      if (matches.length === 1) {
        const id = row.id + '->' + matches[0].id;
        if (!output.edges.some(edge => edge.id === id)) output.edges.push({
          id, from: row.id, to: matches[0].id, kind: 'calls', provenance: 'inferred', index_origin: 'typescript-ast',
          description: '静态名称匹配的调用；运行时绑定尚未验证。',
          sources: [{ path: file.path, sha256: file.sha256, line,
            end_line: source.getLineAndCharacterOfPosition(call.end).line + 1 }] });
      } else output.unresolved_calls.push({ path: file.path, line, caller: row.id, expression,
        reason: '外部、动态、遮蔽或未支持的名称解析；未生成确定连线。' });
    }
  }
}
process.stdout.write(JSON.stringify(output));
