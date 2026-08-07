# Support Ticket System - Lakebase Databricks App

A full-featured support ticket management system built as a Databricks App that:
- Connects to **Lakebase** (Databricks-managed Postgres) using a single `LAKEBASE_URL` secret (a native Postgres role with a static password)
- Provides a modern web UI for creating, viewing, editing, and managing support tickets
- Supports ticket prioritization (high/medium/low) and status tracking (open/in progress/closed)
- Includes threaded messaging on tickets for team collaboration
- Leverages Lakebase's foreign key constraints and cascading deletes for data integrity

## Files

- `app.py` - Flask app with REST API for tickets and messages
- `lakebase.py` - Lakebase connection helper (psycopg2 with transaction support)
- `templates/index.html` - Modern, responsive web UI with filtering, validation, and real-time updates
- `app.yaml` - Databricks App deployment config (command + env vars)
- `.env.example` - Local dev env var template (copy to `.env`, do not commit real values)

## Step-by-step setup

### 1. Create a Lakebase instance and a native-password role

1. In your Databricks workspace, go to **Catalog** (left sidebar) and select the **Lakebase** tab (or search "Lakebase" in the workspace search bar).
2. Click **Create Lakebase instance** (sometimes labeled **Create database instance**).
   - Give it a name (e.g. `ticket-system-db`).
   - Choose the capacity/compute size and region appropriate for your workload (defaults are fine to start).
   - Click **Create** and wait for the instance to reach the **Available**/**Running** state.
3. Open the newly created instance, then go to the **Roles & Databases** tab (sometimes called **Permissions** or **Roles**).
4. **Enable native (password) authentication** for the instance if it isn't already on:
   - Look for an authentication setting such as **Native passwords** or **Password authentication** and toggle/enable it. By default some Lakebase instances only support OAuth/token-based auth — you need password auth enabled so the role below gets a static password instead of a short-lived token.
5. **Create a new role**:
   - Click **Add role** / **Create role**.
   - Choose **Password** as the authentication method (not OAuth).
   - Name the role (e.g. `ticket_app`) and let Databricks generate (or set) a password.
6. **Copy the connection URL** shown for the role. It will look like:

   ```
   postgresql://<role>:<password>@<host>.database.cloud.databricks.com:5432/databricks_postgres?sslmode=require
   ```

   Keep this URL — you'll use it in the next step for local development and app deployment.

### 2. Configure environment variables (local dev)

Copy `.env.example` to `.env` and paste your Lakebase URL as `LAKEBASE_URL` for local runs:

```bash
cp .env.example .env
# Edit .env and set LAKEBASE_URL to your connection string from step 1
```

For deployment as a Databricks App, you'll configure the `LAKEBASE_URL` in the Apps UI (see deployment steps below).

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run locally

```bash
python app.py
```

The app will start on `http://localhost:8000`. Open it in your browser to create and manage support tickets.

### 5. Deploy as a Databricks App (optional)

All of this is done through the Databricks workspace UI:

1. **Create a Git folder**:
   - In the Databricks workspace sidebar, click **Workspace** > **Create** > **Git folder** (in older UIs this is called **Repos** > **Add Repo**).
   - Paste the Git URL of this project's repository (e.g. your GitHub/GitLab remote for this codebase).
   - Choose a folder name and click **Create Git folder**. Databricks will clone the repo directly into your workspace — this becomes the source for your app.

2. **Create the Databricks App**:
   - In the sidebar, go to **Compute** > **Apps** (or search "Apps" in the workspace search bar).
   - Click **Create app**, then choose **Custom** (or "From scratch").
   - Give the app a name (e.g. `support-ticket-system`).

3. **Point the app at your Git folder**:
   - When prompted for the source code location, select **Workspace files** / **Git folder** and browse to the Git folder you created in step 1 (the folder containing `app.py` and `app.yaml`).
   - Databricks will read `app.yaml` from that folder automatically to configure the `command`.
   - Set the environment variable `LAKEBASE_URL` to your connection string from step 1 in the Apps UI environment variables section.

4. **Deploy**:
   - Click **Deploy** (or **Create and deploy**) in the Apps UI. Databricks will build and start the app using the Git folder's current contents — no `databricks` CLI commands are needed.
   - Whenever you update the code, pull the latest changes into the Git folder (**Git folder** > **Pull**, via the UI) and click **Deploy** again in the Apps UI to redeploy.

5. Once deployed, open the app's URL from the Apps UI to start using the ticket system!

## API Endpoints

### Core
- `GET /` - Main web UI for ticket management
- `GET /healthz` - Health check

### Tickets
- `GET /tickets` - List all support tickets with priority and status
- `POST /tickets` - Create a new ticket (JSON body: `{"title": "...", "priority": "high|medium|low", "status": "open|in_progress|closed"}`)
- `PUT /tickets/<id>` - Update a ticket's title, priority, or status
- `DELETE /tickets/<id>` - Delete a ticket (cascades to messages)

### Messages
- `GET /tickets/<id>/messages` - Get all messages for a ticket
- `POST /tickets/<id>/messages` - Add a message to a ticket (JSON body: `{"message_text": "..."}`)

### Legacy
- `GET /records?limit=100` - List tickets (legacy endpoint)

## Enabling Change Data Feed (CDF) for the Ticket System

Lakebase supports **Change Data Feed (CDF)**, a managed way to stream row-level inserts/updates/deletes
from your Lakebase Postgres tables into Unity Catalog Delta tables (no Debezium, no custom connectors).
CDF is enabled per-**schema** in the `databricks_postgres` database, and every table in that schema that
meets two conditions is picked up automatically: it has `REPLICA IDENTITY FULL` set, and it has at least
one row.

### 1. Set `REPLICA IDENTITY FULL` on your ticket tables

By default, Postgres only logs primary-key columns on change. To capture full row contents (needed for
CDF), enable `REPLICA IDENTITY FULL` on the `tickets` and `ticket_messages` tables:

```sql
ALTER TABLE tickets REPLICA IDENTITY FULL;
ALTER TABLE ticket_messages REPLICA IDENTITY FULL;
```

Run this once per table, either from a Databricks SQL editor connected to your Lakebase instance, or
from a `psql` session using your `LAKEBASE_URL`. Any new table you add later (e.g. via `ensure_table`-style
helpers in `app.py`) needs the same `ALTER TABLE ... REPLICA IDENTITY FULL` statement run once before it
will be included in the feed. Tables with the setting but zero rows are skipped until the first row is
inserted, then picked up automatically.

You can confirm which tables currently qualify by querying:

```sql
SELECT * FROM wal2delta.tables;
```

### 2. Start CDF from the Lakebase UI

1. In your Databricks workspace, open the **Lakebase** tab for your instance.
2. Go to **Lakebase CDF** and click **Start**.
3. Select the `databricks_postgres` database and the schema containing your tables (the default
   schema, `public`, works — it's inside `databricks_postgres`).
4. Choose the Unity Catalog destination schema/catalog where the CDF history tables should land.
5. Confirm — the UI shows a preview of qualifying tables (e.g. `tickets`, `ticket_messages`) and
   their sync status before you start.

Once running, each qualifying table gets a corresponding Delta table named `lb_<table_name>_history`
(e.g. `lb_tickets_history`, `lb_ticket_messages_history`) in Unity Catalog, updated roughly every 15 seconds. 
Each row includes metadata columns (`_pg_change_type`, `_pg_lsn`, `_pg_xid`, `_timestamp`, `_sort_by`) describing the
change, so downstream Delta Live Tables/pipelines can build analytics dashboards tracking ticket metrics,
response times, and message activity over time.

> **Note:** Disabling CDF is lossy — changes made while it's off aren't captured, and re-enabling
> triggers a full resync (every row reloaded as an `insert`). There's no per-table exclusion option
> within an enabled schema; the only way to keep a table out of the feed is to not set
> `REPLICA IDENTITY FULL` on it.

## Features

### Database Schema
- **tickets** table: ID, title, priority (high/medium/low), status (open/in_progress/closed), created_by, created_at
- **ticket_messages** table: ID, ticket_id (foreign key with CASCADE delete), message_text, author, created_at
- Foreign key constraints ensure data integrity
- Serial primary keys auto-increment

### Frontend Capabilities
- Modern, responsive UI with gradient design and accessible color contrast
- Create tickets with title, priority, and status
- Filter tickets by priority and/or status
- Edit tickets inline (modal)
- Delete tickets with confirmation
- Threaded messaging per ticket
- Input validation (min/max length, required fields)
- Real-time error handling and user-friendly notifications
- Distinct color-coded badges for priority (red/orange/green) and status (blue/orange/purple)

### Backend Capabilities
- User identification via `X-Forwarded-Email` header (Databricks Apps) or SDK fallback
- Transaction management with `run_write_returning` for atomic operations
- JSON error responses for all endpoints
- Cascading deletes (deleting a ticket removes all its messages)

## Technical Notes

- Lakebase auth uses a single `LAKEBASE_URL` connection string pointing at a native Postgres role with a
  static, non-expiring password — no token refresh logic needed in `lakebase.py`.
- All write operations use `run_write_returning` to ensure commits and retrieve inserted/updated data atomically.
- The app automatically creates tables on first use if they don't exist.
