# AI Install Instructions

Use this file when asking another coding agent to install Hot Swapper into a project.

## Task

Install Hot Swapper safely into the target project.

## Rules

- Do not print or commit real API keys.
- Do not overwrite an existing `auth.json`.
- Do not delete existing provider pool entries.
- Do not patch unrelated runtime code unless the user explicitly asks.
- Prefer installing Hot Swapper as a sidecar under `swapper/`.

## Find The Hermes Home

Look for:

- `auth.json`
- `config.yaml`
- `credential_pool`
- `model.default`

Common locations:

- `%USERPROFILE%\.hermes`
- `%USERPROFILE%\AppData\Local\.openworld\hermes`
- `<repo>\hermes`

## Install Files

Create:

```text
<hermes-home>\swapper\
```

Copy:

```text
hot_swapper.py -> <hermes-home>\swapper\swapper.py
hot_swapper.config.example.json -> <hermes-home>\swapper\hot_swapper.config.json
```

Create:

```text
<hermes-home>\swapper\logs\
```

## Configure Keys

Keys belong in:

```text
<hermes-home>\auth.json
```

Use:

```text
credential_pool.openrouter[].access_token
```

Preserve existing keys and sources. If no key exists, ask the user for one or leave a template.

## Configure Models

Models belong in:

```text
<hermes-home>\swapper\hot_swapper.config.json
```

Use:

```json
{
  "provider_pool": "openrouter",
  "model_cycle": [
    "provider/primary-model",
    "provider/fallback-model",
    "provider/reserve-model"
  ],
  "sticky_swap": true,
  "persist_auth_priority": true,
  "persist_config_default": true
}
```

## Verify

Run:

```bash
cd <hermes-home>\swapper
python swapper.py status --hermes-home <hermes-home>
```

Then, if real keys are present and the user approves:

```bash
python swapper.py check --hermes-home <hermes-home>
python swapper.py test --hermes-home <hermes-home>
```

## Report Back

At the end, tell the user:

- where the files were installed
- whether `auth.json` was found
- where models should be edited
- what validation command passed
