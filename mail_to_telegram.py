import imaplib
import email
import os
import re
import sys
from email.header import decode_header
from html import unescape
from urllib import request, parse

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
EMAIL_LOGIN = os.environ['EMAIL_LOGIN']
EMAIL_PASSWORD = os.environ['EMAIL_PASSWORD']
IMAP_HOST = os.getenv('IMAP_HOST', 'imap.yandex.ru')
IMAP_PORT = int(os.getenv('IMAP_PORT', '993'))
IMAP_FOLDER = os.getenv('IMAP_FOLDER', 'INBOX')
SUBJECT_FILTER = os.getenv('SUBJECT_FILTER', '').strip()
FROM_FILTER = os.getenv('FROM_FILTER', '').strip().lower()
MAX_BODY = int(os.getenv('MAX_BODY', '1200'))


def decode_mime(value):
    if not value:
        return ''
    parts = decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or 'utf-8', errors='ignore'))
        else:
            decoded.append(part)
    return ''.join(decoded).strip()


def html_to_text(html):
    html = re.sub(r'(?is)<(script|style).*?>.*?</\1>', ' ', html)
    html = re.sub(r'(?i)<br\s*/?>', '\n', html)
    html = re.sub(r'(?i)</p>|</div>|</li>|</tr>|</h\d>', '\n', html)
    html = re.sub(r'<[^>]+>', ' ', html)
    html = unescape(html)
    html = re.sub(r'\r', '', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    html = re.sub(r'[ \t]{2,}', ' ', html)
    return html.strip()


def extract_body(msg):
    plain_text = None
    html_text = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get('Content-Disposition', ''))
            if 'attachment' in disp.lower():
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or 'utf-8'
            try:
                text = payload.decode(charset, errors='ignore')
            except Exception:
                text = payload.decode('utf-8', errors='ignore')
            if ctype == 'text/plain' and not plain_text:
                plain_text = text.strip()
            elif ctype == 'text/html' and not html_text:
                html_text = html_to_text(text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            try:
                text = payload.decode(charset, errors='ignore')
            except Exception:
                text = payload.decode('utf-8', errors='ignore')
            if msg.get_content_type() == 'text/html':
                html_text = html_to_text(text)
            else:
                plain_text = text.strip()
    body = plain_text or html_text or ''
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    return body[:MAX_BODY]


def telegram_send(text):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    data = parse.urlencode({'chat_id': TELEGRAM_CHAT_ID, 'text': text}).encode()
    req = request.Request(url, data=data)
    with request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def matches_filters(sender, subject):
    sender_l = (sender or '').lower()
    subject_l = (subject or '').lower()
    if FROM_FILTER and FROM_FILTER not in sender_l:
        return False
    if SUBJECT_FILTER and SUBJECT_FILTER.lower() not in subject_l:
        return False
    return True


def main():
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_LOGIN, EMAIL_PASSWORD)
    status, _ = mail.select(IMAP_FOLDER)
    if status != 'OK':
        raise RuntimeError(f'Cannot open folder: {IMAP_FOLDER}')

    status, data = mail.search(None, '(UNSEEN)')
    if status != 'OK':
        raise RuntimeError('Cannot search unseen emails')

    ids = data[0].split()
    if not ids:
        print('No unseen emails')
        mail.logout()
        return

    forwarded = 0
    for msg_id in ids[-10:]:
        status, msg_data = mail.fetch(msg_id, '(RFC822)')
        if status != 'OK':
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = decode_mime(msg.get('Subject', '(без темы)'))
        sender = decode_mime(msg.get('From', '(неизвестный отправитель)'))
        date = decode_mime(msg.get('Date', ''))
        if not matches_filters(sender, subject):
            continue
        body = extract_body(msg)
        text = (
            f'📩 Новое письмо\n'
            f'От: {sender}\n'
            f'Тема: {subject}\n'
            f'Дата: {date}\n\n'
            f'{body or "(текст письма пустой)"}'
        )
        telegram_send(text[:4096])
        mail.store(msg_id, '+FLAGS', '\\Seen')
        forwarded += 1

    mail.logout()
    print(f'Forwarded: {forwarded}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        raise
