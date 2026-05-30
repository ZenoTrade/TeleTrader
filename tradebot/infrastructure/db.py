"""SQLite persistence for user accounts and copy-trade mappings."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

DB_PATH = Path("users.db")

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name      TEXT    NOT NULL,
    last_name       TEXT    NOT NULL,
    phone           TEXT    DEFAULT '',
    mt5_account     INTEGER NOT NULL UNIQUE,
    mt5_password    TEXT    NOT NULL,
    mt5_server      TEXT    NOT NULL,
    mt5_path        TEXT    NOT NULL,
    risk_mode       TEXT    DEFAULT 'risk_pct',
    risk_per_trade  REAL    DEFAULT 0.01,
    fixed_lot       REAL    DEFAULT 0.01,
    max_slippage    INTEGER DEFAULT 20,
    enabled         INTEGER DEFAULT 1,
    is_master       INTEGER DEFAULT 0,
    pending_expire_minutes INTEGER DEFAULT 0,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS copy_map (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    master_ticket    INTEGER NOT NULL,
    follower_account INTEGER NOT NULL,
    follower_ticket  INTEGER NOT NULL,
    symbol           TEXT,
    created_at       TEXT,
    UNIQUE(master_ticket, follower_account)
);

CREATE TABLE IF NOT EXISTS traders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    enabled     INTEGER DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS bot_pending_placed (
    account    INTEGER NOT NULL,
    ticket     INTEGER NOT NULL,
    placed_at  REAL    NOT NULL,
    PRIMARY KEY (account, ticket)
);
"""

_MIGRATE_IS_MASTER = (
    "ALTER TABLE users ADD COLUMN is_master INTEGER DEFAULT 0"
)

_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN risk_mode TEXT DEFAULT 'risk_pct'",
    "ALTER TABLE users ADD COLUMN fixed_lot REAL DEFAULT 0.01",
    "ALTER TABLE users ADD COLUMN pending_expire_minutes INTEGER DEFAULT 0",
    # Trade-management knobs (only the master's values are used at runtime;
    # followers inherit via CopyTradeSyncer mirroring SL/TP changes).
    "ALTER TABLE users ADD COLUMN be_enabled INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN be_trigger_pct REAL DEFAULT 50.0",
    "ALTER TABLE users ADD COLUMN be_cushion_points INTEGER DEFAULT 5",
    "ALTER TABLE users ADD COLUMN trail_enabled INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN trail_mode TEXT DEFAULT 'tight'",
    "ALTER TABLE users ADD COLUMN trail_min_step_points INTEGER DEFAULT 5",
]


@dataclass
class UserAccount:
    """One trading user with MT5 credentials and personal risk settings."""
    id: int | None = None
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    mt5_account: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_path: str = ""
    risk_mode: str = "risk_pct"       # "risk_pct" or "fixed_lot"
    risk_per_trade: float = 0.01
    fixed_lot: float = 0.01
    max_slippage: int = 20
    enabled: bool = True
    is_master: bool = False
    pending_expire_minutes: int = 0  # 0 = do not auto-cancel pendings
    # --- Trade-management (master-only at runtime) ---
    be_enabled: bool = False
    be_trigger_pct: float = 50.0       # % of distance to original SL
    be_cushion_points: int = 5         # points past entry when BE triggers
    trail_enabled: bool = False
    trail_mode: str = "tight"          # "tight" | "medium" | "loose"
    trail_min_step_points: int = 5
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


# ------------------------------------------------------------------
# Connection / init
# ------------------------------------------------------------------

def _conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path or DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: str | Path | None = None) -> None:
    with _conn(db_path) as con:
        con.executescript(_SCHEMA)
        # Safe migrations for DBs created before newer columns existed
        for sql in [_MIGRATE_IS_MASTER] + _MIGRATIONS:
            try:
                con.execute(sql)
                con.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
    logger.info(f"Database ready at {db_path or DB_PATH}")


# ------------------------------------------------------------------
# Row mapper
# ------------------------------------------------------------------

