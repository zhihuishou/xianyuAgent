import base64
import json
import asyncio
import threading
import time

from dotenv import load_dotenv
load_dotenv()

import os
from pathlib import Path
from loguru import logger

# 日志输出到 logs/ 目录，按天滚动
Path('logs').mkdir(exist_ok=True)
import sys
logger.remove()
logger.add(sys.stderr, level='DEBUG')
logger.add('logs/xianyu_{time:YYYY-MM-DD}.log', rotation='00:00', retention='7 days', encoding='utf-8', level='DEBUG')
import websockets
from goofish_apis import XianyuApis, qrcode_login

from utils.goofish_utils import generate_mid, generate_uuid, trans_cookies, generate_device_id, decrypt, \
    get_session_cookies_str
from utils.cookie_store import save_cookies, load_cookies, clear_cookies
from utils.feishu_notify import notify_feishu
from message import Message, make_text, make_image
from ai_agent import ask


class XianyuLive:
    def __init__(self, cookies_str):
        self.base_url = 'wss://wss-goofish.dingtalk.com/'
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        self.myid = self.cookies['unb']
        self.device_id = generate_device_id(self.myid)
        self.xianyu = XianyuApis(self.cookies, self.device_id)
        self.ws = None
        # 人工介入过的会话 cid 集合，介入后该会话不再 AI 回复
        self._human_cids: set = set()
        # 已处理过的 messageId，防止重连后重复回复
        self._seen_msg_ids: set = set()
        # 按 cid 的串行队列，同一会话内消息顺序处理不乱序
        self._cid_queues: dict = {}
        self._cid_workers: dict = {}

    async def list_all_conversations(self, cid):
        headers = {
            "Cookie": get_session_cookies_str(self.xianyu.session),
            "Host": "wss-goofish.dingtalk.com",
            "Connection": "Upgrade",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Origin": "https://www.goofish.com",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        async with websockets.connect(self.base_url, additional_headers=headers) as websocket:
            asyncio.create_task(self.init(websocket))
            send_mid = generate_mid()
            msg = {
                "lwp": "/r/MessageManager/listUserMessages",
                "headers": {
                    "mid": send_mid
                },
                "body": [
                    f"{cid}@goofish",
                    False,
                    9007199254740991,
                    20,
                    False
                ]
            }
            user_message_models = []
            async for message in websocket:
                try:
                    message = json.loads(message)
                    ack = {
                        "code": 200,
                        "headers": {
                            "mid": message["headers"]["mid"] if "mid" in message["headers"] else generate_mid(),
                            "sid": message["headers"]["sid"] if "sid" in message["headers"] else '',
                        }
                    }
                    if 'app-key' in message["headers"]:
                        ack["headers"]["app-key"] = message["headers"]["app-key"]
                    if 'ua' in message["headers"]:
                        ack["headers"]["ua"] = message["headers"]["ua"]
                    if 'dt' in message["headers"]:
                        ack["headers"]["dt"] = message["headers"]["dt"]
                    await websocket.send(json.dumps(ack))
                except Exception as e:
                    pass
                try:
                    if 'lwp' in message and message['lwp'] == "/s/vulcan":
                        await websocket.send(json.dumps(msg))
                    recv_mid = message["headers"]["mid"] if "mid" in message["headers"] else ''
                    if recv_mid == send_mid:
                        logger.info(f"user history message: {message}")
                        has_more = message["body"]["hasMore"] == 1
                        next_cursor = message["body"]["nextCursor"]
                        for user_message in message["body"]["userMessageModels"]:
                            send_user_name = user_message["message"]["extension"]["reminderTitle"]
                            send_user_id = user_message["message"]["extension"]["senderUserId"]
                            send_message_base64 = user_message["message"]["content"]["custom"]["data"]
                            send_message_json = json.loads(base64.b64decode(send_message_base64).decode('utf-8'))
                            user_message_models.insert(0, {
                                "send_user_id": send_user_id,
                                "send_user_name": send_user_name,
                                "message": send_message_json
                            })
                        if has_more:
                            logger.info(f"has more history messages, next cursor: {next_cursor}")
                            send_mid = generate_mid()
                            msg["headers"]["mid"] = send_mid
                            msg["body"][2] = next_cursor
                            await websocket.send(json.dumps(msg))
                        else:
                            return user_message_models
                except Exception as e:
                    return user_message_models

    async def create_chat(self, ws, toid, item_id='891198795482'):
        msg = {
            "lwp": "/r/SingleChatConversation/create",
            "headers": {
                "mid": generate_mid()
            },
            "body": [
                {
                    "pairFirst": f"{toid}@goofish",
                    "pairSecond": f"{self.myid}@goofish",
                    "bizType": "1",
                    "extension": {
                        "itemId": item_id
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    }
                }
            ]
        }
        await ws.send(json.dumps(msg))

    async def send_msg(self, ws, cid, toid, message: Message):
        msg_type = message["type"]
        msg = {
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {
                "mid": generate_mid()
            },
            "body": [
                {
                    "uuid": generate_uuid(),
                    "cid": f"{cid}@goofish",
                    "conversationType": 1,
                    "content": {
                        "contentType": 101,
                        "custom": {
                            "type": None,
                            "data": None
                        }
                    },
                    "redPointPolicy": 0,
                    "extension": {
                        "extJson": "{}"
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    },
                    "mtags": {},
                    "msgReadStatusSetting": 1
                },
                {
                    "actualReceivers": [
                        f"{toid}@goofish",
                        f"{self.myid}@goofish"
                    ]
                }
            ]
        }
        if msg_type == "text":
            payload = {
                "contentType": 1,
                "text": {
                    "text": message["text"]
                }
            }
            text_base64 = str(base64.b64encode(json.dumps(payload).encode('utf-8')), 'utf-8')
            msg["body"][0]["content"]["custom"]["type"] = 1
            msg["body"][0]["content"]["custom"]["data"] = text_base64
        elif msg_type == "image":
            payload = {
                "contentType": 2,
                "image": {
                    "pics": [
                        {
                            "type": 0,
                            "url": message["image_url"],
                            "width": message["width"],
                            "height": message["height"]
                        }
                    ]
                }
            }
            image_base64 = str(base64.b64encode(json.dumps(payload).encode('utf-8')), 'utf-8')
            msg["body"][0]["content"]["custom"]["type"] = 2
            msg["body"][0]["content"]["custom"]["data"] = image_base64
        elif msg_type == "audio":
            # TODO: handle audio message
            logger.error(f"不支持的消息类型: {msg_type}")
            return
        else:
            logger.error(f"不支持的消息类型: {msg_type}")
            return
        await ws.send(json.dumps(msg))

    async def init(self, ws):
        token = ''
        cookie_invalid = False
        for attempt in range(5):
            try:
                data = self.xianyu.get_token()
                token = data['data']['accessToken'] if 'data' in data and 'accessToken' in data['data'] else ''
                if not token and 'ret' in data:
                    ret_msg = data['ret'][0] if data['ret'] else ''
                    if '令牌过期' in ret_msg or 'SESSION_EXPIRED' in ret_msg:
                        # 真正的 cookie 失效
                        cookie_invalid = True
                        break
                    elif 'RGV587' in ret_msg or '被挤爆' in ret_msg or 'punish' in str(data):
                        # 风控限流，等待后重试
                        wait = 10 * (attempt + 1)
                        logger.warning(f'get_token 触发风控，{wait}秒后重试 ({attempt+1}/5)...')
                        await asyncio.sleep(wait)
                        continue
                    else:
                        logger.warning(f'get_token 未知错误: {ret_msg}，5秒后重试 ({attempt+1}/5)...')
                        await asyncio.sleep(5)
                        continue
            except Exception as e:
                logger.warning(f'get_token 异常 (attempt {attempt+1}/5): {e}')
                await asyncio.sleep(3)
            if token:
                break

        if cookie_invalid:
            logger.error('Cookie 已失效，推送飞书通知')
            notify_feishu('Cookie 失效', '令牌过期，请重新扫码登录后重启服务')
            clear_cookies()
            exit(0)

        if not token:
            # 风控/网络问题，不删 cookie，只记日志，等下次心跳或重连时自然重试
            logger.error('获取token失败（可能是风控或网络），保留 cookie 待下次重试')
            return
        msg = {
            "lwp": "/reg",
            "headers": {
                "cache-header": "app-key token ua wv",
                "app-key": "444e9908a51d1cb236a27862abc769c9",
                "token": token,
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 DingTalk(2.1.5) OS(Windows/10) Browser(Chrome/133.0.0.0) DingWeb/2.1.5 IMPaaS DingWeb/2.1.5",
                "dt": "j",
                "wv": "im:3,au:3,sy:6",
                "sync": "0,0;0;0;",
                "did": self.device_id,
                "mid": generate_mid()
            }
        }
        await ws.send(json.dumps(msg))
        current_time = int(time.time() * 1000)
        msg = {
            "lwp": "/r/SyncStatus/ackDiff",
            "headers": {"mid": generate_mid()},
            "body": [
                {
                    "pipeline": "sync",
                    "tooLong2Tag": "PNM,1",
                    "channel": "sync",
                    "topic": "sync",
                    "highPts": 0,
                    "pts": current_time * 1000,
                    "seq": 0,
                    "timestamp": current_time
                }
            ]
        }
        await ws.send(json.dumps(msg))
        logger.info('init')

    async def heart_beat(self, ws):
        while True:
            msg = {
                "lwp": "/!",
                "headers": {
                    "mid": generate_mid()
                 }
            }
            await ws.send(json.dumps(msg))
            await asyncio.sleep(15)

    def user_alive(self):
        while True:
            time.sleep(600)
            res = self.xianyu.refresh_token()
            if 'ret' in res and res['ret'] and ('FAIL' in res['ret'][0] or '令牌' in res['ret'][0]):
                logger.warning(f'refresh_token 失败: {res["ret"]}')
                notify_feishu('Cookie 失效', f'refresh_token 返回错误：{res["ret"][0]}，请重新扫码登录后重启服务')
                clear_cookies()
                exit(0)

    async def main(self):
        headers = {
            "Cookie": get_session_cookies_str(self.xianyu.session),
            "Host": "wss-goofish.dingtalk.com",
            "Connection": "Upgrade",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Origin": "https://www.goofish.com",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        threading.Thread(target=self.user_alive).start()
        async with websockets.connect(self.base_url, additional_headers=headers) as websocket:
            asyncio.create_task(self.init(websocket))
            asyncio.create_task(self.heart_beat(websocket))
            async for message in websocket:
                # logger.info(f"message: {message}")
                message = json.loads(message)
                ack = {
                    "code": 200,
                    "headers": {
                        "mid": message["headers"]["mid"] if "mid" in message["headers"] else generate_mid(),
                        "sid": message["headers"]["sid"] if "sid" in message["headers"] else '',
                    }
                }
                if 'app-key' in message["headers"]:
                    ack["headers"]["app-key"] = message["headers"]["app-key"]
                if 'ua' in message["headers"]:
                    ack["headers"]["ua"] = message["headers"]["ua"]
                if 'dt' in message["headers"]:
                    ack["headers"]["dt"] = message["headers"]["dt"]
                await websocket.send(json.dumps(ack))

                asyncio.create_task(self.handle_message(message, websocket))

    async def handle_message(self, message, websocket):
        try:
            raw = message["body"]["syncPushPackage"]["data"][0]["data"]
        except (KeyError, IndexError, TypeError):
            return

        try:
            decrypted = decrypt(raw)
            msg_obj = json.loads(decrypted)
        except Exception:
            return

        node1 = msg_obj.get("1")
        if not isinstance(node1, dict):
            return

        node10 = node1.get("10")
        if not isinstance(node10, dict):
            return

        send_user_name = node10.get("reminderTitle", "")
        send_user_id = node10.get("senderUserId", "")
        send_message = node10.get("reminderContent", "")
        if not send_message or not send_user_id:
            return

        # messageId 去重，防止重连后重复回复
        ext_json_str = node10.get("extJson", "{}")
        try:
            msg_id = json.loads(ext_json_str).get("messageId", "")
        except Exception:
            msg_id = ""
        if msg_id and msg_id in self._seen_msg_ids:
            logger.debug(f"[SKIP] 重复消息 {msg_id}，跳过")
            return
        if msg_id:
            self._seen_msg_ids.add(msg_id)
            # 最多保留最近 1000 条，避免无限增长
            if len(self._seen_msg_ids) > 1000:
                self._seen_msg_ids.pop()

        cid_raw = node1.get("2", "")
        cid = (cid_raw[0] if isinstance(cid_raw, list) else cid_raw).split('@')[0]

        # 从 reminderUrl 里解析 itemId
        item_id = ""
        reminder_url = node10.get("reminderUrl", "")
        if "itemId=" in reminder_url:
            try:
                item_id = reminder_url.split("itemId=")[1].split("&")[0]
            except Exception:
                pass

        # 店主自己发消息 → 标记该会话为人工介入，跳过 AI
        if send_user_id == self.myid:
            self._human_cids.add(cid)
            logger.info(f"[HUMAN] 店主介入会话 {cid}，后续 AI 静默")
            return

        # 该会话已有人工介入 → 跳过 AI
        if cid in self._human_cids:
            logger.debug(f"[SKIP] 会话 {cid} 已人工介入，跳过 AI: {send_message}")
            return

        logger.info(f"user: {send_user_name}({send_user_id}), item: {item_id}, msg: {send_message}")

        # 按 cid 入队，同一会话内串行处理，不同会话并发
        await self._enqueue(cid, send_message, item_id, send_user_id, websocket)

    async def _enqueue(self, cid, send_message, item_id, send_user_id, websocket):
        if cid not in self._cid_queues:
            self._cid_queues[cid] = asyncio.Queue()
        await self._cid_queues[cid].put((send_message, item_id, send_user_id, websocket))

        # 该 cid 没有 worker 在跑，启动一个
        if cid not in self._cid_workers or self._cid_workers[cid].done():
            self._cid_workers[cid] = asyncio.create_task(self._cid_worker(cid))

    async def _cid_worker(self, cid):
        queue = self._cid_queues[cid]
        while not queue.empty():
            send_message, item_id, send_user_id, websocket = await queue.get()
            try:
                import random
                await asyncio.sleep(random.uniform(1.5, 3.5))
                reply = await ask(send_message, item_info=item_id)
                logger.info(f"AI reply → cid={cid}: {reply}")
                await self.send_msg(websocket, cid, send_user_id, make_text(reply))
            except Exception as e:
                import traceback
                logger.error(f"ask/send error cid={cid}: {e}\n{traceback.format_exc()}")
            finally:
                queue.task_done()


if __name__ == '__main__':
    import os
    from utils.goofish_utils import trans_cookies_str

    cookies = load_cookies()
    if cookies:
        logger.info('从 cookies.json 加载登录态')
        cookies_str = trans_cookies_str(cookies)
        xianyuLive = XianyuLive(cookies_str)
        # 清掉 __init__ 里无 domain 的 cookie，重新以正确 domain 写入
        xianyuLive.xianyu.session.cookies.clear()
        for name, value in cookies.items():
            xianyuLive.xianyu.session.cookies.set(name, value, domain='.goofish.com', path='/')
    else:
        logger.info('未找到 cookies.json，启动扫码登录...')
        xianyu_api = qrcode_login()
        save_cookies(xianyu_api.session)
        logger.info('登录成功，cookie 已保存到 cookies.json')
        cookies_str = get_session_cookies_str(xianyu_api.session)
        xianyuLive = XianyuLive(cookies_str)
        # 用扫码登录拿到的 session 替换，保留完整 cookie 状态
        xianyuLive.xianyu.session = xianyu_api.session

    # 常驻进程 用于接收消息和 AI 自动回复
    asyncio.run(xianyuLive.main())
