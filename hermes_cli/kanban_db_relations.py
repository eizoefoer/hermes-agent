"""Relations removed by the explicit native task hard-delete APIs."""


def delete_task_relations(conn, task_id):
    """Remove native history before the task; normal archival retains it."""
    conn.execute("DELETE FROM task_links WHERE parent_id = ? OR child_id = ?", (task_id, task_id))
    for table in ("task_comments", "task_events", "task_runs", "kanban_notify_subs"):
        conn.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))
