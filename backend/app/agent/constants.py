"""Pure constants for the agent layer.

Model IDs, pricing (cents per million tokens), and budget rails. No imports
from app internals — this module is the root of the agent dependency graph.
"""

# Dated model IDs — use dated form over aliases for production stability.
MODEL_HAIKU = "claude-haiku-4-5-20251001"
# Sonnet 4.6 dated ID not yet surfaced by context7 at planning time.
# TODO: resolve the dated form via `await client.models.list()` at dev time
# and replace the alias below. Alias is fine for now — narration is optional.
MODEL_SONNET = "claude-sonnet-4-6"

# Pricing in cents per million tokens. Verify against Anthropic pricing
# page periodically; these are the Haiku 4.5 numbers as of Apr 2026.
CENTS_PER_MTOK_HAIKU_INPUT = 100  # $1.00 / MTok
CENTS_PER_MTOK_HAIKU_CACHE_WRITE = 125  # $1.25 / MTok (ephemeral write)
CENTS_PER_MTOK_HAIKU_CACHE_READ = 10  # $0.10 / MTok (cache hit)
CENTS_PER_MTOK_HAIKU_OUTPUT = 500  # $5.00 / MTok

CENTS_PER_MTOK_SONNET_INPUT = 300  # $3.00 / MTok — verify
CENTS_PER_MTOK_SONNET_OUTPUT = 1500  # $15.00 / MTok — verify

# Budget rails per ARCHITECTURE §5.2.
MAX_COST_CENTS_PER_TURN = 15  # ~$0.15; if exceeded, skip Sonnet narration.
MAX_COST_CENTS_PER_RUN = 40  # ~$0.40 hard run cap.
