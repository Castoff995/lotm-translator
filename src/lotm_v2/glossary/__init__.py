"""Versioned human-curated terminology artifact, independent from Gold."""

from .model import Glossary, GlossaryEntry, GlossaryStatus
from .service import GlossaryDuplicateError, GlossaryService

__all__ = ["Glossary", "GlossaryDuplicateError", "GlossaryEntry", "GlossaryService", "GlossaryStatus"]
