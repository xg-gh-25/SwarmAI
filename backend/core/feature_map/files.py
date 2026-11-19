"""The feature folder: its files, their schemas, where the folder lives.

A feature folder holds ``features.yaml`` (the feature knowledge), ``targets/<id>.yaml``
(how to start each runnable part of the product), ``specs/*.spec.ts`` (standard
Playwright tests) and ``fixtures/`` (stub bodies, start-state data). Files are read
with a strict schema: an unknown key, a missing key or a newer schema major is refused,
naming the file and the key (``FeatureMapError``). Nothing in a feature file is ever
run as a command except the argv lists of a target, and those only by the sandbox.

Where the folder lives (design §4.4) is decided once per binding and recorded on it in
``bindings.yaml`` as ``features: repo`` or ``features: brain``:

- ``repo``  — ``<checkout>/.swarmai/features/`` (a working checkout: ``local`` or
  ``repos_dir``), so specs change in the same commit as the UI;
- ``brain`` — ``<Brain>/.artifacts/feature-map/<repo>/`` (a managed ``mirror``, or the
  person asked to keep it out of the repo).

``location()`` is the one resolver every reader uses; ``choose_location()`` records the
first value by that rule. Exports: FeatureMapError, Feature, Target, FeatureMap,
load_folder, location, choose_location, content_hash, REPO_FOLDER.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

SCHEMA_MAJOR = 1
REPO_FOLDER = ".swarmai/features"
BRAIN_FOLDER = ".artifacts/feature-map"
TARGET_KINDS = ("web", "command")
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


class FeatureMapError(ValueError):
    """A feature folder file is invalid: names the file and the key."""

    def __init__(self, file: str, key: str, problem: str):
        self.file, self.key, self.problem = file, key, problem
        super().__init__(f"{file}: {key}: {problem}")


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    target: str
    reach: str = ""
    expect: str = ""
    spec: str | None = None              # folder-relative spec path (web walks)
    run: tuple[str, ...] | None = None   # argv of a command walk
    notes: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Service:
    id: str
    argv: tuple[str, ...]
    ready: str


@dataclass(frozen=True)
class StartState:
    setup: tuple[tuple[str, ...], ...] = ()
    fixture: str | None = None
    to: str | None = None


@dataclass(frozen=True)
class Target:
    id: str
    kind: str
    root: str = ""
    paths: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    prepare: tuple[tuple[str, ...], ...] = ()
    services: tuple[Service, ...] = ()
    base_url: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    start_states: Mapping[str, StartState] = field(default_factory=dict)
    teardown: tuple[tuple[str, ...], ...] = ()
    locale: str = "en-US"
    ready_timeout_s: int = 120


@dataclass(frozen=True)
class FeatureMap:
    folder: Path
    features: tuple[Feature, ...]
    targets: Mapping[str, Target]

    def feature(self, feature_id: str) -> Feature | None:
        return next((f for f in self.features if f.id == feature_id), None)


# ── schema helpers ───────────────────────────────────────────────────────────

def _keys(obj: Any, file: str, where: str, required: set[str], optional: set[str]) -> dict:
    if not isinstance(obj, Mapping):
        raise FeatureMapError(file, where or "(root)", "must be a mapping")
    unknown = sorted(set(obj) - required - optional)
    if unknown:
        raise FeatureMapError(file, f"{where}.{unknown[0]}" if where else unknown[0],
                              "unknown key")
    missing = sorted(k for k in required if k not in obj)
    if missing:
        raise FeatureMapError(file, f"{where}.{missing[0]}" if where else missing[0],
                              "required key missing")
    return dict(obj)


def _str(value: Any, file: str, key: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise FeatureMapError(file, key, "must be a non-empty string")
    return value


def _ident(value: Any, file: str, key: str) -> str:
    text = _str(value, file, key)
    if len(text) > 80 or not set(text) <= _ID_CHARS:
        raise FeatureMapError(file, key, "must be lowercase letters, digits, - or _ (≤80)")
    return text


def _argv(value: Any, file: str, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(a, str) for a in value):
        raise FeatureMapError(file, key, "must be an argv list of strings (never a shell string)")
    return tuple(value)


def _argvs(value: Any, file: str, key: str) -> tuple[tuple[str, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise FeatureMapError(file, key, "must be a list of argv lists")
    return tuple(_argv(v, file, f"{key}[{i}]") for i, v in enumerate(value))


def _strs(value: Any, file: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise FeatureMapError(file, key, "must be a list of strings")
    return tuple(value)


def _rel(value: str, file: str, key: str) -> str:
    """A folder- or repo-relative path that stays inside its base."""
    p = Path(value)
    if p.is_absolute() or ".." in p.parts:
        raise FeatureMapError(file, key, "must be a relative path inside the folder")
    return p.as_posix()


def _read_yaml(path: Path, folder: Path) -> Any:
    rel = path.relative_to(folder).as_posix() if path.is_relative_to(folder) else str(path)
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise FeatureMapError(rel, "(file)", f"unreadable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise FeatureMapError(rel, "(file)", f"not valid YAML: {exc}") from exc


def _schema(value: Any, file: str) -> None:
    if not isinstance(value, int) or value < 1:
        raise FeatureMapError(file, "schema", "must be a positive integer")
    if value > SCHEMA_MAJOR:
        raise FeatureMapError(file, "schema", f"is {value}, newer than this SwarmAI reads "
                              f"({SCHEMA_MAJOR}); update SwarmAI")


def _parse_target(obj: Any, file: str) -> Target:
    d = _keys(obj, file, "", {"id", "kind"},
              {"root", "paths", "needs", "prepare", "services", "base_url", "env",
               "start_states", "teardown", "locale", "ready_timeout_s"})
    tid = _ident(d["id"], file, "id")
    kind = _str(d["kind"], file, "kind")
    if kind not in TARGET_KINDS:
        raise FeatureMapError(file, "kind", f"must be one of {', '.join(TARGET_KINDS)}")
    root = d.get("root", "") or ""
    if root:
        root = _rel(_str(root, file, "root"), file, "root").rstrip("/") + "/"
    services = []
    for i, s in enumerate(d.get("services") or []):
        sd = _keys(s, file, f"services[{i}]", {"id", "argv", "ready"}, set())
        services.append(Service(_ident(sd["id"], file, f"services[{i}].id"),
                                _argv(sd["argv"], file, f"services[{i}].argv"),
                                _str(sd["ready"], file, f"services[{i}].ready")))
    states: dict[str, StartState] = {}
    raw_states = d.get("start_states") or {}
    if not isinstance(raw_states, Mapping):
        raise FeatureMapError(file, "start_states", "must be a mapping")
    for name, st in raw_states.items():
        sname = _ident(name, file, f"start_states.{name}")
        sd = _keys(st or {}, file, f"start_states.{sname}", set(), {"setup", "fixture", "to"})
        fixture = sd.get("fixture")
        states[sname] = StartState(
            _argvs(sd.get("setup"), file, f"start_states.{sname}.setup"),
            _rel(_str(fixture, file, f"start_states.{sname}.fixture"), file,
                 f"start_states.{sname}.fixture") if fixture else None,
            _str(sd["to"], file, f"start_states.{sname}.to") if sd.get("to") else None)
    states.setdefault("default", StartState())
    env = d.get("env") or {}
    if not isinstance(env, Mapping) or not all(isinstance(k, str) and isinstance(v, str)
                                               for k, v in env.items()):
        raise FeatureMapError(file, "env", "must map names to strings")
    timeout = d.get("ready_timeout_s", 120)
    if not isinstance(timeout, int) or not 1 <= timeout <= 1800:
        raise FeatureMapError(file, "ready_timeout_s", "must be an integer from 1 to 1800")
    return Target(
        id=tid, kind=kind, root=root,
        paths=tuple(_rel(p, file, "paths") for p in _strs(d.get("paths"), file, "paths")),
        needs=tuple(_ident(n, file, "needs") for n in _strs(d.get("needs"), file, "needs")),
        prepare=_argvs(d.get("prepare"), file, "prepare"),
        services=tuple(services),
        base_url=_str(d["base_url"], file, "base_url") if d.get("base_url") else None,
        env=dict(env), start_states=states,
        teardown=_argvs(d.get("teardown"), file, "teardown"),
        locale=_str(d.get("locale", "en-US"), file, "locale"),
        ready_timeout_s=timeout)


def _parse_feature(obj: Any, file: str, i: int, folder: Path) -> Feature:
    where = f"features[{i}]"
    d = _keys(obj, file, where, {"id", "title", "target"},
              {"reach", "expect", "spec", "run", "notes"})
    has_spec, has_run = d.get("spec") is not None, d.get("run") is not None
    if has_spec == has_run:
        raise FeatureMapError(file, f"{where}.spec", "give exactly one of spec or run")
    spec = None
    if has_spec:
        spec = _rel(_str(d["spec"], file, f"{where}.spec"), file, f"{where}.spec")
        if not (folder / spec).is_file():
            raise FeatureMapError(file, f"{where}.spec", f"no such file: {spec}")
    return Feature(
        id=_ident(d["id"], file, f"{where}.id"),
        title=_str(d["title"], file, f"{where}.title"),
        target=_ident(d["target"], file, f"{where}.target"),
        reach=_str(d.get("reach", ""), file, f"{where}.reach", empty=True),
        expect=_str(d.get("expect", ""), file, f"{where}.expect", empty=True),
        spec=spec,
        run=_argv(d["run"], file, f"{where}.run") if has_run else None,
        notes=_strs(d.get("notes"), file, f"{where}.notes"),
        raw=dict(d))


def load_folder(folder: Path) -> FeatureMap:
    """Read and validate a feature folder; raises FeatureMapError naming file and key."""
    folder = Path(folder)
    path = folder / "features.yaml"
    if not path.is_file():
        raise FeatureMapError("features.yaml", "(file)", "missing")
    doc = _keys(_read_yaml(path, folder), "features.yaml", "", {"schema", "features"}, set())
    _schema(doc["schema"], "features.yaml")
    if not isinstance(doc["features"], list):
        raise FeatureMapError("features.yaml", "features", "must be a list")
    targets: dict[str, Target] = {}
    tdir = folder / "targets"
    for tpath in sorted(tdir.glob("*.yaml")) if tdir.is_dir() else ():
        rel = tpath.relative_to(folder).as_posix()
        target = _parse_target(_read_yaml(tpath, folder), rel)
        if target.id != tpath.stem:
            raise FeatureMapError(rel, "id", f"must equal the file name ({tpath.stem})")
        targets[target.id] = target
    features, seen = [], set()
    for i, obj in enumerate(doc["features"]):
        f = _parse_feature(obj, "features.yaml", i, folder)
        if f.id in seen:
            raise FeatureMapError("features.yaml", f"features[{i}].id", f"duplicate id {f.id}")
        if f.target not in targets:
            raise FeatureMapError("features.yaml", f"features[{i}].target",
                                  f"no target {f.target} in targets/")
        if f.spec and targets[f.target].kind != "web":
            raise FeatureMapError("features.yaml", f"features[{i}].spec",
                                  f"target {f.target} is not a web target")
        seen.add(f.id)
        features.append(f)
    for t in targets.values():
        for need in t.needs:
            if need not in targets:
                raise FeatureMapError(f"targets/{t.id}.yaml", "needs", f"no target {need}")
    return FeatureMap(folder, tuple(features), targets)


def content_hash(fmap: FeatureMap, feature: Feature) -> str:
    """The walk's identity in the ledger: its feature entry and its spec file. Editing
    either starts the walk's history over (design §7)."""
    h = hashlib.sha256(json.dumps(feature.raw, sort_keys=True, ensure_ascii=False).encode())
    if feature.spec:
        try:
            h.update((fmap.folder / feature.spec).read_bytes())
        except OSError:
            h.update(b"(spec unreadable)")
    return h.hexdigest()[:16]


