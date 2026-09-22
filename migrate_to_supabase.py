"""
Script migrasi data dari SQLite (cookies.db) → Supabase PostgreSQL.
Jalankan SEKALI saja sebelum deploy ke Vercel.

Cara pakai:
  set DATABASE_URL=postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres
  python migrate_to_supabase.py

Script ini akan:
1. Membaca semua data dari cookies.db (SQLite lokal)
2. Membuat tabel di Supabase PostgreSQL (via SQLAlchemy create_all)
3. Memindahkan users, cookie_results, user_cookie_claims ke PostgreSQL
4. Memindahkan config.json & token_proxies.txt ke tabel app_config
"""

import os
import sys
import sqlite3
import json
from datetime import datetime

# Pastikan DATABASE_URL di-set
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("=" * 60)
    print("ERROR: DATABASE_URL belum di-set!")
    print("")
    print("Contoh:")
    print("  set DATABASE_URL=postgresql://postgres.xxx:password@aws-0-region.pooler.supabase.com:6543/postgres")
    print("  python migrate_to_supabase.py")
    print("=" * 60)
    sys.exit(1)

# Set environment dan import app
os.environ['DATABASE_URL'] = DATABASE_URL
from app import create_app, db
from app.models import User, CookieResult, UserCookieClaim, AppConfig

app = create_app()

# Path ke SQLite lokal
SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.db')

if not os.path.exists(SQLITE_PATH):
    print(f"ERROR: File SQLite tidak ditemukan di: {SQLITE_PATH}")
    sys.exit(1)


