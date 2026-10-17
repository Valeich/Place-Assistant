"""Run the place-collection pipeline directly, with no chat agent in front.

Usage:
    python run_collect_places.py "Jakarta Pusat, Indonesia" --type restaurant --limit 20
    python run_collect_places.py "Menteng, Jakarta, Indonesia" --type bar --require-alcohol
"""

import argparse

from dotenv import load_dotenv

load_dotenv()

from place_assistant.pipeline import collect_places  # noqa: E402 (must follow load_dotenv)


def main() -> None:
    """Reads command-line options, runs the collection pipeline, and prints the
    counts and output file paths.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("area", help='Area name, e.g. "Jakarta Pusat, Indonesia"')
    parser.add_argument(
        "--type",
        dest="place_type",
        default="cafe",
        help='Google Place type, e.g. "cafe", "restaurant", "bar", "museum", "tourist_attraction"',
    )
    parser.add_argument("--limit", type=int, default=20, help="max places to enrich (each one is billed)")
    parser.add_argument(
        "--require-alcohol",
        action="store_true",
        help="keep only places Google marks as serving beer, wine or cocktails",
    )
    args = parser.parse_args()

    result = collect_places(
        args.area, place_type=args.place_type, limit=args.limit, require_alcohol=args.require_alcohol
    )
    print(
        f"Checked {result['checked']}, collected {result['collected']} "
        f"{result['place_type']}(s) for '{result['area']}'"
    )
    for label in ("json", "csv", "docx"):
        if result.get(f"{label}_path"):
            print(f"{label.upper()}: {result[f'{label}_path']}")
    if result.get("capped_at_60"):
        print("[warning] Hit Google's 60-result search cap — area may have more places.")


if __name__ == "__main__":
    main()
