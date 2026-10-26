"""Conservative syntax indexing. Business meaning and runtime dispatch remain reviewed inputs."""
import ast
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import project_check as pc
import project_model as model
import workspace_state


def node_id(path, symbol):
    return "symbol:" + hashlib.sha256((path + "#" + symbol).encode()).hexdigest()[:20]


def python_symbols(path, content, sha):
    tree = ast.parse(content, filename=path)
    found, parents = [], {}

    def declarations(body, prefix="", parent=None):
        for item in body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + item.name
                found.append((name, item, parent))
                declarations(item.body, name + ".<locals>.", item)
            elif isinstance(item, ast.ClassDef):
                declarations(item.body, prefix + item.name + ".", item)
    declarations(tree.body)
    counts = Counter(name for name, _, _ in found)
    nodes, lookup = [], {}
    for name, item, parent in found:
        identity = name if counts[name] == 1 else name + "@" + str(item.lineno)
        start = min([item.lineno] + [decorator.lineno for decorator in item.decorator_list])
        arguments = [*item.args.posonlyargs, *item.args.args, *item.args.kwonlyargs]
        if item.args.vararg:
            arguments.append(item.args.vararg)
        if item.args.kwarg:
            arguments.append(item.args.kwarg)
        node = {"id": node_id(path, identity), "kind": "function", "name": name, "symbol": name,
                "description": "源码中的函数声明；业务用途尚未复核。", "provenance": "observed",
                "sources": [{"path": path, "sha256": sha, "line": start, "end_line": item.end_lineno}],
                "inputs": [{"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else "未标注",
                            "description": "源码参数；业务含义尚未复核。"} for arg in arguments],
                "outputs": [{"name": "return", "type": ast.unparse(item.returns) if item.returns else "未标注",
                             "description": "返回值业务含义尚未复核。"}],
                "index_origin": "python-ast", "source_docstring": ast.get_docstring(item) or "",
                "semantic_review": "required"}
        nodes.append(node)
        if counts[name] == 1:
            lookup[name] = node
        parents[node["id"]] = (item, parent)
    edges, unresolved = [], []
    for node in nodes:
        item, parent = parents[node["id"]]
        shadowed = {arg.arg for arg in [*item.args.posonlyargs, *item.args.args, *item.args.kwonlyargs]}
        for child in ast.walk(item):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                shadowed.add(child.id)
        # Do not attribute nested functions' calls to their enclosing function.
        def calls(part):
            for child in ast.iter_child_nodes(part):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(child, ast.Call):
                    yield child
                yield from calls(child)
        for call in calls(item):
            expression = ast.unparse(call.func)
            target = None
            if isinstance(call.func, ast.Name) and call.func.id not in shadowed:
                # Top-level names only: nested scopes/import aliases need richer resolution.
                target = lookup.get(call.func.id)
            if target:
                key = node["id"] + "->" + target["id"]
                if not any(edge["id"] == key for edge in edges):
                    edges.append({"id": key, "from": node["id"], "to": target["id"], "kind": "calls",
                        "description": "静态名称匹配的调用；运行时绑定尚未验证。", "provenance": "inferred",
                        "sources": [{"path": path, "sha256": sha, "line": call.lineno, "end_line": call.end_lineno}],
                        "index_origin": "python-ast"})
            else:
                unresolved.append({"path": path, "line": call.lineno, "caller": node["id"], "expression": expression,
                                   "reason": "外部、动态、遮蔽或未支持的名称解析；未生成确定连线。"})
    return nodes, edges, unresolved


