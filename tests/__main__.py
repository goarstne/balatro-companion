"""One entry point for local checks and the autolearner: python -m tests [--core]."""
import argparse
import subprocess
import sys
from balatro.checks import modules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", action="store_true", help="omit local screenshot-dataset tests")
    args = parser.parse_args()
    failed = []
    for name in modules(args.core):
        print(f"\n=== {name} ===", flush=True)
        try:
            result = subprocess.run([sys.executable, "-m", f"tests.{name}"], timeout=180)
            if result.returncode:
                failed.append(name)
        except subprocess.TimeoutExpired:
            failed.append(name + " (timeout)")
    print("\nFAILED: " + ", ".join(failed) if failed else "\nAll test scripts passed.", flush=True)
    return bool(failed)


if __name__ == "__main__":
    sys.exit(main())
