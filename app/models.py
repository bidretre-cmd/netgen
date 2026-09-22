from . import db
from flask_login import UserMixin
from datetime import datetime


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ads_percentage = db.Column(db.Integer, default=0)
    
    # Keamanan & Batasan Pengguna
    max_daily_claims = db.Column(db.Integer, default=5)
    total_claims_left = db.Column(db.Integer, default=20)
    last_login_ip = db.Column(db.String(45))
    last_login_ua = db.Column(db.String(255))
    last_login_location = db.Column(db.String(100))
    last_active_at = db.Column(db.DateTime)
    session_token = db.Column(db.String(100))

    @property
    def parsed_ua(self):
        if not self.last_login_ua:
            return 'Unknown Device'
        ua = self.last_login_ua.lower()
        
        # Detect OS
        os_name = 'Unknown OS'
        if 'windows' in ua:
            os_name = 'Windows'
        elif 'android' in ua:
            os_name = 'Android'
        elif 'iphone' in ua or 'ipad' in ua:
            os_name = 'iOS'
        elif 'macintosh' in ua or 'mac os' in ua:
            os_name = 'macOS'
        elif 'linux' in ua:
            os_name = 'Linux'
            
        # Detect Browser
        browser_name = 'Unknown Browser'
        if 'chrome' in ua or 'crios' in ua:
            browser_name = 'Chrome'
        elif 'firefox' in ua or 'fxios' in ua:
            browser_name = 'Firefox'
        elif 'safari' in ua and 'chrome' not in ua:
            browser_name = 'Safari'
        elif 'edge' in ua or 'edg' in ua:
            browser_name = 'Edge'
        elif 'opera' in ua or 'opr' in ua:
            browser_name = 'Opera'
            
        return f"{os_name} · {browser_name}"


class CookieResult(db.Model):
    __tablename__ = 'cookie_results'
    id = db.Column(db.Integer, primary_key=True)

    # Cookie raw data
    service_type = db.Column(db.String(20), default='netflix', index=True)
    filename = db.Column(db.String(255))
    cookie_text = db.Column(db.Text, nullable=False)

    # Account classification
    plan_key = db.Column(db.String(50), index=True)       # premium, standard, basic, mobile, free
    plan_name = db.Column(db.String(100))                  # display label
    country = db.Column(db.String(10), index=True)         # 2-letter country code
    is_on_hold = db.Column(db.Boolean, default=False)

    # Account details
    email = db.Column(db.String(200))
    account_name = db.Column(db.String(200))
    quality = db.Column(db.String(50))                     # UHD, HD, SD
    max_streams = db.Column(db.String(20))
    plan_price = db.Column(db.String(50))
    next_billing = db.Column(db.String(100))
    payment_method = db.Column(db.String(100))
    member_since = db.Column(db.String(100))
    extra_members = db.Column(db.String(20))
    profiles = db.Column(db.Text)
    hold_status = db.Column(db.String(20))
    membership_status = db.Column(db.String(100))

    # Metadata
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    source_file = db.Column(db.String(255))
    is_verified = db.Column(db.Boolean, default=True)  # False jika belum/gagal diverifikasi saat recheck


class UserCookieClaim(db.Model):
    """
    Mencatat cookie mana yang sudah pernah di-generate/di-claim oleh user tertentu.
    Sehingga setiap generate berikutnya selalu memberikan cookie yang BERBEDA.
    """
    __tablename__ = 'user_cookie_claims'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    cookie_id = db.Column(db.Integer, db.ForeignKey('cookie_results.id', ondelete='CASCADE'), nullable=False, index=True)
    service_type = db.Column(db.String(20), nullable=False, index=True)
    claimed_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Satu user tidak bisa claim cookie yang sama dua kali
    __table_args__ = (
        db.UniqueConstraint('user_id', 'cookie_id', name='uq_user_cookie_claim'),
    )
