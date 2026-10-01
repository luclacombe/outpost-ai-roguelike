"""Runtime checks on a parsed per-turn output against the rendered turn.

Schema validation (schemas.py) checks shape. These checks compare the output
with the exact user message the persona saw, and downgrade rather than
raise: a bad flag is dropped, the pick survives. Raising would cost a
retry and possibly the pick itself.

1. Fabricated-silence guard (all flags): a `(silent)` quote when the
   message renders no `(silent)` marker is fabrication.
2. Verbatim-quote rule for `confused` / `suspected_bug`: a True flag needs a
   quote whose fragments (split on `...`) all appear in the message, after
   lowercasing, collapsing whitespace and stripping markdown emphasis.
3. `outcome_matched_expectation` is forced to None when the message had no
   `## Last turn` section (turn 1, or nothing to judge).
"""

from __future__ import annotations

import logging
import re

from app.eval.playtest.schemas import PersonaTurnFlags, PersonaTurnOutput

logger = logging.getLogger(__name__)

VERBATIM_FLAGS = ("confused", "suspected_bug")
LAST_TURN_HEADING = "## Last turn"

_QUOTE_CHARS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _normalize(text: str) -> str:
    text = text.translate(_QUOTE_CHARS).replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_is_verbatim(quote: str, user_message: str) -> bool:
    """True if every `...`-separated fragment of `quote` occurs in the message."""
    haystack = _normalize(user_message)
    fragments = [
        _normalize(f).strip(" \"'") for f in re.split(r"\.\.\.|…", quote) if f.strip(" \"'.")
    ]
    return bool(fragments) and all(f in haystack for f in fragments)


def _drop_flags(output: PersonaTurnOutput, flags: set[str]) -> PersonaTurnOutput:
    if not flags:
        return output
    new_quotes = {k: v for k, v in output.quotes.items() if k not in flags}
    flags_dict = output.flags.model_dump()
    for k in flags:
        if k in flags_dict:
            flags_dict[k] = False
    return output.model_copy(update={"quotes": new_quotes, "flags": PersonaTurnFlags(**flags_dict)})


def validate_quotes_against_dashboard(
    output: PersonaTurnOutput, user_message: str
) -> PersonaTurnOutput:
    """Drop fabricated `(silent)` quotes and downgrade their flags.

    A `(silent)` substring inside any `quotes[*]` value is legitimate ONLY
    when the rendered user message also contains `(silent)` markers (i.e.
    the dashboard literally rendered silence for at least one advisor).
    When every advisor's reaction is rendered, the dashboard contains zero
    `(silent)` markers, so a persona quote of `(silent)` is fabrication.

    The persona's free-form `reaction` prose is left untouched (we can't
    audit free-form text; the job here is to gate the binary flag).

    Conservative when the dashboard DOES render silence: we can't tell
    which advisors are legitimately silent vs fabricated, so we accept
    every `(silent)` quote in that case.
    """
    if "(silent)" in user_message:
        return output
    affected = {k for k, v in output.quotes.items() if "(silent)" in v}
    for flag_name in sorted(affected):
        logger.warning(
            "fabricated-silence guard: dropping %s flag (no '(silent)' marker "
            "in rendered dashboard); fabricated quote was %r",
            flag_name,
            output.quotes[flag_name],
        )
    return _drop_flags(output, affected)


def validate_turn_output(output: PersonaTurnOutput, user_message: str) -> PersonaTurnOutput:
    """Apply all three checks (see module docstring). Never raises."""
    output = validate_quotes_against_dashboard(output, user_message)

    unsupported: set[str] = set()
    for flag in VERBATIM_FLAGS:
        if not getattr(output.flags, flag):
            continue
        quote = output.quotes.get(flag, "")
        if not quote_is_verbatim(quote, user_message):
            logger.warning("dropping %s flag: quote missing or not verbatim: %r", flag, quote)
            unsupported.add(flag)
    output = _drop_flags(output, unsupported)

    if output.outcome_matched_expectation is not None and LAST_TURN_HEADING not in user_message:
        output = output.model_copy(update={"outcome_matched_expectation": None})
    return output
