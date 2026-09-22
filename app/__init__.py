from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os

db = SQLAlchemy()
login_manager = LoginManager()


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'netflix-cookie-manager-secret-2024'
    
    default_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cookies.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', f'sqlite:///{default_db_path}')
        
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB upload limit
    app.config['MAX_FORM_PARTS'] = 10000       # Support 10000+ file parts
    app.config['MAX_FORM_MEMORY_SIZE'] = 500 * 1024 * 1024  # 500MB form memory

    db.init_app(app)
    
    with app.app_context():
        from sqlalchemy import text
        try:
            db.session.execute(text("ALTER TABLE cookie_results ADD COLUMN service_type VARCHAR(20) DEFAULT 'netflix'"))
            db.session.commit()
            print("DB Migration: Added service_type column.")
        except Exception:
            db.session.rollback()

        # Migrasi kolom keamanan pengguna
        columns_to_add = [
            ("max_daily_claims", "INTEGER DEFAULT 5"),
            ("total_claims_left", "INTEGER DEFAULT 20"),
            ("last_login_ip", "VARCHAR(45)"),
            ("last_login_ua", "VARCHAR(255)"),
            ("last_login_location", "VARCHAR(100)"),
            ("last_active_at", "DATETIME"),
            ("session_token", "VARCHAR(100)")
        ]
        for col_name, col_type in columns_to_add:
            try:
                db.session.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))
                db.session.commit()
                print(f"DB Migration: Added column {col_name} to users table.")
            except Exception:
                db.session.rollback()

        try:
            db.session.execute(text("ALTER TABLE users ADD COLUMN ads_percentage INTEGER DEFAULT 0"))
            db.session.commit()
            print("DB Migration: Added ads_percentage column to users.")
        except Exception:
            db.session.rollback()

        try:
            db.session.execute(text("ALTER TABLE cookie_results ADD COLUMN is_verified BOOLEAN DEFAULT 1"))
            db.session.commit()
            print("DB Migration: Added is_verified column to cookie_results.")
        except Exception:
            db.session.rollback()

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
