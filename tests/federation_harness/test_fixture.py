"""Self-tests for harness plumbing only; these do not accept product behavior."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

WORKER = Path(__file__).with_name('worker.py')


class Node:
    def __init__(self, root, identity):
        self.root, self.identity = root, identity
        self.process = None
        self.start()

    def start(self):
        # Explicit environment prevents inheriting provider/server credentials.
        self.process = subprocess.Popen(
            [sys.executable, '-I', str(WORKER), str(self.root), self.identity],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={'PYTHONIOENCODING': 'utf-8'},
        )

    def request(self, **payload):
        if self.process.poll() is not None:
            raise ConnectionError('fixture offline')
        self.process.stdin.write(json.dumps(payload) + '\n')
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 5)[0]:
            raise TimeoutError('fixture response deadline')
        line = self.process.stdout.readline()
        if not line:
            raise ConnectionError('fixture closed transport')
        return json.loads(line)

    def stop(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Node(Path(self.tmp.name) / 'a', 'node-a')
        self.addCleanup(self.a.stop)
        self.b = Node(Path(self.tmp.name) / 'b', 'node-b')
        self.addCleanup(self.b.stop)

    def event(self):
        return dict(op='receive', origin='node-a', event='event-1', body={'text': 'fake'})

    def test_independent_process_identity_database_and_workspace(self):
        a, b = [node.request(op='identity') for node in (self.a, self.b)]
        for field in ('pid', 'node', 'db', 'workspace'):
            self.assertNotEqual(a[field], b[field])
        self.assertNotIn(os.getpid(), (a['pid'], b['pid']))
        for item in (a, b):
            self.assertTrue(Path(item['db']).is_file())
            self.assertTrue(Path(item['workspace']).is_dir())
        self.a.request(**self.event())
        self.assertEqual(self.b.request(op='count')['count'], 0)

    def test_lost_ack_replay_after_receiver_restart(self):
        self.assertEqual(self.b.request(**self.event())['status'], 'stored')
        # Controller discards this ACK from sender's view, then crashes receiver.
        self.b.stop()
        self.b.start()
        self.assertEqual(self.b.request(**self.event())['status'], 'duplicate')
        self.assertEqual(self.b.request(op='count')['count'], 1)

    def test_canonical_contract_payload_survives_transport_and_restart(self):
        # Data preservation only: the worker never authorizes/applies commands.
        fixture = WORKER.parents[2] / 'contracts/federation/v1/scenarios.json'
        cases = json.loads(fixture.read_text())
        case = next(c for c in cases['scenarios'] if c['name'] == 'normal_completion')
        commands = [step['command'] for step in case['steps']]
        identities = {command['sender_node_id'] for command in commands}
        self.assertEqual(len(identities), 2)
        for command in commands:
            self.assertEqual(self.b.request(
                op='receive', origin=command['sender_node_id'],
                event=command['request_id'], body=command,
            )['status'], 'stored')
        self.b.stop()
        self.b.start()
        for command in commands:
            self.assertEqual(self.b.request(
                op='receive', origin=command['sender_node_id'],
                event=command['request_id'], body=command,
            )['status'], 'duplicate')
        db = sqlite3.connect(self.b.root / 'state.sqlite3')
        try:
            for command in commands:
                stored = db.execute(
                    'SELECT body FROM receipts WHERE origin=? AND event=?',
                    (command['sender_node_id'], command['request_id']),
                ).fetchone()[0]
                self.assertEqual(json.loads(stored), command)
        finally:
            db.close()
        self.assertEqual(self.b.request(op='count')['count'], len(commands))

    def test_conflicting_replay_and_origin_scoping(self):
        event = self.event()
        self.b.request(**event)
        event['body'] = {'text': 'different'}
        self.assertEqual(self.b.request(**event)['status'], 'conflict')
        self.assertEqual(self.b.request(op='count')['count'], 1)
        event['origin'] = 'node-c'
        self.assertEqual(self.b.request(**event)['status'], 'stored')

    def test_peer_offline_local_fixture_remains_available(self):
        self.a.stop()
        with self.assertRaises(ConnectionError):
            self.a.request(**self.event())
        self.assertEqual(self.b.request(**self.event())['status'], 'stored')

    def test_concurrent_deliveries_share_durable_receipt(self):
        # Second transport process for the SAME receiver DB, not a third node.
        transport = Node(self.b.root, self.b.identity)
        self.addCleanup(transport.stop)
        barrier = threading.Barrier(2, timeout=5)

        def deliver(node):
            barrier.wait()
            return node.request(**self.event())

        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(deliver, [self.b, transport]))
        self.assertEqual(sorted(r['status'] for r in replies), ['duplicate', 'stored'])
        self.assertEqual(self.b.request(op='count')['count'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
