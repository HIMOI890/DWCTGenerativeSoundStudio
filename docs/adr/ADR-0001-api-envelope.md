# ADR-0001: Standard API envelope

## Status
Accepted

## Context
We need consistent client handling for success/failure across endpoints and predictable error display in the UI.

## Decision
All backend endpoints must return:
- Success: `{ "ok": true, ... }`
- Failure: `{ "ok": false, "error": { "message": "...", "hint": "...", "code": "..." } }`

User-facing failures should raise `UserFacingError` and be handled centrally by FastAPI exception handlers.

## Consequences
- UI can implement one uniform success/failure handler
- Backend must maintain envelope stability
- Requires discipline in endpoint implementation

## Alternatives considered
- Raw FastAPI `HTTPException` only (rejected: inconsistent payload shape)
- GraphQL-style error arrays (rejected: unnecessary complexity)
