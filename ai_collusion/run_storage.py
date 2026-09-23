"""Atomic run artifacts and a shared resume contract for transcript runners."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


def stable_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_resume(out: Path, experiment_sha256: str, n_samples: int) -> None:
    manifest = out / "manifest.json"
    if manifest.exists():
        try:
            previous = json.loads(manifest.read_text())
            matches = (isinstance(previous, dict)
                       and previous.get("experiment_sha256") == experiment_sha256
                       and type(previous.get("n_samples")) is int)
        except (ValueError, UnicodeError):
            matches = False
        if not matches:
            raise ValueError("cannot resume: experimental inputs changed or existing manifest lacks valid provenance; use a new run id")
        if n_samples < previous["n_samples"]:
            raise ValueError("cannot resume with a smaller sample count")
    elif any(out.glob("*.json")):
        raise ValueError("cannot resume transcript files without a provenance manifest; use a new run id")


def completed_record(path: Path, expected: dict, *, episode: bool = False,
                     retry_incomplete: bool = True) -> bool:
    """Skip only complete matching records; preserve incomplete files before retrying."""
    if not path.exists():
        return False
    try:
        record = json.loads(path.read_text())
        required = {*expected, "context", "response", "error"}
        complete = isinstance(record, dict) and required <= record.keys()
        if complete and episode:
            body = record.get("episode")
            complete = (isinstance(body, dict) and isinstance(body.get("turns"), list)
                        and bool(body.get("end_reason")))
    except (ValueError, UnicodeError):
        complete = False
    if not complete:
        if not retry_incomplete:
            raise ValueError(f"Partial shared group: incomplete transcript {path}; preserve it and use a new run id")
        saved = path.with_name(f"{path.name}.incomplete-{uuid.uuid4().hex}")
        path.replace(saved)
        print(f"Retrying incomplete transcript; preserved {saved}", file=sys.stderr)
        return False
    if any(record[key] != value for key, value in expected.items()):
        raise ValueError(f"cannot resume: {path} does not match the requested trial provenance")
    return True
