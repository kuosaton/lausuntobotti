# Lausuntobotti

[![CI](https://github.com/kuosaton/lausuntobotti/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kuosaton/lausuntobotti/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/kuosaton/lausuntobotti/graph/badge.svg?token=DM3PJTS30G)](https://codecov.io/gh/kuosaton/lausuntobotti) [![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fkuosaton%2Flausuntobotti%2Frefs%2Fheads%2Fmain%2Fpyproject.toml&logo=python&logoColor=white)](https://www.python.org/)
[![uv package manager](https://img.shields.io/badge/uv-package%20manager?logo=uv&label=package%20manager&color=%23DE5FE9)](https://docs.astral.sh/uv/)

Lausuntobotti is an LLM-based monitoring tool that helps [Kuluttajaliitto](https://www.kuluttajaliitto.fi/) keep up with new public consultation requests and parliamentary committee agendas. It scores new items from [lausuntopalvelu.fi](https://www.lausuntopalvelu.fi) and Eduskunta committee proceedings with [Claude](https://claude.com/product/overview), highlights the most relevant ones, and sends email digests to the chosen recipients.

<p align="center"> <img src=".github/assets/lausuntobotti_digest_example.png" width="700px" alt="Lausuntobotti email digest example"></p>

## How it works

The bot is designed to uncover public policy items that are relevant to Kuluttajaliitto and may otherwise be easy to miss.

For new lausuntopalvelu.fi proposals, the lausuntopyyntö check:

1. **Ignores ones with Kuluttajaliitto on the distribution list (jakelulista)**: the requesting organisation has already identified Kuluttajaliitto as a relevant party and will notify them directly.
2. **Ignores ones that Kuluttajaliitto has already responded to.**
3. **Scores their relevancy from 0 to 10.**
4. **Flags high-scoring proposals for review** (score ≥ 6).
5. **Notifies designated recipients** of new flagged proposals via an HTML email digest.

For Eduskunta committee agendas, the valiokunta check:

1. **Fetches new committee agenda documents** from configured committee pages.
2. **Fetches the full agenda XML from VaskiData** for each new agenda.
3. **Extracts scheduled matters** such as government proposals and EU matters.
4. **Scores each matter from 0 to 10** using the same Kuluttajaliitto context.
5. **Sends a valiokunta digest** with matters above the notification threshold.

### Data sources

All data comes from publicly accessible sources:

- **[lausuntopalvelu.fi Open API](https://www.lausuntopalvelu.fi/api/v1/Lausuntopalvelu.svc)**: new requests for comment via the public OData/Atom feed; distribution lists and prior responses scraped from each proposal's participation page.
- **[kuluttajaliitto.fi WordPress API](https://www.kuluttajaliitto.fi/wp-json/)**: Kuluttajaliitto's published statements, used as the corpus the scoring model compares new proposals against.
- **[eduskunta.fi](https://www.eduskunta.fi/)** and **[avoindata.eduskunta.fi](https://avoindata.eduskunta.fi/)**: committee pages and VaskiData XML for parliamentary committee agendas.

### Relevancy scoring

Each item is scored by Claude based on Kuluttajaliitto's previously published statements and areas of focus. The default model is [Claude Haiku 4.5](https://www.anthropic.com/news/claude-haiku-4-5), and the scoring model/settings are configurable. The rubric is:

- **8 to 10**: Clearly within Kuluttajaliitto's core mandate such as consumer protection, product safety, financial services, or housing.
- **5 to 7**: Concerns consumers indirectly, or is adjacent to Kuluttajaliitto's priorities.
- **2 to 4**: Thin connection to consumer matters.
- **0 to 1**: No clear connection to consumers or Kuluttajaliitto's work.

The bot then acts on the score, printing a tag for each processed item:

- `SKIP DISTRIBUTION`: Kuluttajaliitto is on the lausuntopalvelu.fi distribution list, skipped without scoring.
- `SKIP RESPONDED`: Kuluttajaliitto has already submitted a lausuntopalvelu.fi response, skipped without scoring.
- `FLAG x/10` (6 to 10): Included in the email digest.
- `LOG x/10` (4 to 5): Logged only.
- `DROP x/10` (0 to 3): Dropped silently.

## Usage

### Prerequisites

1. [uv](https://docs.astral.sh/uv/getting-started/installation/)
2. [Python 3.14](https://docs.astral.sh/uv/guides/install-python/)

### Setup

Download the [latest release](https://github.com/kuosaton/lausuntobotti/releases/latest), extract it, and `cd` into `lausuntobotti/`. Then:

#### 1. Install the project dependencies

```bash
uv sync               # runtime dependencies only
uv sync --extra dev   # include dev tools (pytest, ruff, pyright, pre-commit)
```

A `.venv` (virtual environment) directory is created in the project root. Activate it:

```bash
source .venv/bin/activate     # macOS/Linux
.venv\Scripts\activate        # Windows (PowerShell or cmd)
```

All command examples from now on assume that venv has been activated.

_With venv active, `python main.py`, `pytest`, `ruff`, etc. work directly without `uv run`. If you prefer, you may use the same commands without activating venv by using the `uv run` prefix instead._

#### 2. Configure the environment

Copy .env file

```bash
cp .env.example .env
```

Edit `.env` with your values:

| Variable            | Description                                               |
| ------------------- | --------------------------------------------------------- |
| `ANTHROPIC_API_KEY` | Anthropic API key                                         |
| `RESEND_API_KEY`    | Resend API key for email sending                          |
| `SENDER_EMAIL`      | Sender address (must be on a domain verified with Resend) |
| `RECIPIENT_EMAIL`   | Comma-separated recipient addresses for digests           |

#### 3. Use the tool

Use the interactive command line (CLI) interface:

```bash
python main.py
```

Use `h` in the menu for the full list of options.

You may also use the tool through direct CLI commands. For the full list of commands, use:

```bash
python main.py --help
```

### Valiokunta Analysis

`--valiokunta` currently tracks new Talousvaliokunta agendas, fetches the agenda XML from VaskiData, extracts scheduled matters, scores them with the same Kuluttajaliitto context, and sends a valiokunta digest. Support for Maa- ja metsätalousvaliokunta and Ympäristövaliokunta is planned next. Valiokunta digest replay/resend is not persisted yet; use `--valiokunta --dry-run` to preview a fresh run.

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

| File                         | Contents                                    |
| ---------------------------- | ------------------------------------------- |
| `seen_proposals.json`        | Proposals already processed (deduplication) |
| `score_log.jsonl`            | Lausuntopyyntö scoring history              |
| `valiokunta_score_log.jsonl` | Valiokunta scoring history                  |
| `nostetut.json`              | Items that crossed the notify threshold     |
| `seen_documents.json`        | Committee agenda deduplication              |

### Extra: Model configuration

The scoring model configuration is located in [`model_config.toml`](model_config.toml), with the defaults being:

```toml
[scoring]
model = "claude-haiku-4-5"
max_tokens = 300
timeout_seconds = 45.0
prompt_cache = true
cache_ttl = "5m"
```

You may edit this file to experiment with different configurations, such as using a different [Claude model](https://platform.claude.com/docs/en/about-claude/models/overview):

- Haiku 4.5 is used by default because Lausuntobotti does high-volume, short, and structured relevance scoring, for which the model's speed and cost-effectiveness are particularly suited for.
- Sonnet 4.6 could be a useful candidate for ambiguous or borderline items, but introduces increased costs. Compare it against historical score log examples before switching globally.

`max_tokens`, timeout, and cache settings are tuning knobs, not required setup nor something you may need to touch at all. [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), for example, "significantly reduces processing time and costs for repetitive tasks or prompts with consistent elements", and is highly recommended to be kept enabled for optimized API usage.

For deployment-specific overrides, you may the optional `CLAUDE_SCORING_*` environment variables shown in `.env.example`; environment variables take precedence over `model_config.toml`.
