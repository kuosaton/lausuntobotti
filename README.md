# Lausuntobotti

[![CI](https://github.com/kuosaton/lausuntobotti/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kuosaton/lausuntobotti/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/kuosaton/lausuntobotti/graph/badge.svg?token=DM3PJTS30G)](https://codecov.io/gh/kuosaton/lausuntobotti) [![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fkuosaton%2Flausuntobotti%2Frefs%2Fheads%2Fmain%2Fpyproject.toml&logo=python&logoColor=white)](https://www.python.org/)
[![uv package manager](https://img.shields.io/badge/uv-package%20manager?logo=uv&label=package%20manager&color=%23DE5FE9)](https://docs.astral.sh/uv/)

Lausuntobotti is an LLM-based tool to help [Kuluttajaliitto](https://www.kuluttajaliitto.fi/) keep up with [lausuntopalvelu.fi](https://www.lausuntopalvelu.fi). It scores new statement requests with [Claude](https://claude.com/product/overview), highlights the most relevant ones, and sends email digests to the chosen recipients.

<p align="center"> <img src=".github/assets/lausuntobotti_digest_example.png" width="700px" alt="Lausuntobotti email digest example"></p>

## How it works

The bot is designed to uncover proposals that: (i) are relevant to Kuluttajaliitto and (ii) Kuluttajaliitto has not already been made aware of.

For new proposals, the bot:

1. **Ignores ones with Kuluttajaliitto on the distribution list (jakelulista)**: the requesting organisation has already identified Kuluttajaliitto as a relevant party and will notify them directly.
2. **Ignores ones that Kuluttajaliitto has already responded to.**
3. **Scores their relevancy from 0 to 10.**
4. **Flags high-scoring proposals for review** (score ≥ 6).
5. **Notifies designated recipients** of new flagged proposals via an HTML email digest.

### Data sources

All data comes from publicly accessible sources:

- **[lausuntopalvelu.fi Open API](https://www.lausuntopalvelu.fi/api/v1/Lausuntopalvelu.svc)**: new requests for comment via the public OData/Atom feed; distribution lists and prior responses scraped from each proposal's participation page.
- **[kuluttajaliitto.fi WordPress API](https://www.kuluttajaliitto.fi/wp-json/)**: Kuluttajaliitto's published statements, used as the corpus the scoring model compares new proposals against.

### Relevancy scoring

Each proposal is scored by Claude based on Kuluttajaliitto's previously published statements and areas of focus. The default model is [Claude Haiku 4.5](https://www.anthropic.com/news/claude-haiku-4-5), and the scoring model/settings are configurable. The rubric is:

- 8 to 10: Clearly within Kuluttajaliitto's core mandate such as consumer protection, product safety, financial services, or housing.
- 5 to 7: Concerns consumers indirectly, or is adjacent to Kuluttajaliitto's priorities.
- 2 to 4: Thin connection to consumer matters.
- 0 to 1: No clear connection to consumers or Kuluttajaliitto's work.

The bot then acts on the score, printing a tag for each processed proposal:

- `SKIP DISTRIBUTION`: Kuluttajaliitto is on the distribution list, skipped without scoring.
- `SKIP RESPONDED`: Kuluttajaliitto has already submitted a response, skipped without scoring.
- `FLAG x/10` (6 to 10): Included in the email digest.
- `LOG x/10` (4 to 5): Logged only.
- `DROP x/10` (0 to 3): Dropped silently.

## Usage

### Prerequisites

1. [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package and project manager)
2. [Python 3.14](https://www.python.org/downloads/) (We recommend [using uv to install and manage Python versions](https://docs.astral.sh/uv/guides/install-python/).)

### Setup

Download the [latest release](https://github.com/kuosaton/lausuntobotti/releases/latest), extract it, and `cd` into `lausuntobotti/`. Then:

#### 1. Install the project dependencies

```bash
uv sync               # runtime dependencies only
uv sync --extra dev   # include dev tools (pytest, ruff, pyright, pre-commit)
```

`uv sync` creates a `.venv/` directory in the project root. All examples in this README use `uv run`. If you prefer, activate the venv and run commands directly:

```bash
source .venv/bin/activate     # macOS/Linux
.venv\Scripts\activate        # Windows (PowerShell or cmd)
```

With the venv active, `python main.py`, `pytest`, `ruff`, etc. work directly without `uv run`.

#### 2. Configure the environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

| Variable            | Description                                             |
| ------------------- | ------------------------------------------------------- |
| `ANTHROPIC_API_KEY` | Anthropic API key                                       |
| `RESEND_API_KEY`    | Resend API key for email sending                        |
| `SENDER_EMAIL`      | From address (must be on a domain verified with Resend) |
| `RECIPIENT_EMAIL`   | Comma-separated recipient addresses for digests         |

#### Model configuration

Scoring defaults live in [`model_config.toml`](model_config.toml):

```toml
[scoring]
model = "claude-haiku-4-5"
max_tokens = 300
timeout_seconds = 45.0
prompt_cache = true
cache_ttl = "5m"
```

Edit this file for normal local experimentation. For deployment-specific overrides, set the optional `CLAUDE_SCORING_*` environment variables shown in `.env.example`; environment variables take precedence over `model_config.toml`.

Haiku 4.5 is the default because Lausuntobotti does high-volume, short, structured relevance scoring. Sonnet 4.6 is a useful candidate for ambiguous or borderline items, but compare it against historical `score_log.jsonl` examples before switching globally. `max_tokens`, timeout, and cache settings are tuning knobs, not required setup.

#### 3. Fetch up-to-date Kuluttajaliitto published statements context (required before first run)

```bash
uv run python main.py --update-context
```

### Using the tool

Run the interactive menu:

```bash
uv run python main.py
```

Use `h` in the menu, or `--help` on the command line, for the full list of options.

Direct CLI examples:

```bash
# Daily check: score new proposals and send the digest if any clear the threshold
uv run python main.py --daily
uv run python main.py --daily --dry-run    # score and log, but don't send

# Full list of commands: refresh context, preview or resend digests, review borderline, reset state
uv run python main.py --help
```

## Planned features

**Parliamentary committee analysis (`--weekly`, `--midweek`).** Kuluttajaliitto also needs to track proceedings in relevant parliamentary committees (talousvaliokunta, sosiaali- ja terveysvaliokunta). The planned commands would score new committee items using the same model and deliver them in a weekly digest.

## Development

```bash
# Canonical quality gate (same command used in CI)
make check

# Fast local smoke checks
make quick-test

# Optional: run configured hooks across all files
make precommit

# One-time install for git hooks (pre-commit + pre-push)
make precommit-install

# Mutation testing (heavier quality signal)
make mutation
make mutation-results
```

### State files

All state lives under `state/`:

| File                  | Contents                                    |
| --------------------- | ------------------------------------------- |
| `seen_proposals.json` | Proposals already processed (deduplication) |
| `score_log.jsonl`     | Full scoring history                        |
| `nostetut.json`       | Items that crossed the notify threshold     |
| `seen_documents.json` | Reserved for document-level deduplication   |
