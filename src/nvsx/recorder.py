"""recorder — wrap `nvsx demo` in asciinema rec."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def record_demo(
    name: str,
    out: str = "./nvsx-demo.cast",
    target_node: Optional[str] = None,
    no_dwell: bool = False,
) -> None:
    if not shutil.which("asciinema"):
        print("ERROR: asciinema not installed. brew install asciinema", file=sys.stderr)
        raise SystemExit(2)

    out_path = Path(out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Find the nvsx launcher (sibling of this package)
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / "nvsx"
        if candidate.is_file() and (parent / "runbooks").is_dir():
            launcher = candidate
            break
    else:
        print("ERROR: could not locate nvsx launcher", file=sys.stderr)
        raise SystemExit(2)

    inner_cmd = [str(launcher), "demo", name]
    if target_node:
        inner_cmd += ["--target-node", target_node]
    if no_dwell:
        inner_cmd.append("--no-dwell")

    cmd = [
        "asciinema", "rec",
        "--overwrite",
        "--title", f"nvsx demo {name}",
        "--command", " ".join(inner_cmd),
        str(out_path),
    ]
    print(f"[recorder] writing to {out_path}", file=sys.stderr)
    print(f"[recorder] $ {' '.join(cmd)}", file=sys.stderr)
    rc = subprocess.call(cmd)
    if rc == 0:
        print(f"\n[recorder] done. play with: asciinema play {out_path}", file=sys.stderr)
    raise SystemExit(rc)
