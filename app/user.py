from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from functools import wraps
from .models import CookieResult, UserCookieClaim
from . import db
from sqlalchemy import func

user_bp = Blueprint('user', __name__)


def user_approved_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if current_user.is_admin:
            return redirect(url_for('admin.dashboard'))
        if not current_user.is_approved:
            flash('Your account has not been approved yet.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


# Plan display config
PLAN_META = {
    'premium': {'label': 'Premium', 'icon': '👑', 'color': '#E50914', 'desc': 'Ultra HD · 4 Screens'},
    'standard_with_ads': {'label': 'Standard + Ads', 'icon': '📺', 'color': '#F5A623', 'desc': 'HD · 2 Screens + Ads'},
    'standard': {'label': 'Standard', 'icon': '⭐', 'color': '#2196F3', 'desc': 'HD · 2 Screens'},
    'basic': {'label': 'Basic', 'icon': '📱', 'color': '#4CAF50', 'desc': 'SD · 1 Screen'},
    'mobile': {'label': 'Mobile', 'icon': '📲', 'color': '#9C27B0', 'desc': 'Mobile Only'},
    'free': {'label': 'Free', 'icon': '🆓', 'color': '#607D8B', 'desc': 'No Subscription'},
    'extra_member_premium': {'label': 'Extra Member', 'icon': '➕', 'color': '#FF5722', 'desc': 'Extra Member Premium'},
}

# Comprehensive country data: code -> (flag_emoji, full_english_name)
COUNTRY_DATA = {
    # Americas
    'US': ('🇺🇸', 'United States'), 'CA': ('🇨🇦', 'Canada'), 'MX': ('🇲🇽', 'Mexico'),
    'BR': ('🇧🇷', 'Brazil'), 'AR': ('🇦🇷', 'Argentina'), 'CL': ('🇨🇱', 'Chile'),
    'CO': ('🇨🇴', 'Colombia'), 'PE': ('🇵🇪', 'Peru'), 'VE': ('🇻🇪', 'Venezuela'),
    'EC': ('🇪🇨', 'Ecuador'), 'BO': ('🇧🇴', 'Bolivia'), 'PY': ('🇵🇾', 'Paraguay'),
    'UY': ('🇺🇾', 'Uruguay'), 'DO': ('🇩🇴', 'Dominican Republic'), 'GT': ('🇬🇹', 'Guatemala'),
    'HN': ('🇭🇳', 'Honduras'), 'SV': ('🇸🇻', 'El Salvador'), 'NI': ('🇳🇮', 'Nicaragua'),
    'CR': ('🇨🇷', 'Costa Rica'), 'PA': ('🇵🇦', 'Panama'), 'JM': ('🇯🇲', 'Jamaica'),
    'TT': ('🇹🇹', 'Trinidad and Tobago'), 'CU': ('🇨🇺', 'Cuba'), 'HT': ('🇭🇹', 'Haiti'),
    'BB': ('🇧🇧', 'Barbados'), 'GY': ('🇬🇾', 'Guyana'), 'SR': ('🇸🇷', 'Suriname'),
    'BS': ('🇧🇸', 'Bahamas'), 'LC': ('🇱🇨', 'Saint Lucia'), 'GP': ('🇬🇵', 'Guadeloupe'),
    'MQ': ('🇲🇶', 'Martinique'),
    # Europe
    'GB': ('🇬🇧', 'United Kingdom'), 'DE': ('🇩🇪', 'Germany'), 'FR': ('🇫🇷', 'France'),
    'IT': ('🇮🇹', 'Italy'), 'ES': ('🇪🇸', 'Spain'), 'NL': ('🇳🇱', 'Netherlands'),
    'BE': ('🇧🇪', 'Belgium'), 'SE': ('🇸🇪', 'Sweden'), 'NO': ('🇳🇴', 'Norway'),
    'DK': ('🇩🇰', 'Denmark'), 'FI': ('🇫🇮', 'Finland'), 'PL': ('🇵🇱', 'Poland'),
    'PT': ('🇵🇹', 'Portugal'), 'GR': ('🇬🇷', 'Greece'), 'AT': ('🇦🇹', 'Austria'),
    'CH': ('🇨🇭', 'Switzerland'), 'IE': ('🇮🇪', 'Ireland'), 'CZ': ('🇨🇿', 'Czech Republic'),
    'HU': ('🇭🇺', 'Hungary'), 'RO': ('🇷🇴', 'Romania'), 'BG': ('🇧🇬', 'Bulgaria'),
    'SK': ('🇸🇰', 'Slovakia'), 'SI': ('🇸🇮', 'Slovenia'), 'HR': ('🇭🇷', 'Croatia'),
    'RS': ('🇷🇸', 'Serbia'), 'UA': ('🇺🇦', 'Ukraine'), 'RU': ('🇷🇺', 'Russia'),
    'AL': ('🇦🇱', 'Albania'), 'MK': ('🇲🇰', 'North Macedonia'), 'LT': ('🇱🇹', 'Lithuania'),
    'LV': ('🇱🇻', 'Latvia'), 'EE': ('🇪🇪', 'Estonia'), 'LU': ('🇱🇺', 'Luxembourg'),
    'MT': ('🇲🇹', 'Malta'), 'IS': ('🇮🇸', 'Iceland'), 'BY': ('🇧🇾', 'Belarus'),
    'MD': ('🇲🇩', 'Moldova'), 'BA': ('🇧🇦', 'Bosnia and Herzegovina'),
    'ME': ('🇲🇪', 'Montenegro'), 'XK': ('🇽🇰', 'Kosovo'), 'CY': ('🇨🇾', 'Cyprus'),
    'JE': ('🇯🇪', 'Jersey'),
    # Asia and Pacific
    'CN': ('🇨🇳', 'China'), 'JP': ('🇯🇵', 'Japan'), 'KR': ('🇰🇷', 'South Korea'),
    'IN': ('🇮🇳', 'India'), 'ID': ('🇮🇩', 'Indonesia'), 'PH': ('🇵🇭', 'Philippines'),
    'VN': ('🇻🇳', 'Vietnam'), 'TH': ('🇹🇭', 'Thailand'), 'MY': ('🇲🇾', 'Malaysia'),
    'SG': ('🇸🇬', 'Singapore'), 'HK': ('🇭🇰', 'Hong Kong'), 'TW': ('🇹🇼', 'Taiwan'),
    'AU': ('🇦🇺', 'Australia'), 'NZ': ('🇳🇿', 'New Zealand'), 'PK': ('🇵🇰', 'Pakistan'),
    'BD': ('🇧🇩', 'Bangladesh'), 'LK': ('🇱🇰', 'Sri Lanka'), 'NP': ('🇳🇵', 'Nepal'),
    'MM': ('🇲🇲', 'Myanmar'), 'KH': ('🇰🇭', 'Cambodia'), 'MN': ('🇲🇳', 'Mongolia'),
    'BN': ('🇧🇳', 'Brunei'), 'FJ': ('🇫🇯', 'Fiji'), 'PG': ('🇵🇬', 'Papua New Guinea'),
    'AF': ('🇦🇫', 'Afghanistan'), 'GE': ('🇬🇪', 'Georgia'), 'AM': ('🇦🇲', 'Armenia'),
    'AZ': ('🇦🇿', 'Azerbaijan'), 'KZ': ('🇰🇿', 'Kazakhstan'), 'UZ': ('UZ', 'Uzbekistan'),
    'BT': ('🇧🇹', 'Bhutan'), 'MV': ('🇲🇻', 'Maldives'), 'PF': ('🇵🇫', 'French Polynesia'),
    'LA': ('🇱🇦', 'Laos'), 'WS': ('🇼🇸', 'Samoa'), 'MO': ('🇲🇴', 'Macau'),
    # Middle East
    'SA': ('🇸🇦', 'Saudi Arabia'), 'AE': ('🇦🇪', 'United Arab Emirates'), 'QA': ('🇶🇦', 'Qatar'),
    'KW': ('🇰🇼', 'Kuwait'), 'BH': ('🇧🇭', 'Bahrain'), 'OM': ('🇴🇲', 'Oman'),
    'JO': ('🇯🇴', 'Jordan'), 'LB': ('🇱🇧', 'Lebanon'), 'IQ': ('🇮🇶', 'Iraq'),
    'IR': ('🇮🇷', 'Iran'), 'IL': ('🇮🇱', 'Israel'), 'TR': ('🇹🇷', 'Turkey'),
    'SY': ('🇸🇾', 'Syria'), 'YE': ('🇾🇪', 'Yemen'),
    # Africa
    'ZA': ('🇿🇦', 'South Africa'), 'NG': ('🇳🇬', 'Nigeria'), 'EG': ('🇪🇬', 'Egypt'),
    'KE': ('🇰🇪', 'Kenya'), 'ET': ('🇪🇹', 'Ethiopia'), 'GH': ('🇬🇭', 'Ghana'),
    'MA': ('🇲🇦', 'Morocco'), 'DZ': ('🇩🇿', 'Algeria'), 'TN': ('🇹🇳', 'Tunisia'),
    'CI': ('🇨🇮', 'Ivory Coast'), 'TZ': ('🇹🇿', 'Tanzania'), 'CM': ('🇨🇲', 'Cameroon'),
    'AO': ('🇦🇴', 'Angola'), 'MZ': ('🇲🇿', 'Mozambique'), 'ZM': ('🇿🇲', 'Zambia'),
    'ZW': ('🇿🇼', 'Zimbabwe'), 'SN': ('🇸🇳', 'Senegal'), 'TG': ('🇹🇬', 'Togo'),
    'BF': ('🇧🇫', 'Burkina Faso'), 'ML': ('🇲🇱', 'Mali'), 'MG': ('🇲🇬', 'Madagascar'),
    'BW': ('🇧🇼', 'Botswana'), 'NA': ('🇳🇦', 'Namibia'), 'RW': ('🇷🇼', 'Rwanda'),
    'UG': ('🇺🇬', 'Uganda'), 'SD': ('🇸🇩', 'Sudan'), 'GA': ('🇬🇦', 'Gabon'),
    'CD': ('🇨🇩', 'DR Congo'), 'SC': ('🇸🇨', 'Seychelles'), 'TD': ('🇹🇩', 'Chad'),
    'LY': ('🇱🇾', 'Libya'), 'MU': ('🇲🇺', 'Mauritius'), 'CV': ('🇨🇻', 'Cape Verde'),
    'YT': ('🇾🇹', 'Mayotte'), 'MW': ('🇲🇼', 'Malawi'), 'BI': ('🇧🇮', 'Burundi'),
    'SO': ('🇸🇴', 'Somalia'), 'GQ': ('🇬🇶', 'Equatorial Guinea'), 'NE': ('🇳🇪', 'Niger'),
    'RE': ('🇷🇪', 'Réunion'), 'SL': ('🇸🇱', 'Sierra Leone'),
    'XX': ('🌐', 'Global / Unknown'),
    'UNKNOWN': ('🌐', 'Global / Unknown'),
    'PS': ('🇵🇸', 'Palestine'),
}

DEFAULT_ALLOWED_COUNTRIES = {
    # Asia & Pasifik
    'SG', 'MY', 'TH', 'PH', 'VN', 'KH', 'LA', 'MM', 'BN', 'JP', 'KR', 'TW', 'HK', 'MO', 'AU', 'NZ', 'FJ', 'PG', 'WS', 'IN',
    # Timur Tengah
    'SA', 'AE', 'QA', 'KW', 'OM', 'BH', 'IL', 'JO', 'LB',
    # Amerika
    'US', 'CA', 'MX', 'CR', 'PA', 'JM', 'BS', 'DO', 'UY', 'EC', 'BO', 'PY',
    # Eropa
    'GB', 'FR', 'DE', 'NL', 'BE', 'CH', 'AT', 'SE', 'NO', 'DK', 'FI', 'IT', 'ES', 'PT', 'GR', 'PL', 'CZ', 'SK', 'HU', 'RO', 'HR', 'UA', 'IE', 'IS',
    # Afrika
    'MA', 'DZ', 'TN', 'GH', 'SN', 'CI', 'UG', 'TZ', 'MG',
    # Indonesia (Home country)
    'ID'
}

# Alias untuk backwards compatibility
ALLOWED_COUNTRIES = DEFAULT_ALLOWED_COUNTRIES

COUNTRY_REGIONS = {
    # Asia & Pasifik
    'SG': 'Asia & Pasifik', 'MY': 'Asia & Pasifik', 'TH': 'Asia & Pasifik', 'PH': 'Asia & Pasifik',
    'VN': 'Asia & Pasifik', 'KH': 'Asia & Pasifik', 'LA': 'Asia & Pasifik', 'MM': 'Asia & Pasifik',
    'BN': 'Asia & Pasifik', 'JP': 'Asia & Pasifik', 'KR': 'Asia & Pasifik', 'TW': 'Asia & Pasifik',
    'HK': 'Asia & Pasifik', 'MO': 'Asia & Pasifik', 'AU': 'Asia & Pasifik', 'NZ': 'Asia & Pasifik',
    'FJ': 'Asia & Pasifik', 'PG': 'Asia & Pasifik', 'WS': 'Asia & Pasifik', 'IN': 'Asia & Pasifik',
    'CN': 'Asia & Pasifik', 'PK': 'Asia & Pasifik', 'BD': 'Asia & Pasifik', 'LK': 'Asia & Pasifik',
    'NP': 'Asia & Pasifik', 'MN': 'Asia & Pasifik', 'BT': 'Asia & Pasifik', 'MV': 'Asia & Pasifik',
    'PF': 'Asia & Pasifik', 'AF': 'Asia & Pasifik', 'KZ': 'Asia & Pasifik', 'UZ': 'Asia & Pasifik',
    'ID': 'Asia & Pasifik',
    # Americas
    'US': 'Amerika', 'CA': 'Amerika', 'MX': 'Amerika', 'BR': 'Amerika', 'AR': 'Amerika',
    'CL': 'Amerika', 'CO': 'Amerika', 'PE': 'Amerika', 'VE': 'Amerika', 'EC': 'Amerika',
    'BO': 'Amerika', 'PY': 'Amerika', 'UY': 'Amerika', 'DO': 'Amerika', 'GT': 'Amerika',
    'HN': 'Amerika', 'SV': 'Amerika', 'NI': 'Amerika', 'CR': 'Amerika', 'PA': 'Amerika',
    'JM': 'Amerika', 'TT': 'Amerika', 'CU': 'Amerika', 'HT': 'Amerika', 'BB': 'Amerika',
    'GY': 'Amerika', 'SR': 'Amerika', 'LC': 'Amerika', 'GP': 'Amerika', 'MQ': 'Amerika',
    'BS': 'Amerika',
    # Europe
    'GB': 'Eropa', 'DE': 'Eropa', 'FR': 'Eropa', 'IT': 'Eropa', 'ES': 'Eropa', 'NL': 'Eropa',
    'BE': 'Eropa', 'SE': 'Eropa', 'NO': 'Eropa', 'DK': 'Eropa', 'FI': 'Eropa', 'PL': 'Eropa',
    'PT': 'Eropa', 'GR': 'Eropa', 'AT': 'Eropa', 'CH': 'Eropa', 'IE': 'Eropa', 'CZ': 'Eropa',
    'HU': 'Eropa', 'RO': 'Eropa', 'BG': 'Eropa', 'SK': 'Eropa', 'SI': 'Eropa', 'HR': 'Eropa',
    'RS': 'Eropa', 'UA': 'Eropa', 'RU': 'Eropa', 'AL': 'Eropa', 'MK': 'Eropa', 'LT': 'Eropa',
    'LV': 'Eropa', 'EE': 'Eropa', 'LU': 'Eropa', 'MT': 'Eropa', 'IS': 'Eropa', 'BY': 'Eropa',
    'MD': 'Eropa', 'BA': 'Eropa', 'ME': 'Eropa', 'XK': 'Eropa', 'CY': 'Eropa', 'JE': 'Eropa',
    # Middle East
    'SA': 'Timur Tengah', 'AE': 'Timur Tengah', 'QA': 'Timur Tengah', 'KW': 'Timur Tengah',
    'BH': 'Timur Tengah', 'OM': 'Timur Tengah', 'JO': 'Timur Tengah', 'LB': 'Timur Tengah',
    'IQ': 'Timur Tengah', 'IR': 'Timur Tengah', 'IL': 'Timur Tengah', 'TR': 'Timur Tengah',
    'SY': 'Timur Tengah', 'YE': 'Timur Tengah', 'PS': 'Timur Tengah', 'GE': 'Timur Tengah',
    'AM': 'Timur Tengah', 'AZ': 'Timur Tengah',
    # Africa
    'ZA': 'Afrika', 'NG': 'Afrika', 'EG': 'Afrika', 'KE': 'Afrika', 'ET': 'Afrika', 'GH': 'Afrika',
    'MA': 'Afrika', 'DZ': 'Afrika', 'TN': 'Afrika', 'CI': 'Afrika', 'TZ': 'Afrika', 'CM': 'Afrika',
    'AO': 'Afrika', 'MZ': 'Afrika', 'ZM': 'Afrika', 'ZW': 'Afrika', 'SN': 'Afrika', 'TG': 'Afrika',
    'BF': 'Afrika', 'ML': 'Afrika', 'MG': 'Afrika', 'BW': 'Afrika', 'NA': 'Afrika', 'RW': 'Afrika',
    'UG': 'Afrika', 'SD': 'Afrika', 'GA': 'Afrika', 'CD': 'Afrika', 'SC': 'Afrika', 'TD': 'Afrika',
    'LY': 'Afrika', 'MU': 'Afrika', 'CV': 'Afrika', 'YT': 'Afrika', 'MW': 'Afrika', 'BI': 'Afrika',
    'SO': 'Afrika', 'GQ': 'Afrika', 'NE': 'Afrika', 'RE': 'Afrika', 'SL': 'Afrika',
}


def get_country_region(country_code):
    """Mendapatkan region/benua dari kode negara."""
    return COUNTRY_REGIONS.get((country_code or '').upper(), 'Lainnya')


def get_allowed_countries():
    """
    Mengambil daftar negara yang diizinkan untuk dashboard user dari AppConfig.
    Jika belum dikonfigurasi, gunakan DEFAULT_ALLOWED_COUNTRIES.
    """
    from .models import AppConfig
    try:
        raw_val = AppConfig.get('allowed_countries')
        if raw_val is not None and str(raw_val).strip() != '':
            import json
            try:
                data = json.loads(raw_val)
                if isinstance(data, list):
                    return set(str(c).strip().upper() for c in data if c and len(str(c).strip()) == 2)
            except Exception:
                return set(str(c).strip().upper() for c in str(raw_val).split(',') if c.strip() and len(str(c).strip()) == 2)
    except Exception:
        pass
    return set(DEFAULT_ALLOWED_COUNTRIES)


def get_flag(country_code):
    code = (country_code or 'XX').upper()
    if code in ('XX', 'UNKNOWN', ''):
        return '🌐'
    if code in COUNTRY_DATA:
        return COUNTRY_DATA[code][0]
    # Fallback generate Unicode flag emoji dari 2-letter ISO code
    if len(code) == 2 and code.isalpha():
        try:
            return ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in code)
        except Exception:
            pass
    return '🌍'


