from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os

db = SQLAlchemy()
login_manager = LoginManager()


def _column_exists(table_name, column_name):
    """Cek apakah kolom sudah ada di tabel. Kompatibel PostgreSQL & SQLite."""
    from sqlalchemy import text, inspect
    try:
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception:
        return False


def _safe_add_column(table_name, column_name, column_type):
    """Tambah kolom jika belum ada. Kompatibel PostgreSQL & SQLite."""
    from sqlalchemy import text
    if not _column_exists(table_name, column_name):
        try:
            db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
            db.session.commit()
            print(f"DB Migration: Added column {column_name} to {table_name}.")
        except Exception as e:
            db.session.rollback()
            print(f"DB Migration: Skipped {column_name} on {table_name}: {e}")


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'netflix-cookie-manager-secret-2024')
    
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        # SQLAlchemy 1.4+ membutuhkan prefix 'postgresql://' bukan 'postgres://'
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
        # Connection pool settings untuk PostgreSQL / Supabase
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'pool_size': 5,
            'max_overflow': 10,
            'pool_timeout': 30,
            'pool_recycle': 1800,
            'pool_pre_ping': True,
        }
    else:
        default_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cookies.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{default_db_path}'
        
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB upload limit
    app.config['MAX_FORM_PARTS'] = 10000       # Support 10000+ file parts
    app.config['MAX_FORM_MEMORY_SIZE'] = 500 * 1024 * 1024  # 500MB form memory

    db.init_app(app)
    
    with app.app_context():
        # Import models agar terdaftar di SQLAlchemy metadata sebelum create_all
        from . import models
        
        # Buat semua tabel (termasuk AppConfig baru)
        db.create_all()

        # Migrasi kolom untuk cookie_results
        _safe_add_column('cookie_results', 'service_type', "VARCHAR(20) DEFAULT 'netflix'")
        _safe_add_column('cookie_results', 'is_verified', "BOOLEAN DEFAULT TRUE")

        # Migrasi kolom keamanan pengguna
        _safe_add_column('users', 'max_daily_claims', "INTEGER DEFAULT 5")
        _safe_add_column('users', 'total_claims_left', "INTEGER DEFAULT 20")
        _safe_add_column('users', 'last_login_ip', "VARCHAR(45)")
        _safe_add_column('users', 'last_login_ua', "VARCHAR(255)")
        _safe_add_column('users', 'last_login_location', "VARCHAR(100)")
        _safe_add_column('users', 'last_active_at', "TIMESTAMP")
        _safe_add_column('users', 'session_token', "VARCHAR(100)")
        _safe_add_column('users', 'ads_percentage', "INTEGER DEFAULT 0")

        # Migrasi config.json → database (sekali saja)
        _migrate_file_config_to_db()

    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from .auth import auth_bp
    from .admin import admin_bp
    from .user import user_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(user_bp, url_prefix='/user')

    from flask import redirect, url_for, render_template_string, session, flash, request
    from flask_login import logout_user, current_user
    from datetime import datetime

    @app.before_request
    def check_user_session():
        # Abaikan static files dan logout route agar tidak loop
        if request.endpoint in ('static', 'auth.logout'):
            return
            
        if current_user.is_authenticated:
            # 1. Single Session Login
            db_token = getattr(current_user, 'session_token', None)
            flask_token = session.get('session_token')
            
            if db_token and flask_token and db_token != flask_token:
                logout_user()
                session.clear()
                flash('Your session has ended because this account logged in on another device.', 'warning')
                return redirect(url_for('auth.login'))
                
            # 2. Update Aktivitas Terakhir (maksimal 1x per 60 detik)
            now = datetime.utcnow()
            last_active = getattr(current_user, 'last_active_at', None)
            if not last_active or (now - last_active).total_seconds() > 60:
                current_user.last_active_at = now
                
                # Update IP & UA saat beraktivitas
                ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr)
                if ip_addr:
                    ip_addr = ip_addr.split(',')[0].strip()
                
                ip_changed = current_user.last_login_ip != ip_addr
                location_missing = getattr(current_user, 'last_login_location', None) is None

                current_user.last_login_ip = ip_addr
                current_user.last_login_ua = request.headers.get('User-Agent', '')[:255]
                
                try:
                    db.session.commit()
                    
                    # Jalankan pencarian GeoIP di latar belakang jika IP berubah / lokasi kosong
                    if ip_changed or location_missing:
                        from .auth import update_ip_location_bg
                        import threading
                        flask_app = app._get_current_object()
                        threading.Thread(target=update_ip_location_bg, args=(flask_app, current_user.id, ip_addr), daemon=True).start()
                except Exception:
                    db.session.rollback()

    @app.context_processor
    def inject_global_vars():
        from flask_login import current_user
        if current_user.is_authenticated and not getattr(current_user, 'is_admin', False):
            from .models import CookieResult
            from sqlalchemy import func
            try:
                counts = db.session.query(
                    CookieResult.service_type, 
                    func.count(CookieResult.id)
                ).group_by(CookieResult.service_type).all()
                svc_counts = {r[0]: r[1] for r in counts}
            except Exception:
                svc_counts = {}
            return dict(svc_counts=svc_counts)
        return dict(svc_counts={})

    @app.route('/')
    def index():
        return redirect(url_for('auth.login'))

    @app.errorhandler(413)
    def too_large(e):
        from flask import flash
        flash('File too large! Maximum 500MB per upload.', 'danger')
        return redirect(url_for('admin.check'))

    @app.errorhandler(404)
    def not_found(e):
        return render_template_string('<h2 style="font-family:sans-serif;padding:40px">404 — Page not found. <a href="/">Go back</a></h2>'), 404

    return app


def _migrate_file_config_to_db():
    """
    Migrasi satu kali dari config.json dan token_proxies.txt ke tabel AppConfig.
    Hanya berjalan jika belum ada data di AppConfig.
    """
    from .models import AppConfig
    import json

    # Cek apakah sudah pernah migrasi
    try:
        if AppConfig.query.first() is not None:
            return
    except Exception:
        # Tabel mungkin belum siap atau ada issue koneksi sementara
        return

    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            for key, value in config.items():
                if isinstance(value, bool):
                    val_str = 'true' if value else 'false'
                else:
                    val_str = str(value)
                row = AppConfig(key=key, value=val_str)
                db.session.add(row)
            
            db.session.commit()
            print("[CONFIG] Migrated config.json to database.")
        except Exception as e:
            db.session.rollback()
            print(f"[CONFIG] Failed to migrate config.json: {e}")

    proxy_path = os.path.join(os.path.dirname(__file__), 'token_proxies.txt')
    if os.path.exists(proxy_path):
        try:
            with open(proxy_path, 'r', encoding='utf-8') as f:
                proxies_text = f.read()
            row = AppConfig(key='token_proxies_text', value=proxies_text)
            db.session.add(row)
            db.session.commit()
            print("[CONFIG] Migrated token_proxies.txt to database.")
        except Exception as e:
            db.session.rollback()
            print(f"[CONFIG] Failed to migrate token_proxies.txt: {e}")

