"""CLI entry point for the local Human Gold Review Tool."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

from ..hints.providers import OllamaTranslationHintProvider, SimAlignTokenAlignmentProvider
from ..hints.service import HintService
from .server import ReviewHTTPServer
from .service import ReviewError, ReviewSession


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Open a local human-only Gold Draft review UI")
    result.add_argument("gold", type=Path, help="Path to a compatible Gold Draft JSON")
    result.add_argument("--root", type=Path, default=Path.cwd(), help="Project root used for stored source paths")
    result.add_argument("--host", default="127.0.0.1", help="Local bind address (default: 127.0.0.1)")
    result.add_argument("--port", type=int, default=8765, help="Local port (default: 8765; 0 chooses a free port)")
    result.add_argument("--no-browser", action="store_true", help="Do not open the system browser automatically")
    result.add_argument("--hint-translation-model", default="qwen3:8b", help="Local Ollama ZH-to-EN model")
    result.add_argument("--hint-alignment-model", default="bert", help="Local SimAlign model alias or ID")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        session = ReviewSession(args.gold, args.root)
        hints = HintService(
            session.root / "data" / "cache" / "v2" / "hints",
            translation_provider=OllamaTranslationHintProvider(model=args.hint_translation_model),
            alignment_provider=SimAlignTokenAlignmentProvider(model=args.hint_alignment_model),
        )
        server = ReviewHTTPServer((args.host, args.port), session, hint_service=hints)
    except (OSError, ReviewError, ValueError) as error:
        print(f"Cannot start Gold review: {error}", file=sys.stderr)
        return 2
    url = f"http://{args.host}:{server.server_port}/"
    print(f"Human Gold Review Tool: {url}")
    print(f"Gold: {session.gold_path}")
    print("Press Ctrl+C to stop. Changes are saved after each confirmed action.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nReview server stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