def get_country_name(country_code):
    code = (country_code or 'XX').upper()
    if code in ('XX', 'UNKNOWN', ''):
        return 'Global / Unknown'
    if code in COUNTRY_DATA:
        return COUNTRY_DATA[code][1]
    return code


@user_bp.route('/')
@login_required
@user_approved_required
def dashboard():
    service = request.args.get('service', 'netflix')

    # Plan cards with counts — hanya cookie yang sudah terverifikasi
    plan_data = db.session.query(
        CookieResult.plan_key,
        func.max(CookieResult.plan_name).label('plan_name'),
        func.count(CookieResult.id).label('count')
    ).filter(
        CookieResult.service_type == service,
        CookieResult.is_on_hold == False,
        CookieResult.is_verified == True
    ).group_by(CookieResult.plan_key).all()

    plans = []
    for row in plan_data:
        meta = PLAN_META.get(row.plan_key, {
            'label': row.plan_name or row.plan_key,
            'icon': '📦', 'color': '#607D8B', 'desc': ''
        })
        plans.append({
            'key': row.plan_key,
            'label': meta['label'],
            'icon': meta['icon'],
            'color': meta['color'],
            'desc': meta['desc'],
            'count': row.count,
        })

    allowed_countries = get_allowed_countries()

    # Country summary — hanya cookie verified dan di negara yang diizinkan admin
    if allowed_countries:
        country_data = db.session.query(
            CookieResult.country,
            func.count(CookieResult.id).label('count')
        ).filter(
            CookieResult.service_type == service,
            CookieResult.is_on_hold == False,
            CookieResult.plan_key != 'free',
            CookieResult.is_verified == True,
            CookieResult.country.in_(list(allowed_countries))
        ).group_by(CookieResult.country).order_by(
            func.count(CookieResult.id).desc()
        ).all()

        total = CookieResult.query.filter(
            CookieResult.service_type == service,
            CookieResult.is_on_hold == False,
            CookieResult.plan_key != 'free',
            CookieResult.is_verified == True,
            CookieResult.country.in_(list(allowed_countries))
        ).count()
    else:
        country_data = []
        total = 0

    countries = [
        {'code': r.country, 'flag': get_flag(r.country), 'name': get_country_name(r.country), 'count': r.count}
        for r in country_data
    ]

    from datetime import datetime, timedelta
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    daily_claims_count = UserCookieClaim.query.filter(
        UserCookieClaim.user_id == current_user.id,
        UserCookieClaim.claimed_at >= twenty_four_hours_ago
    ).count()

    return render_template('user/dashboard.html', 
                           countries=countries, 
                           total=total, 
                           daily_claims_count=daily_claims_count,
                           current_service=service)