def _to_user(row: sqlite3.Row) -> UserAccount:
    def _col(name: str, default):
        """Tolerant column read — returns default if column is missing or NULL."""
        try:
            v = row[name]
        except (IndexError, KeyError):
            return default
        return v if v is not None else default

    return UserAccount(
        id=row["id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        phone=row["phone"] or "",
        mt5_account=row["mt5_account"],
        mt5_password=row["mt5_password"],
        mt5_server=row["mt5_server"],
        mt5_path=row["mt5_path"],
        risk_mode=row["risk_mode"] or "risk_pct",
        risk_per_trade=row["risk_per_trade"],
        fixed_lot=row["fixed_lot"] if row["fixed_lot"] else 0.01,
        max_slippage=row["max_slippage"],
        enabled=bool(row["enabled"]),
        is_master=bool(row["is_master"]),
        pending_expire_minutes=int(
            row["pending_expire_minutes"]
            if row["pending_expire_minutes"] is not None else 0),
        be_enabled=bool(_col("be_enabled", 0)),
        be_trigger_pct=float(_col("be_trigger_pct", 50.0)),
        be_cushion_points=int(_col("be_cushion_points", 5)),
        trail_enabled=bool(_col("trail_enabled", 0)),
        trail_mode=str(_col("trail_mode", "tight") or "tight"),
        trail_min_step_points=int(_col("trail_min_step_points", 5)),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ------------------------------------------------------------------
# User CRUD
# ------------------------------------------------------------------

def add_user(u: UserAccount, db_path: str | Path | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as con:
        cur = con.execute(
            """INSERT INTO users
               (first_name,last_name,phone,
                mt5_account,mt5_password,mt5_server,mt5_path,
                risk_mode,risk_per_trade,fixed_lot,max_slippage,
                enabled,is_master,pending_expire_minutes,
                be_enabled,be_trigger_pct,be_cushion_points,
                trail_enabled,trail_mode,trail_min_step_points,
                created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (u.first_name, u.last_name, u.phone,
             u.mt5_account, u.mt5_password, u.mt5_server, u.mt5_path,
             u.risk_mode, u.risk_per_trade, u.fixed_lot, u.max_slippage,
             int(u.enabled), int(u.is_master), int(u.pending_expire_minutes),
             int(u.be_enabled), float(u.be_trigger_pct), int(u.be_cushion_points),
             int(u.trail_enabled), str(u.trail_mode or "tight"),
             int(u.trail_min_step_points),
             now, now),
        )
        con.commit()
        return cur.lastrowid  # type: ignore[return-value]


def update_user(u: UserAccount, db_path: str | Path | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as con:
        con.execute(
            """UPDATE users SET
               first_name=?,last_name=?,phone=?,
               mt5_account=?,mt5_password=?,mt5_server=?,mt5_path=?,
               risk_mode=?,risk_per_trade=?,fixed_lot=?,max_slippage=?,
               enabled=?,is_master=?,pending_expire_minutes=?,
               be_enabled=?,be_trigger_pct=?,be_cushion_points=?,
               trail_enabled=?,trail_mode=?,trail_min_step_points=?,
               updated_at=?
               WHERE id=?""",
            (u.first_name, u.last_name, u.phone,
             u.mt5_account, u.mt5_password, u.mt5_server, u.mt5_path,
             u.risk_mode, u.risk_per_trade, u.fixed_lot, u.max_slippage,
             int(u.enabled), int(u.is_master), int(u.pending_expire_minutes),
             int(u.be_enabled), float(u.be_trigger_pct), int(u.be_cushion_points),
             int(u.trail_enabled), str(u.trail_mode or "tight"),
             int(u.trail_min_step_points),
             now, u.id),
        )
        con.commit()


def delete_user(user_id: int, db_path: str | Path | None = None) -> None:
    with _conn(db_path) as con:
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
        con.commit()


def list_users(db_path: str | Path | None = None) -> list[UserAccount]:
    with _conn(db_path) as con:
        rows = con.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [_to_user(r) for r in rows]


def get_enabled_users(db_path: str | Path | None = None) -> list[UserAccount]:
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT * FROM users WHERE enabled=1 ORDER BY id"
        ).fetchall()
    return [_to_user(r) for r in rows]


def get_user(user_id: int, db_path: str | Path | None = None) -> UserAccount | None:
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)
        ).fetchone()
    return _to_user(row) if row else None


# ------------------------------------------------------------------
# Master / follower helpers
# ------------------------------------------------------------------

def get_master_user(db_path: str | Path | None = None) -> UserAccount | None:
    """Return the single master account, or None."""
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT * FROM users WHERE is_master=1 AND enabled=1 LIMIT 1"
        ).fetchone()
    return _to_user(row) if row else None


def get_follower_users(db_path: str | Path | None = None) -> list[UserAccount]:
    """Return all enabled non-master users."""
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT * FROM users WHERE is_master=0 AND enabled=1 ORDER BY id"
        ).fetchall()
    return [_to_user(r) for r in rows]


def set_master(user_id: int, db_path: str | Path | None = None) -> None:
    """Mark one user as master; clears is_master on everyone else."""
    with _conn(db_path) as con:
        con.execute("UPDATE users SET is_master=0")
        con.execute("UPDATE users SET is_master=1 WHERE id=?", (user_id,))
        con.commit()


# ------------------------------------------------------------------
# Copy-map CRUD
# ------------------------------------------------------------------

def upsert_copy_map(master_ticket: int, follower_account: int,
                    follower_ticket: int, symbol: str = "",
                    db_path: str | Path | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as con:
        con.execute(
            """INSERT INTO copy_map
               (master_ticket, follower_account, follower_ticket, symbol, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(master_ticket, follower_account)
               DO UPDATE SET follower_ticket=excluded.follower_ticket""",
            (master_ticket, follower_account, follower_ticket, symbol, now),
        )
        con.commit()


def get_follower_ticket(master_ticket: int, follower_account: int,
                        db_path: str | Path | None = None) -> int | None:
    with _conn(db_path) as con:
        row = con.execute(
            """SELECT follower_ticket FROM copy_map
               WHERE master_ticket=? AND follower_account=?""",
            (master_ticket, follower_account),
        ).fetchone()
    return int(row["follower_ticket"]) if row else None


def get_follower_tickets_for_master(
        master_ticket: int,
        db_path: str | Path | None = None,
) -> list[tuple[int, int]]:
    """Return list of (follower_account, follower_ticket) for a master ticket."""
    with _conn(db_path) as con:
        rows = con.execute(
            """SELECT follower_account, follower_ticket FROM copy_map
               WHERE master_ticket=?""",
            (master_ticket,),
        ).fetchall()
    return [(r["follower_account"], r["follower_ticket"]) for r in rows]


def delete_copy_map(master_ticket: int, follower_account: int,
                    db_path: str | Path | None = None) -> None:
    with _conn(db_path) as con:
        con.execute(
            "DELETE FROM copy_map WHERE master_ticket=? AND follower_account=?",
            (master_ticket, follower_account),
        )
        con.commit()


def delete_copy_map_by_master(master_ticket: int,
                              db_path: str | Path | None = None) -> None:
    with _conn(db_path) as con:
        con.execute(
            "DELETE FROM copy_map WHERE master_ticket=?",
            (master_ticket,),
        )
        con.commit()


# ------------------------------------------------------------------
# Trader CRUD
# ------------------------------------------------------------------

@dataclass
class Trader:
    id: int | None = None
    name: str = ""
    enabled: bool = False
    first_seen: str | None = None
    last_seen: str | None = None


def _to_trader(row: sqlite3.Row) -> Trader:
    return Trader(
        id=row["id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
    )


def upsert_trader(name: str, db_path: str | Path | None = None) -> Trader:
    """Register a trader name. New traders default to disabled.
    Existing traders get their last_seen updated."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as con:
        con.execute(
            """INSERT INTO traders (name, enabled, first_seen, last_seen)
               VALUES (?, 0, ?, ?)
               ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen""",
            (name, now, now),
        )
        con.commit()
        row = con.execute(
            "SELECT * FROM traders WHERE name=?", (name,)
        ).fetchone()
    return _to_trader(row)


def list_traders(db_path: str | Path | None = None) -> list[Trader]:
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT * FROM traders ORDER BY name"
        ).fetchall()
    return [_to_trader(r) for r in rows]


def set_trader_enabled(trader_id: int, enabled: bool,
                       db_path: str | Path | None = None) -> None:
    with _conn(db_path) as con:
        con.execute(
            "UPDATE traders SET enabled=? WHERE id=?",
            (int(enabled), trader_id),
        )
        con.commit()


def is_trader_allowed(name: str, db_path: str | Path | None = None) -> bool:
    """Return True if the trader is registered AND enabled.
    Unknown traders are NOT allowed (they must be enabled in admin first)."""
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT enabled FROM traders WHERE name=?", (name,)
        ).fetchone()
    if not row:
        return False
    return bool(row["enabled"])


def delete_trader(trader_id: int, db_path: str | Path | None = None) -> None:
    with _conn(db_path) as con:
        con.execute("DELETE FROM traders WHERE id=?", (trader_id,))
        con.commit()


# ------------------------------------------------------------------
# Bot pending placement times (host clock — MT5 time_setup is unreliable)
# ------------------------------------------------------------------

def record_bot_pending_placed(
    account: int,
    ticket: int,
    placed_at: float | None = None,
    db_path: str | Path | None = None,
) -> None:
    ts = float(placed_at if placed_at is not None else datetime.now(timezone.utc).timestamp())
    with _conn(db_path) as con:
        con.execute(
            """INSERT INTO bot_pending_placed (account, ticket, placed_at)
               VALUES (?, ?, ?)
               ON CONFLICT(account, ticket) DO UPDATE SET placed_at=excluded.placed_at""",
            (int(account), int(ticket), ts),
        )
        con.commit()


def get_bot_pending_placed_at(
    account: int,
    ticket: int,
    db_path: str | Path | None = None,
) -> float | None:
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT placed_at FROM bot_pending_placed WHERE account=? AND ticket=?",
            (int(account), int(ticket)),
        ).fetchone()
    if not row:
        return None
    return float(row["placed_at"])


def delete_bot_pending_placed(
    account: int,
    ticket: int,
    db_path: str | Path | None = None,
) -> None:
    with _conn(db_path) as con:
        con.execute(
            "DELETE FROM bot_pending_placed WHERE account=? AND ticket=?",
            (int(account), int(ticket)),
        )
        con.commit()
