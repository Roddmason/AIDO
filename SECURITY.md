# Security Policy

## Supported Branch

Security fixes target `dev`, the current default integration branch.

## Reporting A Vulnerability

Please report security issues privately to the repository owner instead of
opening a public issue with exploit details. Include the affected area,
reproduction steps, expected impact, and any safe proof of concept.

## Project Security Expectations

- Do not commit API keys, local tokens, `.env` files, SQLite runtime databases,
  prompt dumps, workspace snapshots with private data, or generated artifacts.
- Real model provider calls and CLI runtime execution are disabled by default
  and must stay behind policy, budget, quota, workspace, and approval gates.
- Dangerous runtime flags such as bypass/yolo/full-access modes must remain
  blocked.
