"""
classifier.py - LLM-powered document classifier with keyword-score voting.

Algorithm
---------
1. Receive extracted text (first 3 pages) from the extractor.
2. Load categories + keywords (passed in from env_loader).
3. Send a single LLM prompt containing:
      [A] The extracted document text
      [B] Each category with its keywords
      [C] Instruction: "Score each category 0-10 based on keyword matches"
4. LLM returns a JSON object with a score per category.
5. The category with the highest score wins.
6. If the top score is below the configured threshold -> classify as "Others".

Fallback
--------
If the LLM call fails for any reason, the module falls back to the
original fuzzy keyword-matching logic (now a secondary path).
"""

from __future__ import annotations

import json
import logging
import re
import string
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum fuzzy-match ratio (0-100) for the fallback keyword scorer
_FUZZY_RATIO_THRESHOLD = 80


# ── Text normalisation (used by fallback scorer) ───────────────────────────────

def _normalise(text: str) -> str:
    """Lowercase and strip punctuation, keeping spaces and newlines."""
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Main classifier ────────────────────────────────────────────────────────────

class DocumentClassifier:
    """
    Classifies document text into a predefined category using an LLM scorer.

    The LLM receives the document text and all category keywords, then
    returns a score (0-10) for each category. The highest-scoring category
    wins. Falls back to fuzzy keyword matching if the LLM is unavailable.

    Args:
        categories:  Mapping of ``{category_name: [keyword, ...]}``.
        threshold:   Minimum LLM score (0-10) to accept a category win.
                     If the winner's score is below this, returns "Others".
        llm_model:   LLM model identifier string (e.g. "claude-sonnet-4-6").
        llm_enabled: Whether to use the LLM scorer. When False, uses only
                     the legacy fuzzy keyword fallback.
    """

    OTHERS = "Others"

    def __init__(
        self,
        categories: dict[str, list[str]],
        threshold: float = 3.0,
        llm_model: str = "claude-sonnet-4-6",
        llm_enabled: bool = True,
    ) -> None:
        self.categories = categories
        self.threshold = threshold          # score out of 10
        self.llm_model = llm_model
        self.llm_enabled = llm_enabled

        # Pre-normalise keywords for the fallback scorer
        self._normalised_keywords: dict[str, list[str]] = {
            cat: [_normalise(kw) for kw in kws]
            for cat, kws in categories.items()
        }

    # ── Public API ─────────────────────────────────────────────────────────────

    def classify(self, text: str) -> tuple[str, float]:
        """
        Assign a category to *text*.

        Returns:
            ``(category_name, score)`` where score is in the range [0.0, 10.0].
            Score is normalised to [0.0, 1.0] for backward compat in reports.
        """
        if not text or not text.strip():
            logger.debug("Empty text; defaulting to '%s'.", self.OTHERS)
            return self.OTHERS, 0.0

        if self.llm_enabled:
            category, score = self._classify_with_llm(text)
            # Normalise 0-10 -> 0-1 for report compatibility
            return category, round(score / 10.0, 4)

        # Fallback to fuzzy scoring
        category, score = self._classify_fuzzy(text)
        return category, round(score, 4)

    # ── LLM Scorer (Primary) ───────────────────────────────────────────────────

    def _classify_with_llm(self, text: str) -> tuple[str, float]:
        """
        Ask the LLM to score each category based on keyword presence.

        The LLM receives:
          [1] Extracted document text (first 3 pages, already trimmed by extractor)
          [2] Each category with its full keyword list
          [3] Instruction to score 0-10 per category

        The LLM returns JSON:
          {
            "scores": {
              "Invoice": 7,
              "Insurance Loss Run": 2,
              "Bank Statement": 0
            },
            "winner": "Invoice",
            "reasoning": "Document contains 'invoice number', 'bill to', 'amount due'..."
          }

        Returns:
            (category_name, score_0_to_10)
        """
        try:
            import openai  # type: ignore
        except ImportError:
            logger.warning("openai package not installed. Falling back to fuzzy scoring.")
            return self._classify_fuzzy(text)

        # ── Build the category + keyword block ────────────────────────────────
        categories_block = "\n".join(
            f"- {cat}: {', '.join(kws)}"
            for cat, kws in self.categories.items()
        )

        # Trim text to avoid huge prompts (extractor already limits to 3 pages)
        text_snippet = text[:4000]

        # ── Prompt ────────────────────────────────────────────────────────────
        prompt = f"""You are a document classifier. Your job is to score how well a document matches each category based on keyword presence.

DOCUMENT TEXT (extracted from first 3 pages):
---
{text_snippet}
---

CATEGORIES AND THEIR KEYWORDS:
{categories_block}

INSTRUCTIONS:
For each category, count how many of its keywords appear in or are semantically present in the document text.
Give each category a score from 0 to 10 (0 = no match, 10 = perfect match).
Then identify the winner (category with the highest score).
If no category scores above {self.threshold}, set winner to "Others".

IMPORTANT: Respond ONLY with valid JSON in this exact format:
{{
  "scores": {{
    "CategoryName1": <integer 0-10>,
    "CategoryName2": <integer 0-10>
  }},
  "winner": "<category name or 'Others'>",
  "reasoning": "<one sentence explaining the top match>"
}}"""

        # ── Call OpenAI API ──────────────────────────────────────────────
        try:
            from env_loader import get_env_setting
            api_key = get_env_setting("OPENAI_API_KEY")
            client = openai.OpenAI(api_key=api_key if api_key else None)

            response = client.chat.completions.create(
                model=self.llm_model,
                max_tokens=512,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You are a document classifier. Always respond with valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                timeout=60.0,
            )

            response_text = response.choices[0].message.content.strip()

            # Strip markdown fences if the LLM wraps the JSON
            response_text = re.sub(r"```[a-z]*\n?", "", response_text).strip("` \n")

            result = json.loads(response_text)

        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s - falling back to fuzzy.", exc)
            return self._classify_fuzzy(text)
        except Exception as exc:
            logger.error("LLM call failed: %s - falling back to fuzzy.", exc)
            return self._classify_fuzzy(text)

        # ── Parse response ────────────────────────────────────────────────────
        scores: dict[str, float] = {}
        raw_scores = result.get("scores", {})

        for cat_name, score_val in raw_scores.items():
            try:
                scores[cat_name] = float(score_val)
            except (TypeError, ValueError):
                scores[cat_name] = 0.0

        winner = result.get("winner", self.OTHERS)
        reasoning = result.get("reasoning", "")

        # Validate winner exists in our categories
        if winner not in self.categories and winner != self.OTHERS:
            logger.warning("LLM returned unknown category '%s'; using 'Others'.", winner)
            winner = self.OTHERS

        # Re-derive winner from scores in case LLM gave a bad winner field
        if scores:
            best_cat = max(scores, key=lambda c: scores[c])
            best_score = scores[best_cat]
            if best_score < self.threshold:
                winner = self.OTHERS
                best_score = max(scores.values()) if scores else 0.0
            else:
                winner = best_cat
        else:
            best_score = 0.0
            winner = self.OTHERS

        logger.info(
            "LLM scores: %s | Winner: '%s' (score=%s) | %s",
            scores,
            winner,
            best_score,
            reasoning,
        )
        return winner, best_score

    # ── Fuzzy Fallback (Secondary) ─────────────────────────────────────────────

    def _classify_fuzzy(self, text: str) -> tuple[str, float]:
        """
        Legacy fuzzy keyword scorer used when LLM is unavailable.

        Returns:
            (category_name, score_0_to_1)
        """
        normalised_text = _normalise(text)
        scores = self._score_all(normalised_text)

        if not scores:
            return self.OTHERS, 0.0

        best_category = max(scores, key=lambda c: scores[c])
        best_score = scores[best_category]

        logger.debug("Fuzzy scores: %s", scores)

        # Threshold for fuzzy is 0-1 scale; convert class threshold from 0-10
        fuzzy_threshold = self.threshold / 10.0
        if best_score >= fuzzy_threshold:
            return best_category, best_score * 10.0  # scale to 0-10

        return self.OTHERS, best_score * 10.0

    def _score_all(self, normalised_text: str) -> dict[str, float]:
        """Compute a normalised confidence score (0-1) for every category."""
        scores: dict[str, float] = {}
        tokens = normalised_text.split()

        for category, keywords in self._normalised_keywords.items():
            if not keywords:
                scores[category] = 0.0
                continue

            raw_score = 0.0
            for keyword in keywords:
                match_score = self._score_keyword(keyword, normalised_text, tokens)
                raw_score += match_score

            scores[category] = raw_score / len(keywords)

        return scores

    def _score_keyword(
        self, keyword: str, normalised_text: str, tokens: list[str]
    ) -> float:
        """Return a [0, 1] score indicating how well *keyword* matches the text."""
        if keyword in normalised_text:
            return 1.0

        keyword_tokens = keyword.split()
        if len(keyword_tokens) == 1:
            return self._fuzzy_token_score(keyword, tokens)

        window_size = len(keyword_tokens)
        best = 0.0
        for i in range(max(1, len(tokens) - window_size + 1)):
            window = " ".join(tokens[i : i + window_size])
            score = self._fuzzy_ratio(keyword, window)
            if score > best:
                best = score
            if best == 1.0:
                break
        return best

    @staticmethod
    def _fuzzy_token_score(keyword: str, tokens: list[str]) -> float:
        """Best fuzzy ratio between *keyword* and any single token."""
        try:
            from rapidfuzz import fuzz  # type: ignore

            best = 0.0
            threshold = _FUZZY_RATIO_THRESHOLD / 100
            for token in tokens:
                ratio = fuzz.ratio(keyword, token) / 100
                if ratio > best:
                    best = ratio
                if best >= threshold:
                    return best
            return best if best >= threshold else 0.0
        except ImportError:
            return 0.0

    @staticmethod
    def _fuzzy_ratio(a: str, b: str) -> float:
        """Return normalised fuzzy ratio (0-1) between two strings."""
        try:
            from rapidfuzz import fuzz  # type: ignore

            ratio = fuzz.partial_ratio(a, b) / 100
            return ratio if ratio >= _FUZZY_RATIO_THRESHOLD / 100 else 0.0
        except ImportError:
            return 1.0 if a == b else 0.0
