# Lausuntobotti

[![CI](https://github.com/kuosaton/lausuntobotti/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kuosaton/lausuntobotti/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/kuosaton/lausuntobotti/graph/badge.svg?token=DM3PJTS30G)](https://codecov.io/gh/kuosaton/lausuntobotti) [![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fkuosaton%2Flausuntobotti%2Frefs%2Fheads%2Fmain%2Fpyproject.toml&logo=python&logoColor=white)](https://www.python.org/)
[![uv package manager](https://img.shields.io/badge/uv-package%20manager?logo=uv&label=package%20manager&color=%23DE5FE9)](https://docs.astral.sh/uv/)

Lausuntobotti is an LLM-based monitoring tool that helps [Kuluttajaliitto](https://www.kuluttajaliitto.fi/) keep up with new public consultation requests and parliamentary committee agendas. It uses [Claude](https://claude.com/product/overview) to surface relevant items from [lausuntopalvelu.fi](https://www.lausuntopalvelu.fi) and Eduskunta committee proceedings, then sends email digests to the chosen recipients.

<p align="center"> <img src=".github/assets/lausuntobotti_digest_example.png" width="700px" alt="Lausuntobotti email digest example"></p>

## How it works

The bot is designed to uncover public policy items that are relevant to Kuluttajaliitto and may otherwise be easy to miss.

It monitors two public sources:

- **lausuntopalvelu.fi proposals**: skips proposals where Kuluttajaliitto is already on the distribution list or has already responded.
- **Eduskunta committee agendas**: reviews each agenda matter.

For the remaining items, the bot:

1. Scores relevance from 0 to 10 with Claude.
2. Flags high-scoring items for the email digest.
3. Logs borderline items for review.
4. Drops low-scoring items.

### Relevancy scoring

Each item is scored by Claude based on Kuluttajaliitto's previously published statements and areas of focus. The default model is [Claude Haiku 4.5](https://www.anthropic.com/news/claude-haiku-4-5), and the scoring model/settings are configurable, as detailed in section [Model configuration](#model-configuration).

| Score | Meaning                                                                                                                     | Action                       |
| ----- | --------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| 8-10  | Clearly within Kuluttajaliitto's core mandate, such as consumer protection, product safety, financial services, or housing. | `FLAG` and include in digest |
| 6-7   | Relevant or adjacent to Kuluttajaliitto's priorities.                                                                       | `FLAG` and include in digest |
| 4-5   | Borderline relevance.                                                                                                       | `LOG` only                   |
| 2-3   | Thin connection to consumer matters.                                                                                        | `DROP`                       |
| 0-1   | No clear connection to consumers or Kuluttajaliitto's work.                                                                 | `DROP`                       |

### Data sources

All data comes from publicly accessible sources:

- [lausuntopalvelu.fi Open API](https://www.lausuntopalvelu.fi/api/v1/Lausuntopalvelu.svc): new requests for comment via the public OData/Atom feed; distribution lists and prior responses scraped from each proposal's participation page.
- [kuluttajaliitto.fi WordPress API](https://www.kuluttajaliitto.fi/wp-json/): Kuluttajaliitto's published statements, used as the corpus the scoring model compares new proposals against.
- [eduskunta.fi](https://www.eduskunta.fi/) and [avoindata.eduskunta.fi](https://avoindata.eduskunta.fi/): committee pages and VaskiData XML for parliamentary committee agendas.

## Usage

Prerequisites:

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Python 3.14](https://docs.astral.sh/uv/guides/install-python/) (uv can use an existing installation or automatically install one if missing.)

### Setup

Download the [latest release](https://github.com/kuosaton/lausuntobotti/releases/latest), extract the compressed files, and navigate into `lausuntobotti/`. Then:

#### 1. Configure the environment

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

#### 2. Use the tool

Run the interactive CLI:

```bash
uv run main.py
```

Use `h` in the menu for options.

Direct commands are listed with:

```bash
uv run python main.py --help
```

`uv run` works without activating a virtual environment. If you prefer an activated shell, run `uv sync` first, activate `.venv`, and then use the same commands without the `uv run` prefix.

### Valiokunta Analysis

`--valiokunta` currently tracks new Talousvaliokunta agendas, extracts scheduled matters from VaskiData XML, scores them with the same Kuluttajaliitto context, and sends a digest. Support for Maa- ja metsätalousvaliokunta and Ympäristövaliokunta is planned next. Replay/resend is not persisted yet; use `--valiokunta --dry-run` to preview a fresh run.

## Development

```bash
# Canonical quality gate (same command used in CI)
uv run make check

# Fast local smoke checks
uv run make quick-test

# Optional: run configured hooks across all files
uv run make precommit

# One-time install for git hooks (pre-commit + pre-push)
uv run make precommit-install

# Mutation testing (heavier quality signal)
uv run make mutation
uv run make mutation-results
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

### Model configuration (optional)

Defaults live in [`model_config.toml`](model_config.toml):

```toml
[scoring]
model = "claude-haiku-4-5"
# Sonnet/Opus 4.6+ only. Uncomment with e.g. model = "claude-sonnet-4-6".
# effort = "medium"
max_tokens = 300
timeout_seconds = 45.0
prompt_cache = true
cache_ttl = "5m"
```

Edit this file to try another [Claude model](https://platform.claude.com/docs/en/about-claude/models/overview):

- Haiku 4.5 is the default for high-volume, short, structured relevance scoring where speed and cost matter.
- Sonnet 4.6 may help with ambiguous or borderline items, but costs more. Compare it against historical score log examples before switching globally.

`effort` is optional and is only sent when configured; use it with supported Sonnet/Opus models, such as `effort = "medium"` for Sonnet 4.6. `max_tokens`, timeout, and cache settings are tuning knobs, not required setup. Keep [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) enabled unless you have a reason to disable it.

For deployment-specific overrides, use the optional `CLAUDE_SCORING_*` environment variables shown in `.env.example`; environment variables take precedence over `model_config.toml`.

### System prompt

The scoring rubric and mission framing live in `SYSTEM_PROMPT` in [`processing/llm_scorer.py`](processing/llm_scorer.py). You can edit it to tune what the bot treats as relevant, but compare changes against historical `score_log.jsonl` examples before using them in production; prompt changes affect which items appear in digests.
