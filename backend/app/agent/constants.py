"""Pure constants for the agent layer.

Model IDs, pricing (cents per million tokens), and budget rails. No imports
from app internals — this module is the root of the agent dependency graph.
"""

# Model IDs. Haiku 4.5 publishes a dated snapshot; Sonnet 4.6 and Opus 4.7
# do not — per Anthropic's models overview (platform.claude.com/docs/en/about-
# claude/models/overview, verified 2026-04-24), the "Claude API ID" for
# claude-sonnet-4-6 is the alias itself. Only Haiku 4.5 has a dated form.
# If Anthropic publishes a dated Sonnet 4.6 later, swap it in here.
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"

# Pricing in cents per million tokens. Verify against Anthropic pricing
# page periodically; these are the Haiku 4.5 numbers as of Apr 2026.
CENTS_PER_MTOK_HAIKU_INPUT = 100  # $1.00 / MTok
CENTS_PER_MTOK_HAIKU_CACHE_WRITE = 125  # $1.25 / MTok (ephemeral write)
CENTS_PER_MTOK_HAIKU_CACHE_READ = 10  # $0.10 / MTok (cache hit)
CENTS_PER_MTOK_HAIKU_OUTPUT = 500  # $5.00 / MTok

CENTS_PER_MTOK_SONNET_INPUT = 300  # $3.00 / MTok
CENTS_PER_MTOK_SONNET_CACHE_WRITE = 375  # $3.75 / MTok (input × 1.25, ephemeral 5m)
CENTS_PER_MTOK_SONNET_CACHE_READ = 30  # $0.30 / MTok (input × 0.10)
CENTS_PER_MTOK_SONNET_OUTPUT = 1500  # $15.00 / MTok

# Budget rails per ARCHITECTURE §5.2.
MAX_COST_CENTS_PER_TURN = 15  # ~$0.15; if exceeded, skip Sonnet narration.
MAX_COST_CENTS_PER_RUN = 40  # ~$0.40 hard run cap.
