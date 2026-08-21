"""Lazy local provider abstractions and baseline implementations."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from .model import HintToken, LexicalAlignment


class HintUnavailable(RuntimeError):
    """An optional local provider/model is unavailable; Gold review may continue."""


class TranslationHintProvider(Protocol):
    @property
    def provider_id(self) -> str: ...
    def translate(self, text: str, source_language: str, target_language: str) -> str: ...


class ChineseSegmentationProvider(Protocol):
    @property
    def provider_id(self) -> str: ...
    def segment(self, text: str) -> tuple[HintToken, ...]: ...


class TokenAlignmentProvider(Protocol):
    @property
    def provider_id(self) -> str: ...
    def align(
        self, zh_text: str, en_text: str,
        zh_tokens: tuple[HintToken, ...], en_tokens: tuple[HintToken, ...],
    ) -> tuple[LexicalAlignment, ...]: ...


class RussianTermSuggestionProvider(Protocol):
    @property
    def provider_id(self) -> str: ...
    def suggest(self, zh_term: str, confirmed_context: object) -> str | None: ...


@dataclass
class OllamaTranslationHintProvider:
    model: str = "qwen3:8b"
    endpoint: str = "http://127.0.0.1:11434/api/generate"
    timeout_seconds: int = 180
    prompt_version: str = "zh-en-literal-v1"

    @property
    def provider_id(self) -> str:
        return f"ollama:{self.model}:{self.prompt_version}"

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        if (source_language, target_language) != ("zh", "en"):
            raise HintUnavailable("The baseline translation provider supports only ZH→EN")
        prompt = (
            "Translate the following Chinese fiction paragraph into clear, literal English. "
            "Preserve names, uncertainty, punctuation, and meaning. Do not explain, summarize, "
            "add notes, or use Markdown. Return only the English translation.\n\n"
            f"CHINESE:\n{text}"
        )
        body = json.dumps({
            "model": self.model, "prompt": prompt, "stream": False, "think": False,
            "keep_alive": "5m", "options": {"temperature": 0, "seed": 7},
        }).encode("utf-8")
        request = Request(self.endpoint, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read())
        except (OSError, URLError, json.JSONDecodeError) as error:
            raise HintUnavailable(f"Local Ollama translation unavailable: {error}") from error
        translation = str(result.get("response", "")).strip()
        if not translation:
            raise HintUnavailable("Local Ollama returned an empty translation hint")
        return translation


class JiebaChineseSegmenter:
    provider_id = "jieba:0.42.1:accurate-hmm-off-v1"

    def __init__(self) -> None:
        self._jieba = None

    def _load(self):
        if self._jieba is None:
            try:
                import jieba
            except ImportError as error:
                raise HintUnavailable("Chinese segmentation unavailable: install optional package 'jieba'") from error
            self._jieba = jieba
        return self._jieba

    def segment(self, text: str) -> tuple[HintToken, ...]:
        jieba = self._load()
        raw = jieba.tokenize(text, mode="default", HMM=False)
        tokens: list[HintToken] = []
        for surface, start, end in raw:
            if surface.strip():
                tokens.append(HintToken(len(tokens), surface, start, end))
        return tuple(tokens)


class RegexEnglishSegmenter:
    provider_id = "regex-en:unicode-word-v1"
    _pattern = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*|[^\w\s]", re.UNICODE)

    def segment(self, text: str) -> tuple[HintToken, ...]:
        return tuple(
            HintToken(index, match.group(0), match.start(), match.end())
            for index, match in enumerate(self._pattern.finditer(text))
        )


@dataclass
class SimAlignTokenAlignmentProvider:
    model: str = "bert"
    device: str = "cpu"
    layer: int = 8
    algorithm_version: str = "itermax-word-score-v1"

    def __post_init__(self) -> None:
        self._aligner = None

    @property
    def provider_id(self) -> str:
        resolved = {"bert": "bert-base-multilingual-cased", "xlmr": "xlm-roberta-base"}.get(self.model, self.model)
        return f"simalign:0.4:{resolved}:layer{self.layer}:{self.algorithm_version}"

    def _load(self):
        if self._aligner is None:
            try:
                from simalign import SentenceAligner
            except ImportError as error:
                raise HintUnavailable("Lexical alignment unavailable: install optional package 'simalign'") from error
            try:
                self._aligner = SentenceAligner(
                    model=self.model, token_type="word", matching_methods="i",
                    device=self.device, layer=self.layer,
                )
            except Exception as error:
                raise HintUnavailable(f"SimAlign model unavailable: {error}") from error
        return self._aligner

    def align(
        self, zh_text: str, en_text: str,
        zh_tokens: tuple[HintToken, ...], en_tokens: tuple[HintToken, ...],
    ) -> tuple[LexicalAlignment, ...]:
        if not zh_tokens or not en_tokens:
            return ()
        aligner = self._load()
        source = [token.surface for token in zh_tokens]
        target = [token.surface for token in en_tokens]
        try:
            source_pieces = [aligner.embed_loader.tokenizer.tokenize(word) for word in source]
            target_pieces = [aligner.embed_loader.tokenizer.tokenize(word) for word in target]
            flattened = [[piece for word in sentence for piece in word] for sentence in (source_pieces, target_pieces)]
            vectors = aligner.embed_loader.get_embed_list([source, target]).cpu().detach().numpy()
            vectors = [vectors[index, :len(flattened[index])] for index in (0, 1)]
            source_vectors, target_vectors = aligner.average_embeds_over_words(
                vectors, [source_pieces, target_pieces],
            )
            similarity = aligner.get_similarity(source_vectors, target_vectors)
            matrix = aligner.iter_max(similarity)
            pairs = [
                (i, j, max(0.0, min(1.0, (float(similarity[i, j]) + 1.0) / 2.0)))
                for i in range(len(source)) for j in range(len(target))
                if matrix[i, j] > 0
            ]
        except Exception as error:
            raise HintUnavailable(f"SimAlign lexical inference failed: {error}") from error
        return _merge_alignment_pairs(pairs)


def _merge_alignment_pairs(pairs: list[tuple[int, int, float]]) -> tuple[LexicalAlignment, ...]:
    if not pairs:
        return ()
    ordered = sorted(pairs)
    groups: list[list[tuple[int, int, float]]] = []
    for pair in ordered:
        if groups and pair[0] == groups[-1][-1][0] + 1 and pair[1] == groups[-1][-1][1] + 1:
            groups[-1].append(pair)
        else:
            groups.append([pair])
    return tuple(
        LexicalAlignment(
            zh_token_start=group[0][0], zh_token_end=group[-1][0] + 1,
            en_token_start=group[0][1], en_token_end=group[-1][1] + 1,
            score=sum(item[2] for item in group) / len(group),
        )
        for group in groups
    )
