"""Bounded project routing pointers in SessionDB; no execution or transcript store."""
import json

from hermes_state_runtime import RuntimeStoreError

_DDL = (
    '''CREATE TABLE IF NOT EXISTS project_route_receipts (
        receipt_id TEXT PRIMARY KEY, input_digest TEXT NOT NULL,
        descriptor_json TEXT NOT NULL, session_id TEXT,
        state TEXT NOT NULL DEFAULT 'pinned', version INTEGER NOT NULL DEFAULT 1)''',
    '''CREATE TABLE IF NOT EXISTS project_route_anchors (
        bot TEXT NOT NULL, profile TEXT NOT NULL, chat_id TEXT NOT NULL,
        thread_id TEXT NOT NULL, message_id TEXT NOT NULL, principal TEXT NOT NULL,
        receipt_id TEXT NOT NULL REFERENCES project_route_receipts(receipt_id),
        session_id TEXT NOT NULL, admission_id TEXT, generation INTEGER,
        PRIMARY KEY(bot,profile,chat_id,thread_id,message_id,principal))''',
)


def ensure_schema(db):
    """Lazy additive migration: disabled gateways never call this."""
    def write(conn):
        for ddl in _DDL:
            conn.execute(ddl)
    db._execute_write(write)


def receipt(db, receipt_id):
    with db._read_ctx() as conn:
        row = conn.execute('SELECT * FROM project_route_receipts WHERE receipt_id=?', (receipt_id,)).fetchone()
        return dict(row) if row else None


def pin(db, *, receipt_id, digest, descriptor):
    def write(conn):
        old = conn.execute('SELECT * FROM project_route_receipts WHERE receipt_id=?', (receipt_id,)).fetchone()
        if old:
            if old['input_digest'] != digest:
                raise RuntimeStoreError('admission_conflict')
            return json.loads(old['descriptor_json'])
        conn.execute('INSERT INTO project_route_receipts(receipt_id,input_digest,descriptor_json) VALUES(?,?,?)',
                     (receipt_id, digest, json.dumps(descriptor, sort_keys=True)))
        return descriptor
    return db._execute_write(write)


def bind(conn, descriptor, session_id):
    """Runs INSIDE admission's transaction; a refused admission cannot bind a session."""
    row = conn.execute('SELECT * FROM project_route_receipts WHERE receipt_id=?',
                       (descriptor['receipt_id'],)).fetchone()
    if (row is None or json.loads(row['descriptor_json']) != descriptor
            or row['session_id'] not in (None, session_id)):
        raise RuntimeStoreError('admission_conflict')
    conn.execute("UPDATE project_route_receipts SET session_id=?,state='admitted' WHERE receipt_id=?",
                 (session_id, descriptor['receipt_id']))


def anchor(db, *, bot, profile, chat, thread, message, principal):
    with db._read_ctx() as conn:
        row = conn.execute('''SELECT r.descriptor_json,a.session_id,a.generation,a.admission_id FROM project_route_anchors a
            JOIN project_route_receipts r USING(receipt_id)
            WHERE a.bot=? AND a.profile=? AND a.chat_id=? AND a.thread_id=?
            AND a.message_id=? AND a.principal=?''',
            (bot, profile, chat, thread or '', message, principal)).fetchone()
        return (json.loads(row[0]), row[1], row[2], row[3]) if row else None


def record_anchor(db, descriptor, message_id, *, admission_id=None, generation=None):
    def write(conn):
        row = conn.execute('SELECT session_id FROM project_route_receipts WHERE receipt_id=?',
                           (descriptor['receipt_id'],)).fetchone()
        if row is None or not row[0]:
            raise RuntimeStoreError('admission_conflict')
        action = conn.execute('''SELECT admission_id,generation FROM session_admissions
            WHERE target_session_id=? AND request_id=? ORDER BY seq DESC LIMIT 1''',
            (row[0], descriptor['input_id'])).fetchone()
        action_id = admission_id or (action[0] if action else None)
        action_generation = generation if generation is not None else (action[1] if action else None)
        values = (descriptor['bot'], descriptor['profile'], descriptor['chat_id'],
                  descriptor['thread_id'] or '', str(message_id), descriptor['principal'],
                  descriptor['receipt_id'], row[0], action_id, action_generation)
        conn.execute('INSERT OR IGNORE INTO project_route_anchors VALUES(?,?,?,?,?,?,?,?,?,?)', values)
    db._execute_write(write)


