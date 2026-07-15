# Google Gemini Provider

AIDO supports Gemini through Google's OpenAI-compatible Gemini API endpoint. The
adapter keeps the same provider contract and credential-vault boundary as the
other hosted model providers. A key entered in the browser is sent once to the
backend credential vault, is never kept in React state or provider metadata,
and is not returned to the browser after storage or committed to the repository.

## Configure an API key

1. Create a new authorization (auth) key in [Google AI Studio](https://ai.google.dev/gemini-api/docs/api-key),
   or verify and migrate an existing key. Google says new AI Studio keys are
   auth keys and Standard keys will stop working with the Gemini API in
   September 2026.
2. In **Settings**, enable the persisted global `runtime.remote.enabled`
   control. If the project has a runtime override, also enable
   `project.runtime.remote.enabled` for that project. The legacy
   `AIDO_ENABLE_REAL_PROVIDER_CALLS` environment flag is not authoritative.
3. Provide the key through the Providers wizard (the preferred path) or the
   process environment:

   ```text
   AIDO_GEMINI_API_KEY=<secret>
   AIDO_GEMINI_MODEL=gemini-3.5-flash
   ```

`GEMINI_API_KEY` remains a compatibility fallback. AIDO-specific configuration
uses `AIDO_GEMINI_API_KEY` first. Do not put a key in frontend code, a committed
`.env` file, logs, task text, or provider metadata. AIDO does not silently use
`GOOGLE_API_KEY`, which avoids ambiguous credentials when Google SDKs and other
Google services share the same process.

Environment variables provide the secret and optional model override; they do
not enable the persisted provider account or declare its billing tier. After
setting them, open **Providers**, configure Gemini with the reference
`env:AIDO_GEMINI_API_KEY`, choose Free or Paid/configured, then sync models and
validate the connection. Strict free-tier routing remains blocked while the
account tier is `unknown`.

The Providers wizard stores a pasted key in the configured credential vault and
sends only its `credentialRef` to the provider account. Existing `env:`,
keyring, OpenBao, or Vault references can be selected instead.

## Free tier is an account policy, not a model price

The Gemini wizard defaults to **Free tier**, but it requires the operator to
attest that Google AI Studio shows the project on that tier before saving
`pricingMode=free`. Choose **Paid / configured** when billing is enabled for the
Google AI project. AIDO cannot inspect Google billing state, so this is an
operator declaration rather than automatic verification. Merely choosing a
model offered on Google's free tier does not make a billed project free.

Use the `free_tier` routing mode for strict zero-cost routing. It considers only
models that support a free tier on provider accounts explicitly marked free,
plus eligible local zero-cost runtimes. It does not silently fall back to paid
or unknown-cost candidates. `free_first` remains a preference mode and can use a
non-free fallback when policy permits it.

The Product Owner default uses `gemini-3.5-flash` for planning, with
`gemini-3.1-flash-lite` as the high-volume fallback. Base profiles and roles may
consider every configured provider by default; capability, health, privacy,
budget, and routing-policy gates still apply.

## Context window and quotas

Gemini 3.5 Flash supports up to **1,048,576 input tokens per request**. That is a
context-window limit, not a quota of one million free tokens. Google's free-tier
limits are dynamic, apply per project and model, and can include requests per
minute, input tokens per minute, and requests per day. Check the effective
limits for the project in Google AI Studio instead of hard-coding a fixed free
allowance. Daily request limits reset at midnight Pacific time.

See Google's current [Gemini 3.5 Flash model details](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash),
[rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits),
and [pricing page](https://ai.google.dev/gemini-api/docs/pricing) before changing
production budgets or quota settings.

## Free-tier data handling

Under Google's current Gemini API terms, content submitted through unpaid
services may be used to provide, improve, and develop Google products and
machine-learning technologies, and may be reviewed by humans. Do not submit
sensitive, confidential, regulated, personal, customer, credential, or
proprietary backlog data through the free tier.

Use a paid/configured Gemini account or a policy-approved local provider for such
work, and keep the task privacy classification accurate. Review the current
[Gemini API Terms of Service](https://ai.google.dev/gemini-api/terms) and
[pricing/data-use disclosures](https://ai.google.dev/gemini-api/docs/pricing)
for the project and jurisdiction before production use.

## Recommended models

| Model | AIDO use | Notes |
| --- | --- | --- |
| `gemini-3.5-flash` | Product Owner planning and complex agentic work | Stable default; 1,048,576-token input context window. |
| `gemini-3.1-flash-lite` | High-volume planning support, classification, and routing | Stable, lower-cost fallback for simpler work. |

Provider availability, model lifecycle, pricing, and quotas can change. Treat
Google AI Studio and the official pages linked above as the runtime source of
truth.