def index_sources(root, typescript_root=None, paths=None, include_tests=False):
    root = Path(root).resolve()
    snapshot = workspace_state.capture(root)
    result = {"version": 1, "method": "syntax-index", "nodes": [], "edges": [], "unresolved_calls": [],
              "parse_errors": [], "unsupported_files": [], "skipped_files": [],
              "limitations": ["静态结构不证明业务含义、运行时调用或需求覆盖。",
                              "索引命名函数和类方法；匿名回调、动态属性和跨模块名称解析仍需人工阅读。"]}
    javascript = []
    prefixes = [str(Path(path)).rstrip("/") for path in paths or []]
    for prefix in prefixes:
        pc.require(not Path(prefix).is_absolute() and ".." not in Path(prefix).parts, "Index paths must stay inside project")
    for path, identity in snapshot["files"].items():
        if identity.get("kind") != "file" or path.startswith(".project-check/"):
            continue
        parts = Path(path).parts
        if prefixes and not any(prefix == "." or path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
            continue
        if any(part in workspace_state.GENERATED | {".agents", ".codex", "vendor"} for part in parts):
            result["skipped_files"].append({"path": path, "reason": "生成物、依赖或工具目录，不进入默认业务结构索引。"})
            continue
        name = Path(path).name
        is_test = (any(part in {"test", "tests", "__tests__", "e2e"} for part in parts)
                   or name.startswith("test_") or name.endswith("_test.py")
                   or ".test." in name or ".spec." in name)
        if is_test and not include_tests:
            result["skipped_files"].append({"path": path, "reason": "测试文件；需要时使用 --include-tests。"})
            continue
        if name.endswith((".min.js", ".bundle.js")):
            result["skipped_files"].append({"path": path, "reason": "打包/压缩产物；请索引原始源码。"})
            continue
        suffix = Path(path).suffix.lower()
        if suffix not in {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            if suffix in {".go", ".rs", ".java", ".vue", ".php", ".rb", ".cs", ".cpp", ".c", ".swift"}:
                result["unsupported_files"].append(path)
            continue
        source = model.source_text(root, path, snapshot)
        if "text" not in source:
            result["skipped_files"].append({"path": path, "reason": source["skipped"]})
            continue
        if suffix != ".py":
            javascript.append({"path": path, "text": source["text"], "sha256": identity["sha256"]})
            continue
        try:
            nodes, edges, unresolved = python_symbols(path, source["text"], identity["sha256"])
            result["nodes"].extend(nodes)
            result["edges"].extend(edges)
            result["unresolved_calls"].extend(unresolved)
        except (SyntaxError, ValueError, RecursionError) as error:
            result["parse_errors"].append({"path": path, "reason": str(error)})
    if javascript:
        try:
            run = subprocess.run(["node", str(Path(__file__).with_name("index_typescript.mjs")),
                                  str(Path(typescript_root or root).resolve())],
                                 input=json.dumps(javascript), text=True, capture_output=True, timeout=60)
            if run.returncode:
                raise ValueError(run.stderr.strip() or "TypeScript parser unavailable")
            indexed = json.loads(run.stdout)
            for field in ("nodes", "edges", "unresolved_calls", "parse_errors"):
                result[field].extend(indexed[field])
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            result["unsupported_files"].extend(row["path"] for row in javascript)
            result["limitations"].append("JS/TS 未分析：" + str(error)[:500])
    return result


def merge_index(target, indexed):
    existing = {node["id"]: node for node in target["nodes"]}
    original_nodes = list(target["nodes"])
    signatures = Counter((n["sources"][0]["path"], n["symbol"]) for n in indexed["nodes"])
    aliases, missing, review_required = {}, [], []
    found = set()
    for node in indexed["nodes"]:
        source = node["sources"][0]
        old = existing.get(node["id"])
        if old is None and signatures[(source["path"], node["symbol"])] == 1:
            candidates = [candidate for candidate in original_nodes
                if str(candidate.get("symbol", "")).removesuffix("()") == node["symbol"]
                and any(s["path"] == source["path"] for s in candidate.get("sources", []))]
            old = candidates[0] if len(candidates) == 1 else None
        if old is None:
            added = copy.deepcopy(node)
            target["nodes"].append(added)
            existing[node["id"]] = added
            found.add(node["id"])
            continue
        aliases[node["id"]] = old["id"]
        found.add(old["id"])
        generated = old.get("index_origin") and old.get("description") == node["description"]
        if generated:
            old.update(copy.deepcopy(node), id=old["id"])
        elif old.get("sources") != node["sources"]:
            review_required.append({"id": old["id"], "suggested_sources": node["sources"],
                                    "reason": "保留已有业务说明与来源；复核后再更新，避免旧说明被标成当前。"})
    for node in target["nodes"]:
        if node.get("index_origin") and node["id"] not in found:
            missing.append(node["id"])
    # Refresh only parser-owned edges. Human-authored links and their evidence are preserved.
    target["edges"] = [edge for edge in target["edges"] if not edge.get("index_origin")]
    pairs = {(edge["from"], edge["to"], edge["kind"]) for edge in target["edges"]}
    for row in indexed["edges"]:
        edge = {**row, "from": aliases.get(row["from"], row["from"]), "to": aliases.get(row["to"], row["to"])}
        pair = (edge["from"], edge["to"], edge["kind"])
        if pair not in pairs:
            target["edges"].append(edge)
            pairs.add(pair)
    target["discovery"] = {**target.get("discovery", {}), "method": indexed["method"],
                          "missing_symbols": missing, "semantic_review_required": review_required,
                          **{key: indexed[key] for key in ("limitations", "unsupported_files", "parse_errors", "unresolved_calls")}}
    return target


def update_index(root, typescript_root=None, paths=None, include_tests=False):
    root = Path(root).resolve()
    config = root / ".project-check"
    inventory = pc.validate_inventory(pc.read_json(config / "inventory.json"))
    pc.require(config.resolve().is_relative_to(root), "Configuration escapes project")
    indexed = index_sources(root, typescript_root, paths, include_tests)
    path = config / "model.json"
    target = pc.read_json(path) if path.exists() else model.discover(root, inventory, root.name)
    model.validate_model(merge_index(target, indexed), inventory, root)
    model.write_json(config / "source-index.json", indexed)
    model.write_json(path, target)
    return {"index": str(config / "source-index.json"), "model": str(path),
            "nodes": len(indexed["nodes"]), "edges": len(indexed["edges"]),
            "unresolved_calls": len(indexed["unresolved_calls"]), "parse_errors": indexed["parse_errors"],
            "unsupported_files": indexed["unsupported_files"], "limitations": indexed["limitations"],
            "semantic_review_required": target["discovery"]["semantic_review_required"]}
