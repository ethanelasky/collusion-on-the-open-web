"""Add readable configuration summaries and tags to existing Docent runs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_collusion.docent_cli import make_client
from ai_collusion.docent_presentation import annotate_presentation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-id", required=True)
    args = parser.parse_args()
    client = make_client()
    ids = client.list_agent_run_ids(args.collection_id)
    with ThreadPoolExecutor(max_workers=4) as pool:
        counts = list(pool.map(lambda run_id: annotate_presentation(
            client, args.collection_id, [run_id]), ids))
    print(f"Verified presentation metadata for {sum(counts)} existing runs")


if __name__ == "__main__":
    main()
