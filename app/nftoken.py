"""
nftoken.py — Generate NFToken on-demand (tidak disimpan ke database)
"""
import sys
import os
import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

try:
    from .netflix_checker_main import (
        create_nftoken,
        build_nftoken_links,
        has_usable_nftoken,
        extract_netflix_cookie_bundles,
        cookies_dict_from_netscape,
        decode_netflix_value,
        NFTOKEN_API_URL,
        NFTOKEN_QUERY_PARAMS,
        NFTOKEN_HEADERS,
    )
    NFTOKEN_AVAILABLE = True
except ImportError as e:
    NFTOKEN_AVAILABLE = False
    print(f"[WARNING] Could not import nftoken: {e}")


# Error types untuk membedakan jenis kegagalan
ERROR_COOKIE_DEAD = 'cookie_dead'       # Cookie confirmed expired/invalid
ERROR_PROXY_FAIL  = 'proxy_fail'        # Proxy/network issue, coba lagi
ERROR_UNKNOWN     = 'unknown'           # Error tidak dikenal


def _test_cookie_alive(nf_id: str, proxy_dict=None) -> bool:
    """
    Cek cepat apakah cookie masih menghasilkan response yang valid dari API NFToken.
    Returns True jika value dict tidak kosong (cookie aktif di API Netflix).
    """
    try:
        headers = dict(NFTOKEN_HEADERS)
        headers['Cookie'] = f'NetflixId={nf_id}'
        resp = requests.get(
            NFTOKEN_API_URL,
            params=NFTOKEN_QUERY_PARAMS,
            headers=headers,
            proxies=proxy_dict,
            timeout=12,
            verify=False,
        )
        if resp.status_code != 200:
            return None  # None = tidak bisa dipastikan (network issue)
        data = resp.json()
        value = data.get('value') or {}
        # value kosong ({}) = cookie expired untuk NFToken API
        return bool(value)
    except Exception:
        return None  # None = tidak bisa dipastikan


def generate_nftoken(cookie_text: str, proxy_list: list = None) -> dict:
    """
    Generate NFToken dari cookie text.
    Returns:
      {
        'success': bool,
        'token': str | None,
        'pc_url': str | None,
        'mobile_url': str | None,
        'expires_at': str | None,
        'error': str | None,
        'error_type': 'cookie_dead' | 'proxy_fail' | 'unknown' | None
      }
    """
    if not NFTOKEN_AVAILABLE:
        return {'success': False, 'error': 'NFToken module not available', 'error_type': ERROR_UNKNOWN}

    try:
        bundles = extract_netflix_cookie_bundles(cookie_text)
        if not bundles:
            return {'success': False, 'error': 'Cookie tidak valid', 'error_type': ERROR_COOKIE_DEAD}

        bundle = bundles[0]
        cookies = bundle.get('cookies') or cookies_dict_from_netscape(bundle.get('netscape_text', ''))

        nf_id = decode_netflix_value(cookies.get('NetflixId', ''))
        if not nf_id:
            return {'success': False, 'error': 'NetflixId tidak ditemukan', 'error_type': ERROR_COOKIE_DEAD}

        # Jumlah percobaan: lebih banyak jika ada proxy (coba proxy berbeda tiap attempt)
        max_attempts = max(3, len(proxy_list)) if proxy_list else 1
        last_error = 'Token tidak tersedia'
        last_error_type = ERROR_UNKNOWN
        proxy_fail_count = 0

        for attempt in range(max_attempts):
            # Pilih proxy baru setiap percobaan agar tidak terjebak di proxy mati
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

            nftoken_data, err = create_nftoken(cookies, attempts=1, proxy_dict=req_proxies)

            if nftoken_data and has_usable_nftoken(nftoken_data):
                token = decode_netflix_value(nftoken_data.get('token'))
                expires = nftoken_data.get('expires_at_utc')

                pc_url = f'https://netflix.com/?nftoken={token}'
                mobile_url = f'https://netflix.com/unsupported?nftoken={token}'

                return {
                    'success': True,
                    'token': token,
                    'pc_url': pc_url,
                    'mobile_url': mobile_url,
                    'expires_at': expires,
                    'error': None,
                    'error_type': None,
                }
            else:
                # Klasifikasikan jenis error
                if err in ('Token missing in response', 'Token missing in API response'):
                    # Ini artinya API merespons 200 tapi value kosong = cookie expired
                    # Konfirmasi dengan quick check
                    alive = _test_cookie_alive(nf_id, req_proxies)
                    if alive is False:
                        # Confirmed: cookie expired
                        return {
                            'success': False,
                            'error': 'Cookie expired (tidak menghasilkan token)',
                            'error_type': ERROR_COOKIE_DEAD,
                        }
                    elif alive is None:
                        # Network issue, coba lagi
                        proxy_fail_count += 1
                        last_error = err or 'Token tidak tersedia'
                        last_error_type = ERROR_PROXY_FAIL
                    else:
                        # Cookie alive tapi token tidak ada? Tetap coba lagi
                        last_error = err or 'Token tidak tersedia'
                        last_error_type = ERROR_UNKNOWN
                elif err in ('proxy error', 'timeout', 'NFToken API error'):
                    proxy_fail_count += 1
                    last_error = err
                    last_error_type = ERROR_PROXY_FAIL
                elif err in ('403', '429'):
                    last_error = f'Netflix API error: {err}'
                    last_error_type = ERROR_PROXY_FAIL  # Bisa retry dengan proxy lain
                else:
                    last_error = err or 'Token tidak tersedia'
                    last_error_type = ERROR_UNKNOWN

        # Jika semua attempt gagal karena proxy, tandai sebagai proxy issue
        if proxy_fail_count == max_attempts:
            last_error_type = ERROR_PROXY_FAIL

        return {'success': False, 'error': last_error, 'error_type': last_error_type}

    except Exception as e:
        return {'success': False, 'error': str(e), 'error_type': ERROR_UNKNOWN}