def ensure_choices(db):
    def write(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS project_route_choices (
            token TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE, input_digest TEXT NOT NULL,
            identity_json TEXT NOT NULL, options_json TEXT NOT NULL, expires REAL NOT NULL,
            session_id TEXT, principal_id TEXT, request_id TEXT,
            project_id TEXT, state TEXT NOT NULL DEFAULT 'pending')''')
    db._execute_write(write)


def create_choice(db, *, receipt_id, digest, identity, options, now, ttl=900):
    import secrets
    ensure_choices(db)
    def write(conn):
        conn.execute('''INSERT OR IGNORE INTO project_route_choices
            (token,receipt_id,input_digest,identity_json,options_json,expires) VALUES(?,?,?,?,?,?)''',
            (secrets.token_hex(12), receipt_id, digest, json.dumps(identity, sort_keys=True),
             json.dumps(options), now + ttl))
        row = conn.execute('SELECT * FROM project_route_choices WHERE receipt_id=?', (receipt_id,)).fetchone()
        if row['input_digest'] != digest or json.loads(row['identity_json']) != identity:
            raise RuntimeStoreError('admission_conflict')
        return dict(row)
    return db._execute_write(write)


def get_choice(db, token):
    with db._read_ctx() as conn:
        row = conn.execute('SELECT * FROM project_route_choices WHERE token=?', (token,)).fetchone()
        return dict(row) if row else None


def selected_project(db, receipt_id):
    with db._read_ctx() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_route_choices'").fetchone()
        if not exists:
            return None
        row = conn.execute('SELECT project_id FROM project_route_choices WHERE receipt_id=? AND state=\'chosen\'',
                           (receipt_id,)).fetchone()
        return row[0] if row else None


def bind_choice(conn, token, *, session_id, principal_id, request_id):
    row = conn.execute('SELECT * FROM project_route_choices WHERE token=?', (token,)).fetchone()
    if row is None or row['session_id'] not in (None, session_id):
        raise RuntimeStoreError('admission_conflict')
    conn.execute('UPDATE project_route_choices SET session_id=?,principal_id=?,request_id=? WHERE token=?',
                 (session_id, principal_id, request_id, token))


def choose(db, *, token, index, identity, now, authorize=None):
    """One choice, retryable only as the exact same choice; never grants execution by itself."""
    def write(conn):
        row = conn.execute('SELECT * FROM project_route_choices WHERE token=?', (token,)).fetchone()
        if row is None or json.loads(row['identity_json']) != identity:
            raise RuntimeStoreError('permission_denied')
        if now >= row['expires']:
            raise RuntimeStoreError('choice_expired')
        options = json.loads(row['options_json'])
        if type(index) is not int or index < 0 or index >= len(options) or not row['session_id']:
            raise RuntimeStoreError('invalid_params')
        pid = options[index]['id']
        if authorize is not None:
            authorize(pid)
        if row['project_id'] not in (None, pid):
            raise RuntimeStoreError('choice_used')
        conn.execute("UPDATE project_route_choices SET project_id=?,state='chosen' WHERE token=?", (pid, token))
        return dict(row), pid
    return db._execute_write(write)


def choice_payload(db, row):
    with db._read_ctx() as conn:
        found = conn.execute('''SELECT payload_json FROM session_admissions
            WHERE principal_id=? AND target_session_id=? AND request_id=?''',
            (row['principal_id'], row['session_id'], row['request_id'])).fetchone()
        if found is None:
            raise RuntimeStoreError('choice_expired')
        return json.loads(found[0])


def capture_input(db, receipt_id, digest, payload):
    """Target-independent ingress exists before transcription or project inference."""
    ensure_schema(db)
    def write(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS project_route_inputs (
            receipt_id TEXT PRIMARY KEY, input_digest TEXT NOT NULL, payload_json TEXT NOT NULL,
            prepared_text TEXT, state TEXT NOT NULL DEFAULT 'captured')''')
        conn.execute('INSERT OR IGNORE INTO project_route_inputs(receipt_id,input_digest,payload_json) VALUES(?,?,?)',
                     (receipt_id, digest, json.dumps(payload, sort_keys=True)))
        row = conn.execute('SELECT * FROM project_route_inputs WHERE receipt_id=?', (receipt_id,)).fetchone()
        if row['input_digest'] != digest:
            raise RuntimeStoreError('admission_conflict')
        return dict(row)
    return db._execute_write(write)