# ── where the folder lives ───────────────────────────────────────────────────

def _binding_doc(project_dir: Path) -> tuple[Path, dict]:
    path = Path(project_dir) / "bindings.yaml"
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, yaml.YAMLError):
        loaded = {}
    return path, loaded if isinstance(loaded, dict) else {}


def recorded(project_dir: Path, repo: str) -> str | None:
    """The ``features`` value recorded on *repo*'s binding, or None."""
    _path, doc = _binding_doc(project_dir)
    for b in doc.get("bindings") or []:
        if isinstance(b, Mapping) and b.get("repo") == repo:
            value = b.get("features")
            return value if value in ("repo", "brain") else None
    return None


def brain_folder(project_dir: Path, repo: str) -> Path:
    return Path(project_dir) / BRAIN_FOLDER / repo


def location(project_dir: Path | None, repo: str | None, root: Path) -> tuple[Path | None, str]:
    """The feature folder for *repo* checked out at *root*: ``(folder, mode)`` where
    mode is ``repo`` / ``brain`` / ``none``. The recorded value decides; with none
    recorded, an existing folder does (repo first). The one resolver every reader uses."""
    root = Path(root)
    value = recorded(project_dir, repo) if project_dir and repo else None
    in_repo, in_brain = root / REPO_FOLDER, (brain_folder(project_dir, repo)
                                             if project_dir and repo else None)
    if value == "repo":
        return (in_repo, "repo") if (in_repo / "features.yaml").is_file() else (None, "none")
    if value == "brain":
        return ((in_brain, "brain") if in_brain and (in_brain / "features.yaml").is_file()
                else (None, "none"))
    if (in_repo / "features.yaml").is_file():
        return in_repo, "repo"
    if in_brain is not None and (in_brain / "features.yaml").is_file():
        return in_brain, "brain"
    return None, "none"


