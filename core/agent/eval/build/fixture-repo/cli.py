"""Print the weekly summary for a JSON file of rows: python cli.py rows.json"""
import json
import sys

from reports import weekly_summary


def main(argv):
    with open(argv[1], encoding="utf-8") as f:
        rows = json.load(f)
    for line in weekly_summary(rows):
        print(f"{line['rep']}: {line['total']}")


if __name__ == "__main__":
    main(sys.argv)
