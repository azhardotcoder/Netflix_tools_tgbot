import random
import os
from colorama import Fore, Style
from bs4 import BeautifulSoup
import re

def get_random_user_agent():
    """Returns a random user agent from a predefined list"""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Edge/120.0.0.0"
    ]
    return random.choice(user_agents)

def get_random_headers():
    """Returns browser-like headers with a random user agent"""
    return {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'TE': 'trailers'
    }

def extract_netflix_account_info(membership_html, security_html, account_html=None):
    """
    membership_html: str (membership.html ka content)
    security_html: str (security.html ka content)
    account_html: str (account.html ka content, optional)
    return: dict with plan, plan_desc, card_type, last4, payment_method, next_payment, extra_member, email, email_verified, phone, phone_verified, profile_transfer, feature_testing, member_since
    """
    info = {
        'plan': None,
        'plan_desc': None,
        'card_type': None,
        'last4': None,
        'payment_method': None,
        'next_payment': None,
        'extra_member': False,
        'email': None,
        'email_verified': None,
        'phone': None,
        'phone_verified': None,
        'profile_transfer': None,
        'feature_testing': None,
        'member_since': None
    }
    # --- Membership Page ---
    soup = BeautifulSoup(membership_html, 'html.parser')
    # Plan
    plan = soup.select_one('h3[data-uia="account-membership-page+plan-card+title"]')
    if plan:
        info['plan'] = plan.text.strip()
    plan_desc = soup.select_one('p[data-uia="account-membership-page+plan-card+description"]')
    if plan_desc:
        info['plan_desc'] = plan_desc.text.strip()
    # Next Payment
    next_payment_title = soup.find('h3', {'data-uia': 'account-membership-page+payments-card+title'})
    if next_payment_title and 'Next payment' in next_payment_title.text:
        next_payment_desc = next_payment_title.find_next('p', {'data-uia': 'account-membership-page+payments-card+description'})
        if next_payment_desc:
            info['next_payment'] = next_payment_desc.text.strip()
    # Payment Method
    card_type = soup.find('span', {'data-uia': re.compile(r'account-membership-page\+payment-method-card\+type')})
    last4 = soup.find('span', {'data-uia': re.compile(r'account-membership-page\+payment-method-card\+last-four')})
    if card_type and last4:
        info['card_type'] = card_type.text.strip()
        info['last4'] = last4.text.strip()
        info['payment_method'] = 'Card'
    else:
        # Paypal or 3rd party
        payment_section = soup.find('div', {'data-uia': re.compile(r'account-membership-page\+payment-method-card')})
        if payment_section:
            if 'paypal' in payment_section.text.lower():
                info['payment_method'] = 'PayPal'
            elif 'billed by' in payment_section.text.lower():
                info['payment_method'] = '3rd Party'
            else:
                info['payment_method'] = 'Unknown'
    # Extra Member
    extra_member = soup.find('h3', {'data-uia': 'account-membership-page+extra-member-card+title'})
    if extra_member:
        info['extra_member'] = True
    # --- Security Page ---
    soup2 = BeautifulSoup(security_html, 'html.parser')
    # Email
    email_li = soup2.find('li', {'data-uia': 'account-security-page+account-details-card+email-button'})
    if email_li:
        email_p = email_li.find('p', string=re.compile(r'Email', re.I))
        if email_p:
            # Email is next sibling
            email_text = email_p.find_next_sibling(text=True)
            if email_text:
                info['email'] = email_text.strip()
        # Verification status
        verif_p = email_li.find_all('p')
        if len(verif_p) > 1:
            status = verif_p[-1].text.strip().lower()
            info['email_verified'] = 'verify' not in status
    # Phone
    phone_li = soup2.find('li', {'data-uia': 'account-security-page+account-details-card+phone'})
    if phone_li:
        phone_p = phone_li.find('p', string=re.compile(r'Mobile phone', re.I))
        if phone_p:
            phone_text = phone_p.find_next_sibling(text=True)
            if phone_text:
                info['phone'] = phone_text.strip()
        # Verification status
        verif_p = phone_li.find_all('p')
        if len(verif_p) > 1:
            status = verif_p[-1].text.strip().lower()
            info['phone_verified'] = 'verify' not in status
    # Profile Transfer
    profile_li = soup2.find('li', {'data-uia': 'account-security-page+security-card+profile-transfer'})
    if profile_li:
        if 'off' in profile_li.text.lower():
            info['profile_transfer'] = False
        elif 'on' in profile_li.text.lower():
            info['profile_transfer'] = True
    # Feature Testing
    feature_li = soup2.find('li', {'data-uia': 'account-security-page+security-card+feature-testing'})
    if feature_li:
        if 'off' in feature_li.text.lower():
            info['feature_testing'] = False
        elif 'on' in feature_li.text.lower():
            info['feature_testing'] = True
    # --- Account Page: Member Since ---
    if account_html:
        soup3 = BeautifulSoup(account_html, 'html.parser')
        member_since_div = soup3.find('div', string=re.compile(r'Member since', re.I))
        if member_since_div:
            # e.g. 'Member since May 2025' -> extract after 'since'
            text = member_since_div.text.strip()
            match = re.search(r'Member since\s*(.*)', text, re.I)
            if match:
                info['member_since'] = match.group(1).strip()
    return info

async def fetch_netflix_service_code(session, cookies=None):
    try:
        async with session.get(
            'https://www.netflix.com/api/shakti/mre/servicecode',
            cookies=cookies,
            timeout=8
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('data', {}).get('authCode')
            return None
    except Exception:
        return None

def convert_netscape_cookie_lines(lines):
    """Convert Netscape HTTP Cookie file lines to list of Netflix cookie strings.

    Args:
        lines (Iterable[str]): Lines from a Netscape cookie file.

    Returns:
        list[str]: List of strings like 'NetflixId=...; SecureNetflixId=...'.
    """
    results = []
    # Temporary storage for the current set that will be combined
    current = {}

    for raw in lines:
        line = raw.strip()
        # Skip blank lines or header comments (but keep #HttpOnly_ prefix as it includes data)
        if not line or (line.startswith('#') and not line.startswith('#HttpOnly_')):
            continue

        # Remove optional '#HttpOnly_' prefix which some browser exports add
        if line.startswith('#HttpOnly_'):
            line = line[len('#HttpOnly_'):]

        # Netscape format ideally uses tab, but some sources convert tabs to spaces when pasted.
        # Split on any consecutive whitespace (tab or spaces)
        parts = re.split(r"\s+", line)
        if len(parts) < 7:
            continue  # Not a valid cookie row

        name = parts[5].strip()
        value = parts[6].strip()

        if name in ("NetflixId", "SecureNetflixId"):
            current[name] = value

        # Once both are collected, create combined cookie string
        if "NetflixId" in current and "SecureNetflixId" in current:
            cookie_str = f"NetflixId={current['NetflixId']}; SecureNetflixId={current['SecureNetflixId']}"
            results.append(cookie_str)
            current = {}  # Reset for the next possible account

    return results