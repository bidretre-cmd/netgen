from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from .models import User
from . import db

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('user.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash('Incorrect username or password.', 'danger')
            return render_template('auth/login.html')

        if not user.is_admin and not user.is_approved:
            flash('Your account is pending admin approval. Please wait.', 'warning')
            return render_template('auth/login.html')

        import uuid
        from datetime import datetime

        # Generate token sesi acak untuk single-session login
        token = str(uuid.uuid4())
        user.session_token = token
        
        # Ambil IP client (mendukung header proxy)
        ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip_addr:
            ip_addr = ip_addr.split(',')[0].strip()
        user.last_login_ip = ip_addr
        user.last_login_ua = request.headers.get('User-Agent', '')[:255]
        user.last_active_at = datetime.utcnow()
        
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Simpan token ke sesi Flask
        session['session_token'] = token

        # Jalankan pencarian GeoIP di latar belakang secara asinkron (non-blocking)
        from flask import current_app
        import threading
        flask_app = current_app._get_current_object()
        threading.Thread(target=update_ip_location_bg, args=(flask_app, user.id, ip_addr), daemon=True).start()

        login_user(user, remember=remember)
        if user.is_admin:
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('user.dashboard'))

    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not username or not email or not password:
            flash('All fields are required.', 'danger')
            return render_template('auth/register.html')

        if len(username) < 3:
            flash('Username must be at least 3 characters.', 'danger')
            return render_template('auth/register.html')

        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/register.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('auth/register.html')

        if User.query.filter_by(username=username).first():
            flash('Username is already taken.', 'danger')
            return render_template('auth/register.html')

        if User.query.filter_by(email=email).first():
            flash('Email is already registered.', 'danger')
            return render_template('auth/register.html')

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            is_admin=False,
            is_approved=False,
        )
        db.session.add(user)
        db.session.commit()
        flash('Registration successful! Waiting for admin approval.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


def update_ip_location_bg(app, user_id, ip):
    """Mencari lokasi geografis IP di latar belakang menggunakan ip-api.com secara asinkron."""
    import requests
    with app.app_context():
        # Cek jika IP privat/localhost
        if not ip or ip in ('127.0.0.1', 'localhost', '::1') or ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.16.'):
            location = 'Local Server'
        else:
            try:
                r = requests.get(f"http://ip-api.com/json/{ip}", timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    if data.get('status') == 'success':
                        city = data.get('city') or ''
                        country = data.get('country') or ''
                        location = f"{city}, {country}" if city and country else (city or country or 'Unknown')
                    else:
                        location = 'Unknown'
                else:
                    location = 'Unknown'
            except Exception:
                location = 'Unknown'
        
        # Simpan ke database
        try:
            from .models import User
            user = User.query.get(user_id)
            if user:
                user.last_login_location = location
                db.session.commit()
                print(f"[GEOIP] Lokasi IP {ip} untuk user {user.username} berhasil di-update: {location}")
        except Exception as e:
            db.session.rollback()
            print(f"[GEOIP ERROR] Gagal menyimpan lokasi ke DB: {e}")
