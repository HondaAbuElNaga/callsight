"""Wait for agent AGENT to be in an answered call, then ask the PBX (API call/listen) to let MONITOR listen.
Prints raw events so we learn the 30011 format. Usage: python listen_test.py 119 114 180"""
import asyncio, json, ssl, sys
sys.path.insert(0, r"D:\claude\data_analsis_call\tools")
import pbx_probe as p
import websockets

AGENT, MONITOR, SECONDS = sys.argv[1], sys.argv[2], int(sys.argv[3])


def find_channel(obj, agent):
    """Return channel_id of a member whose number == agent and which is talking/answered."""
    if isinstance(obj, dict):
        num = str(obj.get("number") or obj.get("extension") or obj.get("ext_num") or "")
        ch = obj.get("channel_id")
        state = str(obj.get("member_status") or obj.get("status") or obj.get("call_status") or "").upper()
        if num == agent and ch and state in ("ANSWERED", "ANSWER", "TALKING", "UP", ""):
            return ch, state
        for v in obj.values():
            r = find_channel(v, agent)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_channel(v, agent)
            if r:
                return r
    return None


async def main():
    c = p.YeastarClient.from_env()
    c.get_token()
    url = c.base_url.replace("https://", "wss://") + p.API_PATH + "/subscribe?access_token=" + c.access_token
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    try:
        async with websockets.connect(url, ssl=ctx, user_agent_header=p.USER_AGENT) as ws:
            await ws.send(json.dumps({"topic_list": [30011]}))
            print("subscribe:", await ws.recv(), flush=True)
            loop = asyncio.get_running_loop(); end = loop.time() + SECONDS; beat = loop.time() + 30
            while loop.time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    if loop.time() > beat:
                        await ws.send("heartbeat"); beat = loop.time() + 30
                    continue
                if msg == "heartbeat response":
                    continue
                ev = json.loads(msg)
                if isinstance(ev.get("msg"), str):
                    try: ev["msg"] = json.loads(ev["msg"])
                    except json.JSONDecodeError: pass
                print("EVENT:", json.dumps(p.mask_phone(ev), ensure_ascii=False), flush=True)
                hit = find_channel(ev, AGENT)
                if hit and hit[1] in ("ANSWERED", "ANSWER", "TALKING", "UP"):
                    ch = hit[0]
                    print(f"agent {AGENT} talking on channel {ch}; requesting listen for {MONITOR}", flush=True)
                    r = c.http.post(c._url("call/listen"), params={"access_token": c.access_token},
                                    json={"monitor": MONITOR, "channel_id": ch, "type": "listen"}).json()
                    print("LISTEN RESULT:", r, flush=True)
                    return
            print("timeout: no answered call seen for", AGENT)
    finally:
        c.revoke()

asyncio.run(main())
