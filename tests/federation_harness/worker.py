"""Test-only durable receipt worker; NOT an AnyGarden node or wire protocol."""
import json
import os
from pathlib import Path
import sqlite3
import sys


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / 'workspace'
    workspace.mkdir(exist_ok=True)
    db = sqlite3.connect(root / 'state.sqlite3')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS identity (node TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS receipts (
            origin TEXT, event TEXT, body TEXT,
            PRIMARY KEY(origin, event));
    ''')
    saved = db.execute('SELECT node FROM identity').fetchone()
    if saved and saved[0] != sys.argv[2]:
        raise ValueError('fixture identity mismatch')
    with db:
        db.execute('INSERT OR IGNORE INTO identity VALUES (?)', (sys.argv[2],))
    for line in sys.stdin:
        request = json.loads(line)
        op = request['op']
        if op == 'identity':
            reply = dict(node=sys.argv[2], pid=os.getpid(), db=str(root / 'state.sqlite3'),
                         workspace=str(workspace), evidence='fixture_only')
        elif op == 'receive':
            key = (request['origin'], request['event'])
            body = json.dumps(request['body'], sort_keys=True, separators=(',', ':'))
            with db:
                inserted = db.execute(
                    'INSERT OR IGNORE INTO receipts VALUES (?, ?, ?)', (*key, body)
                ).rowcount
                saved_body = db.execute(
                    'SELECT body FROM receipts WHERE origin=? AND event=?', key
                ).fetchone()[0]
                status = ('stored' if inserted else 'duplicate') if saved_body == body else 'conflict'
            reply = dict(status=status)
        elif op == 'count':
            reply = dict(count=db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0])
        else:
            reply = dict(error='unsupported_fixture_operation')
        print(json.dumps(reply), flush=True)


if __name__ == '__main__':
    main()
