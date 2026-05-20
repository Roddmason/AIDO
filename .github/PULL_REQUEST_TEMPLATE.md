## Summary

- 

## Testing

- [ ] `uv run pytest tests_py -q`
- [ ] `corepack pnpm@10.24.0 run typecheck:web`
- [ ] `corepack pnpm@10.24.0 run build:web`
- [ ] `corepack pnpm@10.24.0 run test:web`
- [ ] `corepack pnpm@10.24.0 run security:secrets`
- [ ] `corepack pnpm@10.24.0 run security:sast`

## Security Checklist

- [ ] No secrets, API keys, tokens, `.env` files, local databases, prompt dumps, or generated artifacts are committed.
- [ ] Provider credentials use `credentialRef` values such as `env:NVIDIA_NIM_API_KEY` or `keyring:aido/nvidia_nim`.
- [ ] Real provider calls and CLI runtime execution remain behind policy, budget, quota, workspace, and approval gates.
- [ ] Dangerous runtime flags remain blocked.

## Notes For Owner Review

- 
