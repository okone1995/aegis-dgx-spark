from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements-lock.txt"
SKILLS = {"aegis-evolve": "aegis_evolve.py", "aegis-hunt": "aegis_hunt.py",
          "aegis-jevtrain": "aegis_jevtrain.py", "aegis-self-repair": "aegis_skill.py"}


def package_name(requirement: str) -> str:
    return requirement.split("==", 1)[0].strip()


def dependency_check() -> dict:
    missing = []
    checked = []
    if not LOCK.is_file():
        return {"status": "missing", "lock_file": str(LOCK), "missing": ["requirements-lock.txt"]}
    for raw in LOCK.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = package_name(line)
        expected = line.split("==", 1)[1].strip() if "==" in line else None
        checked.append(name)
        try:
            installed = importlib.metadata.version(name)
            if expected and installed != expected:
                missing.append(f"{name}=={expected} (installed {installed})")
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return {
        "status": "ok" if not missing else "needs_configuration",
        "lock_file": str(LOCK.relative_to(ROOT)),
        "checked": checked,
        "missing": missing,
    }


def skill_check() -> dict:
    result = {}
    for name in SKILLS:
        folder = ROOT / "skills" / name
        required = [folder / "SKILL.md", folder / "scripts" / SKILLS[name]]
        absent = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
        result[name] = {
            "status": "ok" if not absent else "needs_configuration",
            "entrypoint": str((folder / "SKILL.md").relative_to(ROOT)),
            "scripts": [str(path.relative_to(ROOT)) for path in required[1:] if path.is_file()],
            "missing": absent,
        }
    return {"status": "ok" if all(v["status"] == "ok" for v in result.values()) else "needs_configuration",
            "skills": result}


def command_parts(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def profile_check() -> dict:
    try:
        import yaml
    except ImportError:
        return {"status": "needs_configuration", "profiles": [], "missing": ["PyYAML"]}

    profiles = []
    all_missing: list[str] = []
    interpreters: set[str] = {"python"}
    for profile_path in sorted((ROOT / "targets").rglob("profile.yaml")):
        try:
            data = yaml.safe_load(profile_path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, yaml.YAMLError) as exc:
            profiles.append({"profile": str(profile_path.relative_to(ROOT)), "status": "needs_configuration",
                             "error": str(exc), "missing": []})
            all_missing.append(str(profile_path.relative_to(ROOT)))
            continue

        refs: list[str] = []
        paths = data.get("paths", {}) or {}
        target_root = paths.get("root")
        if target_root:
            refs.append(str(target_root).replace("\\", "/"))
            for source in paths.get("canonical_sources", []) or []:
                refs.append(f"{str(target_root).rstrip('/')}/{str(source).lstrip('/')}".replace("\\", "/"))
        commands: list[str] = []
        for key, value in (data.get("gates", {}) or {}).items():
            if isinstance(value, str):
                commands.append(value)
            elif key == "business" and isinstance(value, dict):
                commands.extend(v for v in value.values() if isinstance(v, str))
        deploy = paths.get("deploy_cmd")
        if deploy:
            commands.append(str(deploy))
        for command in commands:
            parts = command_parts(command)
            if not parts:
                continue
            executable = Path(parts[0]).name.lower()
            if executable in {"bash", "sh", "php"}:
                interpreters.add(executable)
            if "pytest" in parts:
                index = parts.index("pytest")
                if index + 1 < len(parts) and not parts[index + 1].startswith("-"):
                    refs.append(parts[index + 1].replace("\\", "/"))
            for token in parts[1:]:
                normalized = token.strip("\"'").replace("\\", "/")
                if normalized.startswith("{") or "{" in normalized or normalized.startswith("-"):
                    continue
                if normalized.endswith((".py", ".sh", ".php", ".yaml", ".yml")):
                    refs.append(normalized)

        missing = []
        for ref in dict.fromkeys(refs):
            if not (ROOT / ref).exists():
                missing.append(ref)
        all_missing.extend(missing)
        profiles.append({"profile": str(profile_path.relative_to(ROOT)),
                         "status": "ok" if not missing else "needs_configuration",
                         "references": list(dict.fromkeys(refs)), "missing": missing})

    # The interpreter running Doctor satisfies the Python requirement even if
    # Windows does not expose a separate `python` command on PATH.
    missing_interpreters = sorted(
        name for name in interpreters
        if name != "python" and not shutil.which(name)
        and not (name == "php" and os.environ.get("AEGIS_PHP_BIN")
                 and Path(os.environ["AEGIS_PHP_BIN"]).is_file())
    )
    return {"status": "ok" if not all_missing and not missing_interpreters else "needs_configuration",
            "profiles": profiles,
            "required_interpreters": sorted(interpreters),
            "missing_interpreters": missing_interpreters,
            "missing_references": sorted(set(all_missing))}


def local_configuration_check() -> dict:
    configured_registry = os.environ.get("AEGIS_TARGETS_FILE", "").strip()
    registry = Path(configured_registry).expanduser() if configured_registry else ROOT / "targets" / "_operator_targets.json"
    if not registry.is_absolute():
        registry = ROOT / registry
    registry_ok = registry.is_file()
    secrets = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("secrets*.env") if p.is_file())
    missing = [] if registry_ok else ["operator target registry (set AEGIS_TARGETS_FILE or create targets/_operator_targets.json)"]
    if registry_ok:
        try:
            document = json.loads(registry.read_text(encoding="utf-8-sig"))
            targets = document.get("targets")
            if document.get("schema_version") != 1 or not isinstance(targets, list) or not targets:
                raise ValueError("invalid target registry schema")
            identifiers = set()
            for target in targets:
                if not isinstance(target, dict):
                    raise ValueError("invalid target entry")
                target_id = target.get("target_id")
                if not isinstance(target_id, str) or not target_id or target_id in identifiers:
                    raise ValueError("missing or duplicate target id")
                identifiers.add(target_id)
                source = target.get("source_root")
                if not isinstance(source, str) or not source or not (ROOT / source).is_dir():
                    missing.append(f"registered source directory unavailable: {target_id}")
                if not target.get("target_base") or not target.get("case_id"):
                    missing.append(f"incomplete target configuration: {target_id}")
        except (OSError, ValueError, AttributeError, TypeError):
            missing.append("operator target registry is malformed")
    status = "ok" if not missing else "needs_configuration"
    return {"status": status, "target_registry": str(registry), "target_registry_exists": registry_ok,
            "local_secret_files": secrets,
            "note": "Secret files are optional for local/offline checks and required only by configured external services." if not secrets else None,
            "missing": missing}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Aegis development environment check")
    parser.add_argument("--offline", action="store_true", help="check only locked development dependencies")
    args = parser.parse_args()
    checks = {"dependencies": dependency_check()}
    if not args.offline:
        checks["skills"] = skill_check()
        checks["target_profiles"] = profile_check()
        checks["local_configuration"] = local_configuration_check()
    needs_configuration = any(
        check.get("status") != "ok"
        for check in checks.values()
    )
    result = {"status": "needs_configuration" if needs_configuration else "ok",
              "mode": "offline" if args.offline else "full",
              "python": sys.version.split()[0], "checks": checks}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
