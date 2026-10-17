"""Real notifier/SQLite/process/HTTP crash-window probe; no external messaging.

This characterizes the known at-least-once boundary, not exactly-once acceptance.
"""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

import pytest


def _notifier_process(db_path, url, task_id, crash, now):
    from gateway.config import Platform
    from gateway.kanban_watchers_notifier import _KanbanNotification
    from gateway.run import GatewayRunner
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    os.environ['HERMES_KANBAN_DB'] = db_path
    time.time = lambda: now

    class HTTPAdapter:
        def accepted_send(self, chat_id, text):
            body = json.dumps({'chat_id': chat_id, 'text': text}).encode()
            with urlopen(Request(url, data=body, method='POST'), timeout=10) as response:
                assert response.status == 200

        async def send(self, chat_id, text, metadata=None):
            await asyncio.to_thread(self.accepted_send, chat_id, text)
            if crash:
                os._exit(73)  # remote accepted; notifier has not checkpointed the ping

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: HTTPAdapter()}
    with kbc.connect_closing() as conn:
        sub = kbn.list_notify_subs(conn, task_id)[0]
        metadata = {}
        old, cursor, events = kbn.claim_unseen_events_for_sub(
            conn, task_id=task_id, platform='telegram', chat_id='isolated-origin',
            kinds=['completed'], claim_metadata=metadata,
        )
        assert len(events) == 1
        sub['_claim_expires_at'] = metadata['claim_expires_at']
        batch = dict(sub=sub, old_cursor=old, cursor=cursor, events=events,
                     task=kb.get_task(conn, task_id), board=None)
    asyncio.run(_KanbanNotification(runner, batch, platform_cls=Platform,
                                   sub_fail_counts={}).deliver())


@pytest.mark.platforms('linux')
def test_remote_acceptance_before_process_death_replays_same_event(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    db = tmp_path / 'isolated-board.db'
    monkeypatch.setenv('HERMES_KANBAN_DB', str(db))
    monkeypatch.setattr(time, 'time', lambda: 10000)
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title='crash boundary', assignee='fixture')
        kbn.add_notify_sub(conn, task_id=tid, platform='telegram',
                           chat_id='isolated-origin', delivery_mode='notify')
        kb.complete_task(conn, tid, summary='isolated result', force=True)

    accepted = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            accepted.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/accepted'
    try:
        command = [sys.executable, str(Path(__file__).resolve()), str(db), url, tid]
        first = subprocess.run(command + ['crash', '10000'], capture_output=True, text=True, timeout=45, check=False)
        assert first.returncode == 73, first.stdout + first.stderr
        assert len(accepted) == 1
        with kbc.connect_closing() as conn:
            row = kbn.list_notify_subs(conn, tid)[0]
            assert row['last_ping_event_id'] == 0
            assert row['claim_old_cursor'] is not None
            assert row['claim_expires_at'] == 10300
        retry = subprocess.run(command + ['settle', '11000'], capture_output=True, text=True, timeout=45, check=False)
        assert retry.returncode == 0, retry.stdout + retry.stderr
        assert len(accepted) == 2, 'characterization: remote acceptance is replayed after crash'
        assert accepted[0] == accepted[1]
        with kbc.connect_closing() as conn:
            row = kbn.list_notify_subs(conn, tid)[0]
            assert row['last_ping_event_id'] > 0
            assert row['claim_old_cursor'] is None
        print(json.dumps({'source': 'real notifier + SQLite + child process + loopback HTTP',
                          'crash_exit': first.returncode, 'accepted_sends': len(accepted),
                          'identical_origin_and_payload': accepted[0] == accepted[1],
                          'exactly_once_assertion': 'DISPROVED', 'settled_after_retry': True}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


if __name__ == '__main__':
    _notifier_process(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == 'crash', int(sys.argv[5]))
