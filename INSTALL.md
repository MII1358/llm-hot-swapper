# Install

Hot Swapper is designed for a Hermes-style setup with:

- `auth.json`
- `config.yaml`
- `credential_pool.<provider>`

The tested pattern is:

- API keys in `auth.json`
- current default model in `config.yaml`
- model rotation in `hot_swapper.config.json`

## 1. Install Dependencies

```bash
pip install -r requirements.txt
```

## 2. Install Into Hermes

```bash
python install_into_hermes.py --hermes-home %LOCALAPPDATA%\.openworld\hermes
```

Optional: write the model cycle during install:

```bash
python install_into_hermes.py --hermes-home %LOCALAPPDATA%\.openworld\hermes --models "provider/primary-model,provider/fallback-model,provider/reserve-model"
```

The installer creates:

```text
<hermes-home>/
  swapper/
    swapper.py
    hot_swapper.config.json
    logs/
```

It does not overwrite existing files unless you pass `--force`.

## 3. Add Keys

Keys belong in:

```text
<hermes-home>\auth.json
```

Example shape:

```json
{
  "credential_pool": {
    "openrouter": [
      {
        "id": "slot_1",
        "label": "Primary",
        "priority": 0,
        "access_token": "sk-or-v1-REPLACE_ME",
        "base_url": "https://openrouter.ai/api/v1"
      },
      {
        "id": "slot_2",
        "label": "Fallback",
        "priority": 1,
        "access_token": "sk-or-v1-REPLACE_ME",
        "base_url": "https://openrouter.ai/api/v1"
      }
    ]
  }
}
```

Do not commit real keys.

## 4. Add Models

Models belong in:

```text
<hermes-home>\swapper\hot_swapper.config.json
```

Example:

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

## 5. Verify

From the installed `swapper` directory:

```bash
cd C:\path\to\hermes\swapper
python swapper.py status --hermes-home C:\path\to\hermes
python swapper.py check --hermes-home C:\path\to\hermes
```

Only run a real API request when you are ready:

```bash
python swapper.py test --hermes-home C:\path\to\hermes
```

## What Gets Updated

When sticky swap succeeds, Hot Swapper can update:

- `auth.json` slot priorities
- `config.yaml` `model.default`

That is how the successful fallback becomes the next default.