def prepare_input(db, receipt_id, text):
    def write(conn):
        conn.execute('UPDATE project_route_inputs SET prepared_text=? WHERE receipt_id=? AND prepared_text IS NULL',
                     (text, receipt_id))
        return conn.execute('SELECT prepared_text FROM project_route_inputs WHERE receipt_id=?', (receipt_id,)).fetchone()[0]
    return db._execute_write(write)


def link_delivery(db, obligation_id, descriptor, source):
    def write(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS project_route_deliveries (
            obligation_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL, source_json TEXT NOT NULL)''')
        conn.execute('INSERT OR IGNORE INTO project_route_deliveries VALUES(?,?,?)',
                     (obligation_id, descriptor['receipt_id'], json.dumps(source, sort_keys=True)))
    db._execute_write(write)


def delivery_pointer(db, obligation_id):
    with db._read_ctx() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_route_deliveries'").fetchone():
            return None
        row = conn.execute('''SELECT r.descriptor_json,d.source_json FROM project_route_deliveries d
            JOIN project_route_receipts r USING(receipt_id) WHERE obligation_id=?''', (obligation_id,)).fetchone()
        return (json.loads(row[0]), json.loads(row[1])) if row else None


def settle_input(db, receipt_id):
    db._execute_write(lambda conn: conn.execute("UPDATE project_route_inputs SET state='settled' WHERE receipt_id=?", (receipt_id,)))


def captured_inputs(db):
    """Bounded scans without keeping a reader open across network awaits."""
    with db._read_ctx() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_route_inputs'").fetchone():
            return
        choices = conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_route_choices'").fetchone()
    recoverable = "i.state='captured'"
    if choices:
        recoverable += """ OR (EXISTS (SELECT 1 FROM project_route_choices c
            WHERE c.receipt_id=i.receipt_id AND c.state='chosen')
            AND NOT EXISTS (SELECT 1 FROM project_route_receipts r
                WHERE r.receipt_id=i.receipt_id AND r.session_id IS NOT NULL))"""
        recoverable = f"""({recoverable}) AND NOT EXISTS (
            SELECT 1 FROM project_route_choices c WHERE c.receipt_id=i.receipt_id
                AND c.state='pending' AND c.expires<=?)"""
        import time
        params = (time.time(),)
    else:
        params = ()
    cursor = 0
    while True:
        with db._read_ctx() as conn:
            rows = conn.execute(f"""SELECT i.rowid,i.payload_json FROM project_route_inputs i
                WHERE ({recoverable}) AND i.rowid>? ORDER BY i.rowid LIMIT 32""", (*params, cursor)).fetchall()
        if not rows:
            return
        for row in rows:
            cursor = row[0]
            yield json.loads(row[1])


def held_input_media(conn):
    """Native admissions own admitted media; unresolved ingress holds only until selection expires."""
    import time
    predicate = "i.state='captured'"
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_route_choices'").fetchone():
        predicate = """(i.state='captured' AND NOT EXISTS (
            SELECT 1 FROM project_route_choices c WHERE c.receipt_id=i.receipt_id
                AND c.state='pending' AND c.expires<=?)) OR (i.state!='settled' AND EXISTS (SELECT 1 FROM project_route_choices c
            WHERE c.receipt_id=i.receipt_id AND (c.state='chosen' OR c.expires>?))
            AND NOT EXISTS (SELECT 1 FROM project_route_receipts r
                WHERE r.receipt_id=i.receipt_id AND r.session_id IS NOT NULL))"""
        params = (time.time(), time.time())
    else:
        params = ()
    rows = conn.execute(f"""SELECT json_extract(i.payload_json,'$.native_text_v1.media')
        FROM project_route_inputs i WHERE {predicate}""", params)
    return {ref['path'] for row in rows for ref in json.loads(row[0] or '[]')}