@user_bp.route('/country/<country_code>')
@login_required
@user_approved_required
def country_view(country_code):
    """Render the country view where user selects device."""
    service = request.args.get('service', 'netflix')
    allowed_countries = get_allowed_countries()
    if country_code.upper() not in allowed_countries:
        flash('Negara tidak didukung atau belum diaktifkan oleh admin.', 'danger')
        return redirect(url_for('user.dashboard', service=service))
    flag = get_flag(country_code)
    country_name = get_country_name(country_code)
    return render_template('user/country.html',
                           country_code=country_code.upper(),
                           country_name=country_name,
                           flag=flag,
                           current_service=service)

import json
import os
import random
from sqlalchemy import select as sa_select



@user_bp.route('/api/generate/<country_code>', methods=['POST'])
@login_required
@user_approved_required
def api_generate_token(country_code):
    """Generate NFToken on-demand based on device."""
    allowed_countries = get_allowed_countries()
    if country_code.upper() not in allowed_countries:
        return jsonify({'error': 'Negara tidak didukung atau belum diaktifkan oleh admin.'}), 400
    device = request.args.get('device', 'desktop')
    service = 'netflix'

    from datetime import datetime, timedelta
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    daily_claims_count = UserCookieClaim.query.filter(
        UserCookieClaim.user_id == current_user.id,
        UserCookieClaim.claimed_at >= twenty_four_hours_ago
    ).count()

    limit = getattr(current_user, 'max_daily_claims', 5)
    if limit is None: limit = 5

    if daily_claims_count >= limit:
        return jsonify({'error': f'Daily limit ({limit} tokens) reached. Please try again tomorrow.'}), 403

    # Prioritaskan cookie yang is_verified = True
    base_query_verified = CookieResult.query.filter(
        CookieResult.service_type == service,
        CookieResult.is_on_hold == False,
        CookieResult.is_verified == True
    )
    # Query cadangan untuk cookie yang belum ter-verify (jika verified habis)
    base_query_all = CookieResult.query.filter(
        CookieResult.service_type == service,
        CookieResult.is_on_hold == False
    )

    if country_code.strip().upper() == 'UNKNOWN':
        base_query_verified = base_query_verified.filter(CookieResult.country.in_(['Unknown', 'UNKNOWN', 'unknown', 'XX', 'xx']))
        base_query_all = base_query_all.filter(CookieResult.country.in_(['Unknown', 'UNKNOWN', 'unknown', 'XX', 'xx']))
    else:
        base_query_verified = base_query_verified.filter(CookieResult.country == country_code.strip())
        base_query_all = base_query_all.filter(CookieResult.country == country_code.strip())

    if device == 'mobile':
        base_query_verified = base_query_verified.filter(CookieResult.plan_key != 'free')
        base_query_all = base_query_all.filter(CookieResult.plan_key != 'free')
    else:
        base_query_verified = base_query_verified.filter(CookieResult.plan_key.notin_(['free', 'mobile']))
        base_query_all = base_query_all.filter(CookieResult.plan_key.notin_(['free', 'mobile']))

    # ── Baca setting prevent_duplicate_claims dari database ────────
    from .models import AppConfig
    prevent_duplicate_claims = AppConfig.get_bool('prevent_duplicate_claims', True)
    # ──────────────────────────────────────────────────────────────────

    already_claimed_sq = sa_select(UserCookieClaim.cookie_id).where(
        UserCookieClaim.user_id == current_user.id
    ).scalar_subquery()

    if prevent_duplicate_claims:
        # Filter ketat: cegah klaim cookie yang sama
        available_cookies = base_query_verified.filter(
            CookieResult.id.notin_(already_claimed_sq)
        ).all()

        if not available_cookies:
            available_cookies = base_query_all.filter(
                CookieResult.id.notin_(already_claimed_sq)
            ).all()
    else:
        # Bebas klaim ulang: utamakan yang belum di-claim, jika tidak ada izinkan klaim ulang
        available_cookies = base_query_verified.filter(
            CookieResult.id.notin_(already_claimed_sq)
        ).all()

        if not available_cookies:
            available_cookies = base_query_verified.all()

        if not available_cookies:
            available_cookies = base_query_all.all()

    if not available_cookies:
        return jsonify({'error': f'Stok cookie valid untuk negara {country_code} ({device}) sedang habis.'}), 404



    from .nftoken import generate_nftoken, ERROR_COOKIE_DEAD
    ads_pct = getattr(current_user, 'ads_percentage', 0)

    # ── Baca config proxy dari database ──────────────────────────
    from .models import AppConfig
    token_proxies = []
    use_token_proxy = AppConfig.get_bool('use_token_proxy', True)

    if use_token_proxy:
        proxies_text = AppConfig.get('token_proxies_text', '')
        if proxies_text:
            try:
                from .netflix_checker_main import _parse_proxy_line
                for line in proxies_text.splitlines():
                    if line.strip():
                        parsed = _parse_proxy_line(line)
                        if parsed:
                            token_proxies.append(parsed)
            except Exception:
                pass
    # ────────────────────────────────────────────────────────────────────

    last_error = "Unknown error"
    # Retry lebih banyak karena banyak cookie bisa expired dan harus di-skip
    for attempt in range(10):
        if not available_cookies:
            break

        is_ads = random.randint(1, 100) <= ads_pct
        ads_candidates = [c for c in available_cookies if c.plan_key == 'standard_with_ads']
        non_ads_candidates = [c for c in available_cookies if c.plan_key != 'standard_with_ads']

        if is_ads and ads_candidates:
            selected_cookie = random.choice(ads_candidates)
        elif non_ads_candidates:
            selected_cookie = random.choice(non_ads_candidates)
        elif ads_candidates:
            selected_cookie = random.choice(ads_candidates)
        else:
            break

        result = generate_nftoken(selected_cookie.cookie_text, token_proxies)

        if result.get('success'):
            try:
                claim = UserCookieClaim(
                    user_id=current_user.id,
                    cookie_id=selected_cookie.id,
                    service_type=service
                )
                db.session.add(claim)
                db.session.commit()
            except Exception:
                db.session.rollback()
            return jsonify(result)
        else:
            error_type = result.get('error_type')
            last_error = result.get('error', 'Unknown error')
            print(f"[DEBUG] Cookie {selected_cookie.id} gagal: {last_error} (type={error_type})")

            if error_type == ERROR_COOKIE_DEAD:
                # Cookie confirmed expired — hapus dari DB agar tidak dicoba user lain
                try:
                    cookie_obj = CookieResult.query.get(selected_cookie.id)
                    if cookie_obj:
                        db.session.delete(cookie_obj)
                        db.session.commit()
                        print(f"[INFO] Cookie {selected_cookie.id} dihapus dari DB (expired)")
                except Exception:
                    db.session.rollback()

            # Hapus dari daftar kandidat loop ini (apapun error typenya)
            available_cookies = [c for c in available_cookies if c.id != selected_cookie.id]

    return jsonify({'error': f"Tidak ada token tersedia saat ini. Detail: {last_error}"}), 500

