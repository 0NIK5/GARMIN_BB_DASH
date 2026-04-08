"""
Одноразовый интерактивный логин в Garmin Connect через garth.

Использует простой прямой SSO flow (без нескольких стратегий), который
иногда проходит Cloudflare там, где стратегии garminconnect падают.

Запуск:
    python login_interactive.py

После успеха токены сохраняются в worker/.garmin_tokens/, и worker сможет
работать без повторного логина до истечения refresh token (обычно ~1 год).
"""
import getpass
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import garth

TOKEN_DIR = os.path.join(os.path.dirname(__file__), ".garmin_tokens")


def prompt_mfa() -> str:
    print()
    return input("Введите MFA код (если пришёл): ").strip()


def main() -> int:
    email = os.environ.get("GARMIN_USERNAME") or input("Email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Password: ")

    if not email or not password:
        print("ERROR: email и password обязательны", file=sys.stderr)
        return 1

    print(f"Logging in as {email}...")
    try:
        garth.login(email, password, prompt_mfa=prompt_mfa)
    except Exception as exc:
        print(f"\nFAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("\nВозможные причины:", file=sys.stderr)
        print("  - Cloudflare bot protection (наиболее вероятно)", file=sys.stderr)
        print("  - Неверный email/password", file=sys.stderr)
        print("  - Аккаунт заблокирован Garmin", file=sys.stderr)
        return 2

    os.makedirs(TOKEN_DIR, exist_ok=True)
    garth.save(TOKEN_DIR)
    print(f"\nOK: токены сохранены в {TOKEN_DIR}")
    print("Теперь worker.py может работать без повторного логина.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
