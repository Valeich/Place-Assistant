"""Run a product survey or menu check directly, with no chat agent in front.

Usage:
    # Which restaurants and cafes in Menteng sell Aqua, and at what price?
    python run_product_survey.py "Menteng, Jakarta, Indonesia" --product Aqua \
        --alias "Aqua Reflections" --description "Danone bottled mineral water brand" \
        --type restaurant --type cafe --limit 10

    # Does each of these outlets sell it?
    python run_product_survey.py "Jakarta, Indonesia" --product Aqua \
        --places "Sate Khas Senayan Menteng" "Bakmi GM Sarinah"

    # Full menus with prices for named places (no product)
    python run_product_survey.py "Jakarta, Indonesia" --places "Anomali Coffee Menteng"
"""

import argparse

from dotenv import load_dotenv

load_dotenv()

from place_assistant.pipeline import check_named_places, survey_product  # noqa: E402 (must follow load_dotenv)


def main() -> None:
    """Reads command-line options, runs the survey or menu check, and prints
    the counts, the places with evidence and the output file paths.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("area", help='Area name, e.g. "Menteng, Jakarta, Indonesia"')
    parser.add_argument("--product", default="", help="product or brand to look for")
    parser.add_argument("--alias", action="append", default=[], help="another name for the same product (repeatable)")
    parser.add_argument("--description", default="", help='what the product is, e.g. "Danone bottled mineral water brand"')
    parser.add_argument("--type", dest="place_types", action="append", default=[], help="Google place type (repeatable, max 3)")
    parser.add_argument("--limit", type=int, default=20, help="places to check in an area survey (each one is billed)")
    parser.add_argument("--places", nargs="+", help="check these named places instead of surveying the area")
    parser.add_argument("--no-third-party", action="store_true", help="skip the delivery-app/food-blog search")
    args = parser.parse_args()

    if args.places:
        result = check_named_places(
            args.places, args.area, args.product, args.alias, args.description,
            search_third_party=not args.no_third_party,
        )
    else:
        if not args.product:
            parser.error("--product is required unless --places is given")
        result = survey_product(
            args.area, args.product, args.place_types or None, args.alias, args.description, args.limit,
            search_third_party=not args.no_third_party,
        )

    summary = result["summary"]
    print(f"Checked {summary['checked']} place(s) in '{summary['area']}'")
    for status, count in summary["status_counts"].items():
        if count:
            print(f"  {status}: {count}")
    if summary.get("price_summary"):
        p = summary["price_summary"]
        print(f"  prices: {p['count']} in {p['currency']}, min {p['min']:,.0f}, median {p['median']:,.0f}, max {p['max']:,.0f}")
    for place in result["places"]:
        found = place.get("evidence") or (place.get("menu") or {}).get("items") or []
        if found:
            print(f"- {place.get('name')}: {place['status']}")
            for entry in found[:5]:
                source = f" [{entry['source']}]" if "source" in entry else ""
                print(f"    {entry.get('item')}: {entry.get('price') or 'no price'}{source}")
    for label in ("json", "csv", "docx"):
        if result.get(f"{label}_path"):
            print(f"{label.upper()}: {result[f'{label}_path']}")


if __name__ == "__main__":
    main()
