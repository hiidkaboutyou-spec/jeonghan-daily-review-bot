"""Inspect durable shadow truth; recovery requires an explicit interrupted run ID."""
import argparse
import json
from pathlib import Path

from app.completeness_engine import CompletenessEngine
from app.source_ledger import SourceLedgerStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('.state/private-review.sqlite3'))
    parser.add_argument('--run-id')
    parser.add_argument('--recover-interrupted-run', action='store_true')
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error('Completeness database does not exist')
    if args.recover_interrupted_run and not args.run_id:
        parser.error('Recovery requires the exact interrupted --run-id')
    store = SourceLedgerStore(args.db)
    try:
        engine = CompletenessEngine(store)
        row = store.conn.execute('SELECT run_id FROM completeness_attempts ORDER BY sequence DESC LIMIT 1').fetchone()
        run_id = args.run_id or (row['run_id'] if row else '')
        if args.recover_interrupted_run:
            engine.close_run(run_id)
        print(json.dumps(engine.report(run_id), ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
