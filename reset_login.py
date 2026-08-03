"""
重置登录态，下次运行 goofish_live.py 时会重新扫码。
新机器部署、切换账号、cookie 异常时使用。
"""
import os
from dotenv import load_dotenv
load_dotenv()

cookies_file = os.getenv('COOKIES_FILE', 'cookies.json')
if os.path.exists(cookies_file):
    os.remove(cookies_file)
    print(f'已删除 {cookies_file}，下次启动将重新扫码登录')
else:
    print(f'{cookies_file} 不存在，无需清理')
