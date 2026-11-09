# Contributing to OpenChorus

Thanks for helping. Bug reports, ideas, docs fixes and code are all welcome.

## Ground rules

- Be kind. See the [Code of Conduct](CODE_OF_CONDUCT.md).
- Open an issue before a large change so we can agree on the approach.
- Keep everything local and open: new features must run on open models and must not
  call a paid or closed AI service.
- Never add code that copies, scrapes or decompiles a commercial music service.
  OpenChorus is a clean-room project (see [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md)).

## Set up

```bash
git clone https://github.com/kshivam4781/openchorus
cd openchorus
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m playwright install chromium
```

You do not need a GPU to develop. The demo engine synthesises instrumental sketches
offline so every feature can be exercised.

## Run the checks

```bash
ruff check .                 # lint
pytest                       # API, engine and planner tests (demo engine, no GPU)
python tests/run_e2e.py      # full browser run with Playwright
```

CI runs the same three on every pull request.

## Code style

- Python 3.10+, type hints, small functions, comments that explain *why*.
- Front end is plain HTML, CSS and JavaScript with no build step. Keep it that way.
- User-facing text: plain words, sentence case, say what happens ("Paint cover", not "Submit").

## Pull requests

1. Fork and create a branch from `main`.
2. Add or update tests for what you changed.
3. Update `CHANGELOG.md` under **Unreleased**.
4. Make sure `ruff check .` and `pytest` pass.
5. Open the PR and fill in the template.

## Adding an engine

Engines live in `server/engines/`. Implement the `Engine` interface from
`server/engines/base.py` (`check`, `available`, `generate`) and register it in
`server/engines/__init__.py`. Add a contract test with a fake server like
`tests/test_acestep_contract.py`.

## Releases

Maintainers tag `vX.Y.Z` on `main`. The release workflow builds and publishes the
Docker image to GitHub Container Registry. Versions follow [SemVer](https://semver.org/):
breaking API changes bump the major number.