def parse_datetime(val):
    """Parse datetime string dari SQLite."""
    if not val:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def migrate():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row

    with app.app_context():
        # Buat semua tabel (sudah dipanggil di create_app, tapi pastikan)
        db.create_all()

        # ── 1. Migrasi Users ──────────────────────────────────────────
        print("\n[1/4] Migrating users...")
        cursor = conn.execute("SELECT * FROM users")
        rows = cursor.fetchall()
        migrated_users = 0
        skipped_users = 0

        for row in rows:
            existing = User.query.filter_by(username=row['username']).first()
            if existing:
                skipped_users += 1
                continue

            user = User(
                id=row['id'],
                username=row['username'],
                email=row['email'],
                password_hash=row['password_hash'],
                is_admin=bool(row['is_admin']),
                is_approved=bool(row['is_approved']),
                created_at=parse_datetime(str(row['created_at'])) if row['created_at'] else None,
            )
            # Kolom opsional
            for col in ('ads_percentage', 'max_daily_claims', 'total_claims_left', 
                        'last_login_ip', 'last_login_ua', 'last_login_location', 'session_token'):
                try:
                    val = row[col]
                    if val is not None:
                        setattr(user, col, val)
                except (IndexError, KeyError):
                    pass
            try:
                val = row['last_active_at']
                if val:
                    user.last_active_at = parse_datetime(str(val))
            except (IndexError, KeyError):
                pass

            db.session.add(user)
            migrated_users += 1

        try:
            db.session.commit()
            print(f"   ✅ {migrated_users} users migrated, {skipped_users} skipped (already exist)")
        except Exception as e:
            db.session.rollback()
            print(f"   ❌ Error migrating users: {e}")

        # ── 2. Migrasi Cookie Results ─────────────────────────────────
        print("\n[2/4] Migrating cookie_results...")
        cursor = conn.execute("SELECT * FROM cookie_results")
        rows = cursor.fetchall()
        migrated_cookies = 0
        skipped_cookies = 0
        batch_size = 500

        for i, row in enumerate(rows):
            # Skip jika cookie_text sudah ada
            existing = CookieResult.query.filter_by(cookie_text=row['cookie_text']).first()
            if existing:
                skipped_cookies += 1
                continue

            cookie = CookieResult(
                id=row['id'],
                cookie_text=row['cookie_text'],
                filename=row['filename'] if 'filename' in row.keys() else None,
            )
            # Map semua kolom opsional
            optional_cols = [
                'service_type', 'plan_key', 'plan_name', 'country', 'email',
                'account_name', 'quality', 'max_streams', 'plan_price',
                'next_billing', 'payment_method', 'member_since', 'extra_members',
                'profiles', 'hold_status', 'membership_status', 'source_file'
            ]
            for col in optional_cols:
                try:
                    val = row[col]
                    if val is not None:
                        setattr(cookie, col, val)
                except (IndexError, KeyError):
                    pass

            # Boolean columns
            for bcol in ('is_on_hold', 'is_verified'):
                try:
                    val = row[bcol]
                    if val is not None:
                        setattr(cookie, bcol, bool(val))
                except (IndexError, KeyError):
                    pass

            try:
                val = row['checked_at']
                if val:
                    cookie.checked_at = parse_datetime(str(val))
            except (IndexError, KeyError):
                pass

            db.session.add(cookie)
            migrated_cookies += 1

            # Commit per batch
            if migrated_cookies % batch_size == 0:
                try:
                    db.session.commit()
                    print(f"   ... {migrated_cookies} cookies committed")
                except Exception as e:
                    db.session.rollback()
                    print(f"   ❌ Batch error: {e}")

        try:
            db.session.commit()
            print(f"   ✅ {migrated_cookies} cookies migrated, {skipped_cookies} skipped")
        except Exception as e:
            db.session.rollback()
            print(f"   ❌ Error migrating cookies: {e}")

        # ── 3. Migrasi User Cookie Claims ─────────────────────────────
        print("\n[3/4] Migrating user_cookie_claims...")
        try:
            cursor = conn.execute("SELECT * FROM user_cookie_claims")
            rows = cursor.fetchall()
            migrated_claims = 0
            skipped_claims = 0

            for row in rows:
                existing = UserCookieClaim.query.filter_by(
                    user_id=row['user_id'],
                    cookie_id=row['cookie_id']
                ).first()
                if existing:
                    skipped_claims += 1
                    continue

                claim = UserCookieClaim(
                    id=row['id'],
                    user_id=row['user_id'],
                    cookie_id=row['cookie_id'],
                    service_type=row['service_type'] if 'service_type' in row.keys() else 'netflix',
                )
                try:
                    val = row['claimed_at']
                    if val:
                        claim.claimed_at = parse_datetime(str(val))
                except (IndexError, KeyError):
                    pass

                db.session.add(claim)
                migrated_claims += 1

            try:
                db.session.commit()
                print(f"   ✅ {migrated_claims} claims migrated, {skipped_claims} skipped")
            except Exception as e:
                db.session.rollback()
                print(f"   ❌ Error migrating claims: {e}")
        except sqlite3.OperationalError:
            print("   ⚠️  Table user_cookie_claims not found in SQLite, skipping")

        # ── 4. Migrasi Config ─────────────────────────────────────────
        print("\n[4/4] Migrating config...")
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app', 'config.json')
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                for key, value in config.items():
                    existing = AppConfig.query.filter_by(key=key).first()
                    if existing:
                        continue
                    if isinstance(value, bool):
                        val_str = 'true' if value else 'false'
                    else:
                        val_str = str(value)
                    db.session.add(AppConfig(key=key, value=val_str))
                db.session.commit()
                print(f"   ✅ Config migrated: {list(config.keys())}")
            except Exception as e:
                db.session.rollback()
                print(f"   ❌ Error migrating config: {e}")
        else:
            print("   ⚠️  config.json not found, skipping")

        proxy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app', 'token_proxies.txt')
        if os.path.exists(proxy_path):
            existing = AppConfig.query.filter_by(key='token_proxies_text').first()
            if not existing:
                try:
                    with open(proxy_path, 'r', encoding='utf-8') as f:
                        text = f.read()
                    db.session.add(AppConfig(key='token_proxies_text', value=text))
                    db.session.commit()
                    print("   ✅ token_proxies.txt migrated")
                except Exception as e:
                    db.session.rollback()
                    print(f"   ❌ Error migrating proxies: {e}")
            else:
                print("   ⚠️  token_proxies_text already exists, skipping")
        else:
            print("   ⚠️  token_proxies.txt not found, skipping")

    conn.close()

    # ── Summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ MIGRASI SELESAI!")
    print("=" * 60)
    print(f"\nData dari: {SQLITE_PATH}")
    print(f"Ke:        {DATABASE_URL[:50]}...")
    print("\nLangkah selanjutnya:")
    print("1. Set DATABASE_URL di Vercel Dashboard → Settings → Environment Variables")
    print("2. Deploy ke Vercel: vercel --prod")
    print("3. Test akses: https://your-app.vercel.app/admin/")


if __name__ == '__main__':
    print("=" * 60)
    print("MIGRASI SQLite → Supabase PostgreSQL")
    print("=" * 60)
    print(f"\nSQLite: {SQLITE_PATH}")
    print(f"Target: {DATABASE_URL[:50]}...")
    print("")
    
    confirm = input("Lanjutkan migrasi? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Dibatalkan.")
        sys.exit(0)
    
    migrate()
