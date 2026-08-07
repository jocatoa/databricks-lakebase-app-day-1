"""
Support Ticket System - Databricks App

A full-featured ticket management system that:
- Serves a Flask REST API for tickets and messages
- Provides a modern web UI with priority filtering and status tracking
- Reads/writes to Lakebase (Databricks-managed Postgres) via lakebase.py
- Uses foreign key constraints and cascading deletes for data integrity
- Tracks ticket priority (high/medium/low) and status (open/in_progress/resolved)

Database schema:
- tickets: ticket_id (PK), title, priority, status, created_by, created_at
- ticket_messages: message_id (PK), ticket_id (FK), message_text, author, created_at

Run locally:
    python app.py
Deploy as a Databricks App using app.yaml.
"""

import logging
import os
import re

import requests
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify, render_template, request

import lakebase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ticket-hmwk-app")

app = Flask(__name__)
_w = WorkspaceClient()

TICKET_TABLE_NAME = os.environ.get("TICKET_TABLE_NAME", "tickets")
TICKET_MESSAGE_TABLE_NAME = os.environ.get("TICKET_MESSAGE_TABLE_NAME", "ticket_messages")


def ensure_ticket_table():
    """Create the destination table in Lakebase if it doesn't exist yet."""
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {TICKET_TABLE_NAME} (
            ticket_id serial PRIMARY KEY,
            title varchar(100) NOT NULL,
            priority varchar(20) DEFAULT 'low',
            status varchar(20) DEFAULT 'open',
            created_by varchar(100) NOT NULL,
            created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def ensure_ticket_message_table():
    """Create the ticket messages table in Lakebase if it doesn't exist yet."""
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {TICKET_MESSAGE_TABLE_NAME} (
            message_id serial PRIMARY KEY,
            ticket_id bigint NOT NULL,
            message_text text,
            author varchar(100) NOT NULL,
            created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_ticket_id FOREIGN KEY (ticket_id) 
                REFERENCES {TICKET_TABLE_NAME}(ticket_id) 
                ON DELETE CASCADE
        );
        """
    )


def _current_user_email() -> str:
    """
    Resolve the current user's email so the ticket can be personalized.

    Databricks Apps inject the logged-in user's identity via the
    X-Forwarded-Email header on every request. Fall back to the Databricks
    SDK's current_user API for local development where that header isn't set.
    """
    header_email = request.headers.get("X-Forwarded-Email")
    if header_email:
        return header_email
    return _w.current_user.me().user_name


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON (not an HTML error page),
    so the frontend's resp.json() call never chokes on HTML."""
    logger.exception("Unhandled exception while processing request")
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    """Main web UI for creating, viewing, and managing support tickets."""
    return render_template("index.html")


@app.route("/records")
def list_records():
    """Legacy endpoint: read tickets from Lakebase (use GET /tickets for full data)."""
    ensure_ticket_table()
    limit = int(request.args.get("limit", 100))
    rows = lakebase.run_query(
        f"SELECT ticket_id, title, priority, status, created_by FROM {TICKET_TABLE_NAME} ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    return jsonify(rows)





@app.route("/tickets", methods=["GET"])
def get_tickets():
    """View all support tickets."""
    ensure_ticket_table()
    rows = lakebase.run_query(
        f"SELECT ticket_id, title, priority, status, created_by, created_at FROM {TICKET_TABLE_NAME} "
        f"ORDER BY created_at DESC"
    )
    return jsonify(rows)

@app.route("/tickets", methods=["POST"])
def create_ticket():
    """Create a new support ticket."""
    ensure_ticket_table()
    
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    
    title = request.json.get("title", "").strip()
    priority = request.json.get("priority", "low").strip()
    status = request.json.get("status", "open").strip()
    created_by = _current_user_email()
    
    if not title:
        return jsonify({"error": "Title is required"}), 400
    
    # Use run_write_returning to commit and get RETURNING data
    result = lakebase.run_write_returning(
        f"""
        INSERT INTO {TICKET_TABLE_NAME} (title, priority, status, created_by)
        VALUES (%s, %s, %s, %s)
        RETURNING ticket_id, title, priority, status, created_by, created_at
        """,
        (title, priority, status, created_by),
    )
    
    if result:
        return jsonify(result), 201
    return jsonify({"error": "Failed to create ticket"}), 500

@app.route("/tickets/<int:ticket_id>", methods=["PUT"])
def update_ticket(ticket_id):
    """Update a ticket's status, title, or priority."""
    ensure_ticket_table()
    
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    
    status = request.json.get("status", "").strip()
    title = request.json.get("title")
    priority = request.json.get("priority", "").strip()
    
    if not status and not title and not priority:
        return jsonify({"error": "At least one field (status, title, or priority) is required"}), 400
    
    # Build dynamic update query
    updates = []
    params = []
    if status:
        updates.append("status = %s")
        params.append(status)
    if title:
        updates.append("title = %s")
        params.append(title.strip())
    if priority:
        updates.append("priority = %s")
        params.append(priority)
    params.append(ticket_id)
    
    # Use run_write_returning to commit and get RETURNING data
    result = lakebase.run_write_returning(
        f"""
        UPDATE {TICKET_TABLE_NAME}
        SET {', '.join(updates)}
        WHERE ticket_id = %s
        RETURNING ticket_id, title, priority, status, created_by, created_at
        """,
        tuple(params),
    )
    
    if result:
        return jsonify(result)
    return jsonify({"error": "Ticket not found"}), 404


@app.route("/tickets/<int:ticket_id>", methods=["DELETE"])
def delete_ticket(ticket_id):
    """Delete a support ticket with confirmation."""
    ensure_ticket_table()
    
    # Check if ticket exists first
    existing = lakebase.run_query(
        f"SELECT ticket_id FROM {TICKET_TABLE_NAME} WHERE ticket_id = %s",
        (ticket_id,),
    )
    
    if not existing:
        return jsonify({"error": "Ticket not found"}), 404
    
    lakebase.run_write(
        f"DELETE FROM {TICKET_TABLE_NAME} WHERE ticket_id = %s",
        (ticket_id,),
    )
    
    return jsonify({"message": "Ticket deleted successfully", "ticket_id": ticket_id})


@app.route("/tickets/<int:ticket_id>/messages", methods=["GET"])
def get_ticket_messages(ticket_id):
    """Get all messages for a specific ticket."""
    ensure_ticket_message_table()
    
    rows = lakebase.run_query(
        f"""
        SELECT message_id, ticket_id, message_text, author, created_at 
        FROM {TICKET_MESSAGE_TABLE_NAME}
        WHERE ticket_id = %s
        ORDER BY created_at ASC
        """,
        (ticket_id,),
    )
    return jsonify(rows)


@app.route("/tickets/<int:ticket_id>/messages", methods=["POST"])
def add_ticket_message(ticket_id):
    """Add a message to a ticket."""
    ensure_ticket_message_table()
    
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    
    message_text = request.json.get("message_text", "").strip()
    author = _current_user_email()
    
    if not message_text:
        return jsonify({"error": "Message text is required"}), 400
    
    # Verify ticket exists
    ticket = lakebase.run_query(
        f"SELECT ticket_id FROM {TICKET_TABLE_NAME} WHERE ticket_id = %s",
        (ticket_id,),
    )
    
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    
    # Use run_write_returning to commit and get RETURNING data
    result = lakebase.run_write_returning(
        f"""
        INSERT INTO {TICKET_MESSAGE_TABLE_NAME} (ticket_id, message_text, author)
        VALUES (%s, %s, %s)
        RETURNING message_id, ticket_id, message_text, author, created_at
        """,
        (ticket_id, message_text, author),
    )
    
    if result:
        return jsonify(result), 201
    return jsonify({"error": "Failed to add message"}), 500


if __name__ == '__main__':
    host = os.getenv('FLASK_RUN_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_RUN_PORT', 8000))
    app.run(debug=True, host=host, port=port)
    print(f"Flask app running on http://{host}:{port}")