def rule(project_dir: Path, repo: str) -> str:
    """The first location by checkout kind: ``brain`` for a managed mirror, ``repo``
    for a working checkout (``local``, ``repos_dir``), and ``repo`` when the kind cannot
    be read (an unbound checkout is the person's own)."""
    try:
        from core.repo_placement import placement
        kind = (placement(Path(project_dir)).get(repo) or {}).get("checkout")
    except Exception:  # noqa: BLE001 — no placement = a working checkout
        kind = None
    return "brain" if kind == "mirror" else "repo"


def choose_location(project_dir: Path, repo: str, root: Path, *,
                    keep_out_of_repo: bool = False) -> tuple[Path, str]:
    """The folder to write the first feature in: the recorded value, else the rule (or
    ``brain`` when the person asked), recorded on the binding now."""
    value = recorded(project_dir, repo) or ("brain" if keep_out_of_repo else rule(project_dir, repo))
    if recorded(project_dir, repo) is None:
        _record(project_dir, repo, value)
    folder = Path(root) / REPO_FOLDER if value == "repo" else brain_folder(project_dir, repo)
    return folder, value


def _record(project_dir: Path, repo: str, value: str) -> None:
    """Write ``features: <value>`` on *repo*'s binding (backed up, atomic). A repo with
    no binding gets nothing recorded: the folder rule still applies when read."""
    path, doc = _binding_doc(project_dir)
    bindings = doc.get("bindings") or []
    target = next((b for b in bindings if isinstance(b, dict) and b.get("repo") == repo), None)
    if target is None:
        return
    target["features"] = value
    if path.is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".bindings-", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, path)
