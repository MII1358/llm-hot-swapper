import argparse
import json
import shutil
from pathlib import Path


def resolve_source_dir() -> Path:
    return Path(__file__).resolve().parent


def resolve_target_home(raw_home: str | None) -> Path:
    if raw_home:
        return Path(raw_home).expanduser().resolve()

    candidates = [
        Path.home() / "AppData" / "Local" / ".openworld" / "hermes",
        Path.home() / ".hermes",
    ]
    for candidate in candidates:
        if (candidate / "auth.json").exists() or (candidate / "config.yaml").exists():
            return candidate.resolve()
    return candidates[0].resolve()


def copy_file(src: Path, dst: Path, force: bool) -> None:
    if dst.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file without --force: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def write_target_config(src: Path, dst: Path, force: bool, provider: str, models: list[str]) -> None:
    if dst.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file without --force: {dst}")

    data = json.loads(src.read_text(encoding="utf-8"))
    data["provider_pool"] = provider
    if models:
        data["model_cycle"] = models

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Hot Swapper into a Hermes home")
    parser.add_argument("--hermes-home", help="Target Hermes home directory")
    parser.add_argument("--force", action="store_true", help="Overwrite existing exported files")
    parser.add_argument("--provider", default="openrouter", help="Credential pool name, default: openrouter")
    parser.add_argument(
        "--models",
        help="Comma-separated model cycle to write into hot_swapper.config.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show planned changes without copying files")
    args = parser.parse_args()

    source_dir = resolve_source_dir()
    target_home = resolve_target_home(args.hermes_home)
    target_swapper_dir = target_home / "swapper"
    target_logs_dir = target_swapper_dir / "logs"
    target_runtime = target_swapper_dir / "swapper.py"
    target_config = target_swapper_dir / "hot_swapper.config.json"
    config_source = source_dir / "hot_swapper.config.example.json"
    models = [item.strip() for item in (args.models or "").split(",") if item.strip()]

    auth_file = target_home / "auth.json"
    hermes_config = target_home / "config.yaml"

    print(f"Hermes home: {target_home}")
    print(f"Runtime target: {target_runtime}")
    print(f"Config target: {target_config}")
    if not auth_file.exists():
        print(f"Warning: auth.json was not found at {auth_file}")
    if not hermes_config.exists():
        print(f"Warning: config.yaml was not found at {hermes_config}")

    if args.dry_run:
        print("Dry run only. No files were changed.")
        return

    target_home.mkdir(parents=True, exist_ok=True)
    target_swapper_dir.mkdir(parents=True, exist_ok=True)
    target_logs_dir.mkdir(parents=True, exist_ok=True)

    copy_file(source_dir / "hot_swapper.py", target_runtime, args.force)
    write_target_config(config_source, target_config, args.force, args.provider, models)

    print(f"Installed into: {target_swapper_dir}")
    print("Next steps:")
    print(f"  1. Put API keys in {auth_file}")
    print(f"  2. Edit models in {target_config}")
    print(f"  3. Run: python {target_runtime} status --hermes-home {target_home}")


if __name__ == "__main__":
    main()
