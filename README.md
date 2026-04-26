# MCP Weather Project Template

A Python MCP-based weather assistant that connects one host process to two weather tool servers:

- Israel weather server (browser automation via Playwright)
- USA weather server (NWS API via HTTP)

The host uses a Gemini-compatible OpenAI client API to decide when to call tools and how to answer user queries.

## Project Structure

- host.py: Main chat host, tool orchestration, Gemini/OpenAI-compatible calls
- client.py: MCP stdio client wrapper used to connect to MCP servers
- weather_Israel.py: MCP server for Israel city weather navigation/search
- weather_USA.py: MCP server for USA alerts and forecast data
- .env.example: Environment variable template
- pyproject.toml: Project metadata and dependencies

## Requirements

- Python 3.13+
- uv (recommended package manager/runner)
- Playwright Chromium browser (for Israel weather server)
- Gemini API key

## Setup

1. Install dependencies:

```bash
uv sync
```

2. Install Playwright browser:

```bash
uv run playwright install chromium
```

3. Create environment file from template and set your key:

```bash
copy .env.example .env
```

Then edit .env and set:

```env
GEMINI_API_KEY=your_real_api_key
```

## Run

Start the host:

```bash
uv run host.py
```

The app starts an interactive loop. Type a query and press Enter.
Type quit to exit.

## How It Works

1. host.py starts and loads GEMINI_API_KEY from .env.
2. It connects to both MCP servers through client.py.
3. It lists available MCP tools and exposes them to the model.
4. For each query:
   - It first tries direct city weather opening for Israel queries.
   - Otherwise it asks the model, which may call one or more MCP tools.
5. Tool outputs are returned to the model, then a final response is printed.

## Notes

- The Israel weather server attempts a visible Chromium session first, and falls back to headless when needed.
- In SSL-intercepted environments, the code currently uses disabled certificate verification for some HTTP clients.
- .env is ignored by git, while .env.example remains tracked.

## Dependencies

Defined in pyproject.toml:

- anthropic
- mcp
- python-dotenv
- playwright
- openai

## Troubleshooting

- Missing GEMINI_API_KEY: add the key to .env.
- Playwright browser errors: run the Playwright install command again.
- Rate limit (429): wait for the suggested retry delay and rerun your query.
- Host fails at startup: run a syntax check:

```bash
uv run python -m py_compile host.py
```
