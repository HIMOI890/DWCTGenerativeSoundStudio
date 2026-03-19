# Contributing

## Dev setup
- Use Node >= 18 (or the version specified in project docs)
- Use Python 3.11 for the backend

## Code style
### Node/TS (studio/edmg-studio)
- Format with Prettier
- Lint with ESLint
- Typecheck with `npm run typecheck`

### Python (studio/edmg-studio/python_backend)
- Format + lint with Ruff
- Run tests with pytest

## Commands (recommended)
### Node/TS
- `npm run typecheck`
- `npm run dev`
- After adding scripts: `npm run lint`, `npm run format`, `npm test`

### Python (from python_backend dir)
- `python -m ruff check .`
- `python -m ruff format .`
- `pytest`

## Pull requests
- Keep changes focused
- Include tests for behavior changes
- Update docs if you change API shape or UX
