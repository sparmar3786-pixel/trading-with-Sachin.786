from __future__ import annotations
import os, asyncio, json, time
from typing import Any, Callable

class FeedStatus:
    def __init__(self):
        self.connected=False; self.provider='none'; self.last_tick=None; self.error=None
    def as_dict(self):
        return {'connected':self.connected,'provider':self.provider,'lastTick':self.last_tick,'error':self.error}

class UpstoxFeed:
    """Optional authenticated Upstox WebSocket adapter. It only starts when UPSTOX_ACCESS_TOKEN is configured.
    Instrument keys are supplied through UPSTOX_INSTRUMENT_KEYS, comma-separated.
    The app still works with NSE gateway when no broker is configured.
    """
    def __init__(self, on_message:Callable[[dict],Any]):
        self.token=os.getenv('UPSTOX_ACCESS_TOKEN','').strip()
        self.keys=[x.strip() for x in os.getenv('UPSTOX_INSTRUMENT_KEYS','').split(',') if x.strip()]
        self.on_message=on_message; self.status=FeedStatus(); self.status.provider='upstox'
        self.task=None
    async def start(self):
        if not self.token or not self.keys:
            self.status.error='Set UPSTOX_ACCESS_TOKEN and UPSTOX_INSTRUMENT_KEYS to enable authenticated broker WebSocket.'
            return self.status.as_dict()
        try:
            import websockets
            import httpx
            async with httpx.AsyncClient(timeout=15) as c:
                r=await c.get('https://api.upstox.com/v3/feed/market-data-feed/authorize',headers={'Authorization':f'Bearer {self.token}','Accept':'application/json'})
                r.raise_for_status(); uri=r.json()['data']['authorized_redirect_uri']
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20, max_size=None) as ws:
                self.status.connected=True; self.status.error=None
                msg={'guid':f'ai-{int(time.time()*1000)}','method':'sub','data':{'mode':'full','instrumentKeys':self.keys}}
                await ws.send(json.dumps(msg))
                async for raw in ws:
                    self.status.last_tick=time.time()
                    if isinstance(raw, bytes):
                        # Protobuf decoding is provider-version specific; keep raw bytes available to a future decoder.
                        await self.on_message({'provider':'upstox','raw_bytes':len(raw),'received_at':self.status.last_tick})
                    else:
                        await self.on_message({'provider':'upstox','payload':raw,'received_at':self.status.last_tick})
        except Exception as e:
            self.status.connected=False; self.status.error=str(e)
        return self.status.as_dict()
