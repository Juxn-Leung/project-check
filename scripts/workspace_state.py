"""Content identities for source snapshots; never store source contents or secrets."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

GENERATED = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache',
             '.mypy_cache', '.ruff_cache', 'test-results', 'playwright-report', 'coverage',
             'dist', 'build', '.next', '.DS_Store'}
PC_OUTPUTS = {'runs', 'views', 'changes'}
PC_INPUTS = {'project.json', 'inventory.json', 'model.json'}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def git(root, *args):
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    except FileNotFoundError:
        return None
    return result.stdout if result.returncode == 0 else None


def excluded(name):
    parts = Path(name).parts
    return (name == '.project-check/CHECKLIST.md' or any(part in GENERATED for part in parts) or name.endswith(('.pyc', '.pyo')) or
            (len(parts) > 1 and parts[0] == '.project-check' and parts[1] in PC_OUTPUTS))


def capture(root):
    root = Path(root).resolve()
    git_root = git(root, 'rev-parse', '--show-toplevel')
    is_git = git_root is not None
    tracked = set()
    if is_git:
        raw = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard')
        if raw is None:
            raise ValueError('Cannot enumerate Git working tree')
        names = {os.fsdecode(name) for name in raw.split(b'\0') if name}
        tracked_raw = git(root, 'ls-files', '-z', '--cached')
        if tracked_raw is None:
            raise ValueError('Cannot enumerate tracked source files')
        tracked = {os.fsdecode(name) for name in tracked_raw.split(b'\0') if name}
    else:
        names = set()
        for directory, dirs, files in os.walk(root, followlinks=False):
            relative = Path(directory).relative_to(root)
            dirs[:] = [d for d in dirs if not excluded(str(relative / d))]
            # os.walk lists directory symlinks in dirs but does not visit them.
            names.update(str(relative / d) for d in dirs if (Path(directory) / d).is_symlink())
            names.update(str(relative / f) for f in files)
    # Acceptance policy and mappings remain inputs even when a project ignores
    # the whole .project-check directory to keep run artifacts out of Git.
    names.update(str(Path('.project-check') / name) for name in PC_INPUTS
                 if (root / '.project-check' / name).exists() or (root / '.project-check' / name).is_symlink())
    files = {}
    for name in sorted(names):
        parts = Path(name).parts
        if name == '.project-check/CHECKLIST.md' or (len(parts) > 1 and parts[0] == '.project-check' and parts[1] in PC_OUTPUTS) or (name not in tracked and excluded(name)):
            continue
        if Path(name).is_absolute() or '..' in parts:
            raise ValueError(f'Source path escapes project root: {name}')
        # Git can still list tracked descendants after their directory was
        # replaced by a symlink. Record the link itself, never read through it.
        for index in range(1, len(parts)):
            ancestor = Path(*parts[:index])
            path = root / ancestor
            if path.is_symlink():
                files[str(ancestor)] = {'sha256': digest(os.fsencode(os.readlink(path))), 'kind': 'symlink'}
                break
        else:
            ancestor = None
        if ancestor is not None:
            continue
        path = root / name
        if path.is_symlink():
            files[name] = {'sha256': digest(os.fsencode(os.readlink(path))), 'kind': 'symlink'}
        elif path.is_file():
            files[name] = {'sha256': digest(path.read_bytes()), 'kind': 'file',
                           'executable': bool(path.stat().st_mode & 0o111)}
        elif not path.exists():
            files[name] = {'kind': 'deleted'}
        elif path.is_dir():
            raise ValueError(f'Nested Git repository/submodule needs explicit analysis: {name}')
    revision = git(root, 'rev-parse', 'HEAD') if is_git else None
    state = {'version': 1, 'revision': os.fsdecode(revision).strip() if revision else 'non-git', 'files': files}
    state['fingerprint'] = digest(json.dumps(state, sort_keys=True, ensure_ascii=True).encode())
    return state


def changes(before, after):
    rows = []
    for path in sorted(set(before['files']) | set(after['files'])):
        old, new = before['files'].get(path), after['files'].get(path)
        if old != new:
            kind = 'deleted' if new is None or new.get('kind') == 'deleted' else 'added' if old is None or old.get('kind') == 'deleted' else 'modified'
            rows.append({'path': path, 'change': kind})
    return rows


def current(snapshot, root):
    observed = capture(root)
    return {'current': snapshot.get('fingerprint') == observed['fingerprint'],
            'expected': snapshot.get('fingerprint'), 'actual': observed['fingerprint'],
            'changes': changes(snapshot, observed)}
