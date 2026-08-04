import io
import os

import qrcode
import requests


def _webhook_url() -> str:
    return os.getenv('FEISHU_WEBHOOK_URL', '')


def _send_webhook(payload: dict) -> None:
    url = _webhook_url()
    if not url:
        return
    requests.post(url, json=payload, timeout=10)


def notify_feishu(title: str, content: str) -> None:
    try:
        _send_webhook({'msg_type': 'text', 'content': {'text': f'[闲鱼客服] {title}\n{content}'}})
    except Exception:
        pass


def notify_feishu_qrcode(qr_url: str) -> None:
    """生成二维码图片，上传到飞书后通过 webhook 发送。"""
    try:
        qr = qrcode.QRCode(border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        image_bytes = buf.getvalue()

        image_key = _upload_image(image_bytes)
        if not image_key:
            # 上传失败降级发文字
            notify_feishu('闲鱼登录二维码', f'请用闲鱼 APP 扫码登录：\n{qr_url}')
            return

        _send_webhook({'msg_type': 'text', 'content': {'text': '[闲鱼客服] Cookie 已失效，请用闲鱼 APP 扫描下方二维码重新登录'}})
        _send_webhook({'msg_type': 'image', 'content': {'image_key': image_key}})
    except Exception:
        pass


def _get_tenant_access_token() -> str:
    resp = requests.post(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        json={
            'app_id': os.getenv('FEISHU_APP_ID', ''),
            'app_secret': os.getenv('FEISHU_APP_SECRET', ''),
        },
        timeout=10,
    )
    return resp.json().get('tenant_access_token', '')


def _upload_image(image_bytes: bytes) -> str:
    """上传图片到飞书，返回 image_key（需要 App API）"""
    token = _get_tenant_access_token()
    if not token:
        return ''
    resp = requests.post(
        'https://open.feishu.cn/open-apis/im/v1/images',
        headers={'Authorization': f'Bearer {token}'},
        data={'image_type': 'message'},
        files={'image': ('qrcode.png', image_bytes, 'image/png')},
        timeout=15,
    )
    return resp.json().get('data', {}).get('image_key', '')
