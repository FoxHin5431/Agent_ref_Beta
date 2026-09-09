"""Verify release integrity: python -m agent_ref_validator.verify."""
from pathlib import Path
import hashlib
import json


def source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def release_files(package: Path) -> dict[str, str]:
    return {
        path.relative_to(package).as_posix(): source_hash(path)
        for path in sorted(package.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def verify_release(package: Path | None = None) -> dict:
    package = package or Path(__file__).resolve().parent
    manifest = json.loads((package / "release.json").read_text(encoding="utf-8"))
    actual = release_files(package)
    if actual != manifest["files"]:
        changed = sorted(key for key in set(actual) | set(manifest["files"])
                         if actual.get(key) != manifest["files"].get(key))
        raise RuntimeError(f"Validator release drift: {', '.join(changed)}. Update the canonical source and run tools/sync_validator.py.")
    return manifest


if __name__ == "__main__":
    release = verify_release()
    print(f"Validator {release['version']} verified: {release['files']['core.py']}")
