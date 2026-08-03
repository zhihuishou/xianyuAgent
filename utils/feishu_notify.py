import io
import os
import tempfile

import qrcode
import requests


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


def _upload_image(token: str, image_bytes: bytes) -> str:
    """上传图片到飞书，返回 image_key"""
    resp = requests.post(
        'https://open.feishu.cn/open-apis/im/v1/images',
        headers={'Authorization': f'Bearer {token}'},
        data={'image_type': 'message'},
        files={'image': ('qrcode.png', image_bytes, 'image/png')},
        timeout=15,
    )
    return resp.json().get('data', {}).get('image_key', '')


def _send_message(token: str, receive_id: str, msg_type: str, content: str) -> None:
    requests.post(
        'https://open.feishu.cn/open-apis/im/v1/messages',
        params={'receive_id_type': 'user_id'},
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        json={
            'receive_id': receive_id,
            'msg_type': msg_type,
            'content': content,
        },
        timeout=10,
    )


def notify_feishu(title: str, content: str) -> None:
    app_id = os.getenv('FEISHU_APP_ID', '')
    receive_id = os.getenv('FEISHU_RECEIVE_ID', '')
    if not app_id or not receive_id:
        return
    try:
        token = _get_tenant_access_token()
        if not token:
            return
        import json as _json
        _send_message(
            token, receive_id,
            'text',
            _json.dumps({'text': f'[闲鱼客服] {title}\n{content}'}),
        )
    except Exception:
        pass


def notify_feishu_qrcode(qr_url: str) -> None:
    """生成二维码图片并通过飞书发送给用户"""
    app_id = os.getenv('FEISHU_APP_ID', '')
    receive_id = os.getenv('FEISHU_RECEIVE_ID', '')
    if not app_id or not receive_id:
        return
    try:
        # 生成二维码 PNG
        qr = qrcode.QRCode(border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        image_bytes = buf.getvalue()

        token = _get_tenant_access_token()
        if not token:
            return

        image_key = _upload_image(token, image_bytes)
        if not image_key:
            # 上传失败时降级发文字链接
            notify_feishu('闲鱼登录二维码', f'请用闲鱼 APP 扫码登录：\n{qr_url}')
            return

        import json as _json
        # 先发提示文字
        _send_message(
            token, receive_id,
            'text',
            _json.dumps({'text': '[闲鱼客服] Cookie 已失效，请用闲鱼 APP 扫描下方二维码重新登录'}),
        )
        # 再发二维码图片
        _send_message(
            token, receive_id,
            'image',
            _json.dumps({'image_key': image_key}),
        )
    except Exception:
        pass
