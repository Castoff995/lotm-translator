"""Launch the dedicated local Pairwise Gold Review Tool."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

from .server import PairwiseReviewHTTPServer
from .service import PairwiseReviewError, PairwiseReviewWorkspace


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Open the independent Pairwise Gold Reviewer")
    value.add_argument("pairwise_gold", type=Path)
    value.add_argument("--bootstrap-from", type=Path)
    value.add_argument("--root", type=Path, default=Path.cwd())
    value.add_argument("--host", default="127.0.0.1")
    value.add_argument("--port", type=int, default=8766)
    value.add_argument("--no-browser", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        workspace = PairwiseReviewWorkspace(args.pairwise_gold, args.root, args.bootstrap_from)
        server = PairwiseReviewHTTPServer((args.host, args.port), workspace)
    except (OSError, ValueError, PairwiseReviewError) as error:
        print(f"Cannot start Pairwise Gold review: {error}", file=sys.stderr)
        return 2
    url = f"http://{args.host}:{server.server_port}/"
    print(f"Pairwise Gold Review Tool: {url}")
    print(f"Pairwise Gold: {workspace.gold_path}")
    if workspace.bootstrap:
        print("TRILINGUAL BOOTSTRAP — NOT PAIRWISE TRUTH")
    if workspace.read_only:
        print("Confirmed Pairwise Gold opened read-only.")
    else:
        print(f"Pairwise Review Session: {workspace.session_document.session_id}")
        print("Human actions autosave only the ignored session; Publish writes draft Gold only.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPairwise Review server stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
