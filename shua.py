"""南通大学 AIGC 课程平台（xdplus.com.cn）刷课脚本。

两种模式：
  api   直接调用平台的进度接口上报学习时长（快，几分钟刷完必修）
  play  真的打开 Chrome 播放视频，倍速连播，由页面自己上报进度（稳，耗时接近视频总长/倍速）

直接双击 刷课.bat 或执行 py shua.py 进入引导流程：
输账号密码、选模式、选要不要刷选修、选课程，刷完直接汇总刷了几门课几节。

命令行用法（不传参数就是引导模式）：
  py shua.py                       # 引导流程
  py shua.py run --elective        # 非交互，接口模式，连选修一起刷
  py shua.py run --mode play --speed 2
  py shua.py run --course 21,20    # 指定课程
  py shua.py status --all          # 看进度（含选修明细）
  py shua.py courses               # 列出账号下的课程
  py shua.py forget                # 清掉保存的登录态
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

API_BASE = "https://xdplus.com.cn/html5/new_student_nantong_university_course_front/"
SITE_BASE = (
    "https://xdplus.com.cn/aigc/nantong_university/new_student_nantong_university/"
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "刷课状态.json")

# 账号密码不写死在代码里，交给使用者自己输；上次登录过的账号会存在状态文件里。
DEFAULT_ACCOUNT = ""
DEFAULT_PASSWORD = ""


# --------------------------------------------------------------------------- #
# 状态文件：token 与上次使用的 user_id
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
    except Exception as exc:  # 状态文件写不进去不影响刷课
        print("提示：状态文件写入失败", exc)


# 本机登录过就把账号密码留一份给辅助脚本用；新机器上这两个值还是空的，
# 引导流程会正常问使用者要。
_saved = load_state()
DEFAULT_ACCOUNT = str(_saved.get("account") or "")
DEFAULT_PASSWORD = str(_saved.get("password") or "")


# --------------------------------------------------------------------------- #
# HTTP 客户端
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self, account: str, password: str, state: dict):
        self.account = account
        self.password = password
        self.state = state
        self.token = state.get("token") or ""
        self.user_id = state.get("user_id")

    def _post(self, path: str, fields: dict, token: str | None = None):
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
            for k, v in fields.items()
        ]
        body = ("".join(parts) + f"--{boundary}--\r\n").encode("utf-8")
        req = urllib.request.Request(API_BASE + path, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Accept", "application/json, text/plain, */*")
        req.add_header("User-Agent", UA)
        req.add_header("Content-Length", str(len(body)))
        use_token = token if token is not None else self.token
        if use_token:
            req.add_header("token", use_token)
        with urllib.request.urlopen(req, timeout=40) as resp:
            raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:
            raise RuntimeError(f"接口返回的不是 JSON（{path}）：{raw[:160]}")

    def call(self, path: str, fields: dict | None = None, retry: bool = True):
        fields = dict(fields or {})
        try:
            out = self._post(path, fields)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"网络请求失败：{exc}") from exc
        code = out.get("code")
        if code in (401, 2007) and retry:
            print("登录态失效，重新登录…")
            self.login()
            return self.call(path, fields, retry=False)
        return out

    def login(self, force: bool = False):
        """复用已有 token；token 不可用时才重新登录。"""
        same_account = self.state.get("account") == self.account
        if not force and self.token and self.user_id and same_account:
            out = self.call("getUserDetail", {"user_id": self.user_id}, retry=False)
            if out.get("code") == 200:
                print(f"复用已保存的登录态：{self.state.get('name') or self.user_id}")
                return out.get("data") or {}
            print("已保存的登录态不可用，重新登录…")
        out = self._post("login", {"account": self.account, "password": self.password})
        if out.get("code") != 200:
            raise RuntimeError(f"登录失败：{out.get('msg')}")
        data = out.get("data") or {}
        self.token = data.get("token") or ""
        self.user_id = data.get("user_id")
        if not self.token or not self.user_id:
            raise RuntimeError(f"登录响应缺少 token/user_id：{json.dumps(out, ensure_ascii=False)[:200]}")
        self.state["token"] = self.token
        self.state["user_id"] = self.user_id
        self.state["account"] = self.account
        self.state["name"] = data.get("user_name") or ""
        self.state["password"] = self.password
        save_state(self.state)
        print(f"已登录：{data.get('user_name')} (user_id={self.user_id})")
        return data

    def courses(self) -> list[dict]:
        out = self.call(f"courseList?user_id={self.user_id}&page=1&limit=50", {})
        return ((out.get("data") or {}).get("list")) or []

    def course_detail(self, course_id) -> dict:
        out = self.call("courseDetail", {"user_id": self.user_id, "course_id": course_id})
        if out.get("code") != 200:
            raise RuntimeError(f"读取课程失败：{out.get('msg')}")
        return out.get("data") or {}

    def submit(self, course_id, class_id, chapter_id, period_id, seconds, period_type):
        return self.call(
            "submitStudyDuration",
            {
                "user_id": self.user_id,
                "course_id": course_id,
                "chapter_id": chapter_id,
                "period_id": period_id,
                "class_id": class_id,
                "study_duration": int(seconds),
                "period_type": int(period_type or 0),
            },
        )


    def user_payload(self) -> dict:
        """给浏览器用的登录信息，形状跟网页自己的 acg-user 一致。"""
        out = self.call("getUserDetail", {"user_id": self.user_id})
        raw = out.get("data") or {}
        data = dict(raw)
        data["id"] = raw.get("user_id") or self.user_id
        data.setdefault("classes", raw.get("class") if isinstance(raw.get("class"), list) else [])
        data.setdefault("coupons", raw.get("coupons") if isinstance(raw.get("coupons"), list) else [])
        return data


# --------------------------------------------------------------------------- #
# 课程数据整理
# --------------------------------------------------------------------------- #
def flatten(detail: dict) -> tuple[dict, list[dict]]:
    course = detail.get("course") or {}
    items: list[dict] = []
    for ch in detail.get("chapters") or []:
        for p in ch.get("periods") or []:
            need = float(p.get("period_hour") or 0)
            done = float(p.get("class_schedule") or 0)
            items.append(
                {
                    "chapter_id": ch.get("id"),
                    "chapter_name": ch.get("chapter_name") or "",
                    "period_id": p.get("id"),
                    "period_name": p.get("period_name") or "",
                    "need_min": need,
                    "done_min": done,
                    "elective": int(p.get("period_type") or 0) == 1,
                    "locked": p.get("is_play") is False,
                }
            )
    return course, items


def fmt_min(minutes: float) -> str:
    total = int(round(max(0.0, minutes)))
    return f"{total // 60}小时{total % 60:02d}分"


def fmt_pct(value) -> str:
    """平台返回的进度是个浮点数，收一下小数位再显示。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{v:.2f}".rstrip("0").rstrip(".")


def print_status(api: Api, course_id: int, show_all: bool = False) -> dict:
    detail = api.course_detail(course_id)
    course, items = flatten(detail)
    required = [x for x in items if not x["elective"]]
    electives = [x for x in items if x["elective"]]
    done_req = sum(min(x["done_min"], x["need_min"]) for x in required)
    need_req = sum(x["need_min"] for x in required)
    finished = sum(1 for x in required if x["need_min"] > 0 and x["done_min"] >= x["need_min"])
    finished_elec = sum(
        1 for x in electives if x["need_min"] > 0 and x["done_min"] >= x["need_min"]
    )
    done_elec = sum(min(x["done_min"], x["need_min"]) for x in electives)
    print(f"课程：{course.get('course_name')}  (course_id={course_id})")
    print(f"班级：{course.get('class_type')}  class_id={course.get('class_id')}")
    print(
        f"必修：{finished}/{len(required)} 节完成，已学 {fmt_min(done_req)} / 需 {fmt_min(need_req)}"
    )
    print(
        f"选修：{finished_elec}/{len(electives)} 节完成，已学 {fmt_min(done_elec)} / 共 "
        f"{fmt_min(sum(x['need_min'] for x in electives))}（选修不计入课程进度）"
    )
    print(f"平台显示进度：{fmt_pct(course.get('total_progress'))}%")
    print()
    for x in items:
        if not show_all and x["elective"]:
            continue
        tag = "选修" if x["elective"] else "必修"
        state = "已完成"
        if x["need_min"] <= 0 or x["done_min"] < x["need_min"]:
            state = "已锁定" if x["locked"] else "未完成"
        print(
            f"  [{tag}] {x['period_id']:>4} {state:<4} "
            f"{x['done_min']:>6.0f}/{x['need_min']:<6.0f}分  {x['period_name']}"
        )
    return detail


# --------------------------------------------------------------------------- #
# api 模式
# --------------------------------------------------------------------------- #
def grind_api(api: Api, course_id: int, args) -> None:
    detail = api.course_detail(course_id)
    course, items = flatten(detail)
    class_id = course.get("class_id")
    todo_all = [
        x for x in items
        if (args.elective or not x["elective"]) and x["need_min"] > 0
        and x["done_min"] < x["need_min"]
    ]
    if not todo_all:
        print("所有课时都已完成，无需刷课。")
        return
    print(
        f"待刷 {len(todo_all)} 节，共 {fmt_min(sum(x['need_min'] - x['done_min'] for x in todo_all))}"
        f"（每节分 {args.steps} 次上报，间隔 {args.gap}s）"
    )
    start = time.time()
    played = 0
    for round_no in range(1, args.max_rounds + 1):
        pending = [
            x for x in todo_all
            if x["done_min"] < x["need_min"]
        ]
        if not pending:
            break
        progressed = False
        for x in pending:
            if args.limit and played >= args.limit:
                break
            detail = api.course_detail(course_id)
            _, fresh = flatten(detail)
            cur = next((f for f in fresh if f["period_id"] == x["period_id"]), x)
            x.update(cur)
            if cur["need_min"] <= 0 or cur["done_min"] >= cur["need_min"]:
                continue
            if cur["locked"] and not cur["elective"]:
                print(
                    f"  跳过 {cur['period_id']} {cur['period_name']}："
                    f"仍是锁定状态（等前面的课时解锁）"
                )
                continue
            progressed = True
            target = cur["need_min"]
            ptype = 1 if cur["elective"] else 0
            # 分 steps 次递增上报，而不是一次报满，留一条像样的心跳轨迹
            step = max(1.0, target / max(1, args.steps))
            report = min(target, max(cur["done_min"] + 1.0, step))
            while True:
                out = api.submit(course_id, class_id, cur["chapter_id"],
                                 cur["period_id"], report * 60, ptype)
                stored = float((out.get("data") or {}).get("study_duration") or 0)
                if report >= target or stored >= target:
                    break
                if args.gap:
                    time.sleep(args.gap)
                report = min(target, report + step)
            data = out.get("data") or {}
            stored = float(data.get("study_duration") or cur["done_min"])
            cur["done_min"] = stored
            x.update(cur)
            played += 1
            pct = course_progress(api, course_id)
            print(
                f"  [{round_no}] {cur['period_id']} {cur['period_name']} -> "
                f"{stored:.0f}/{target:.0f}分  课程进度 {fmt_pct(pct)}%"
            )
            if args.stop_at and pct and pct >= args.stop_at:
                print(f"  已达到目标进度 {args.stop_at}%，停止。")
                return
            if args.gap:
                time.sleep(args.gap)
        if not progressed:
            print("没有可上报的课时了（可能都被锁定），停止。")
            break
    detail = api.course_detail(course_id)
    course, items = flatten(detail)
    required = [x for x in items if not x["elective"]]
    print(
        f"\n本轮结束，用时 {(time.time() - start) / 60:.1f} 分钟，"
        f"课程进度 {fmt_pct(course.get('total_progress'))}%，"
        f"必修完成 {sum(1 for x in required if x['need_min'] > 0 and x['done_min'] >= x['need_min'])}"
        f"/{len(required)}"
    )


def course_progress(api: Api, course_id: int):
    try:
        return (api.course_detail(course_id).get("course") or {}).get("total_progress")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# play 模式
# --------------------------------------------------------------------------- #
def grind_play(api: Api, course_id: int, args) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "play 模式需要 playwright：先执行 py -m pip install playwright"
        )

    detail = api.course_detail(course_id)
    course, items = flatten(detail)
    profile = os.path.join(HERE, ".chrome-play")
    os.makedirs(profile, exist_ok=True)
    rate = max(0.5, min(float(args.speed), 3.0))
    window = "无窗口" if args.headless else "有窗口"
    print(f"play 模式：倍速 {rate}x，{window}，浏览器配置目录 {profile}")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile, channel="chrome", headless=args.headless,
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # 直接把 Python 端拿到的登录态塞进浏览器，省得再登录一次把网页端顶掉
        boot = (
            "(() => { try {"
            f"localStorage.setItem('acg-token', {json.dumps(api.token)});"
            f"localStorage.setItem('acg-user', {json.dumps(json.dumps(api.user_payload(), ensure_ascii=False))});"
            f"localStorage.setItem('video_playback_rate', {json.dumps(str(rate))});"
            "Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });"
            "Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });"
            "} catch (e) {} })();"
        )
        ctx.add_init_script(boot)
        page.goto(SITE_BASE + f"#/courses/primary/{course_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if "#/login" in page.url:
            raise RuntimeError(
                "浏览器里没能进入课程页，登录态可能已失效；重跑一次脚本即可。"
            )
        played = 0
        skips = 0
        try:
            while True:
                detail = api.course_detail(course_id)
                _, items = flatten(detail)
                todo = [
                    x for x in items
                    if (args.elective or not x["elective"])
                    and x["need_min"] > 0 and x["done_min"] < x["need_min"] and not x["locked"]
                ]
                if not todo:
                    print("全部课时完成。")
                    break
                x = todo[0]
                if skips >= 3:
                    print("连续 3 节都打不开播放器，停止。")
                    break
                print(f"播放 {x['period_id']} {x['period_name']} "
                      f"({x['done_min']:.0f}/{x['need_min']:.0f}分)")
                page.goto(
                    f"{SITE_BASE}#/video/primary/{course_id}?lesson={x['period_id']}",
                    wait_until="domcontentloaded",
                )
                video_seen = False
                for _ in range(20):
                    page.wait_for_timeout(1500)
                    if page.evaluate("() => !!document.querySelector('video')"):
                        video_seen = True
                        break
                    if "#/login" in page.url:
                        raise RuntimeError("播放中途登录态失效，重跑脚本即可。")
                if not video_seen:
                    print("    这一节没找到播放器，跳过。")
                    skips += 1
                    continue
                skips = 0
                started = time.time()
                last_log = time.time()
                watched = x["done_min"] * 60.0
                ended = False
                while time.time() - started < args.period_timeout * 60:
                    st = page.evaluate(
                        """() => { const v = document.querySelector('video');
                        if (!v) return null; return { t: v.currentTime, d: v.duration,
                        p: v.paused, e: v.ended, r: v.playbackRate }; }"""
                    )
                    if not st:
                        page.wait_for_timeout(3000)
                        continue
                    if st["p"] and not st["e"]:
                        page.evaluate(
                            "(opts) => { const v = document.querySelector('video');"
                            "if (!v) return; v.muted = opts.mute;"
                            "v.playbackRate = opts.rate;"
                            "const pr = v.play(); if (pr && pr.catch) pr.catch(() => {}); }",
                            {"mute": not args.no_mute, "rate": rate},
                        )
                    watched = max(watched, float(st["t"] or 0))
                    if time.time() - last_log > 45:
                        last_log = time.time()
                        pct = course_progress(api, course_id)
                        print(f"    播放中 {st['t']:.0f}/{st['d']:.0f}s "
                              f"({st['r']}x)，课程进度 {fmt_pct(pct)}%")
                    if st["e"] or (st["d"] and st["t"] >= st["d"] - 1.5):
                        ended = True
                        page.wait_for_timeout(4000)
                        break
                    page.wait_for_timeout(4000)
                # 页面自己会上报，这里只是补一刀：看完报满，没看完报实际看过的秒数
                seconds = x["need_min"] * 60 if ended else watched
                api.submit(course_id, course.get("class_id"), x["chapter_id"],
                           x["period_id"], seconds, 1 if x["elective"] else 0)
                pct = course_progress(api, course_id)
                print(f"    本节{'看完' if ended else '先停在'} "
                      f"{seconds / 60:.1f}/{x['need_min']:.0f}分，课程进度 {fmt_pct(pct)}%")
                played += 1
                if args.limit and played >= args.limit:
                    print(f"已达到 --limit {args.limit} 节，停止。")
                    break
                if args.stop_at and pct and pct >= args.stop_at:
                    print(f"已达到目标进度 {args.stop_at}%，停止。")
                    break
        except KeyboardInterrupt:
            print("\n已停止（Ctrl+C），浏览器保持打开，可直接手动继续观看。")
            return
        ctx.close()


# --------------------------------------------------------------------------- #
# 引导流程：账号密码、模式、选修、课程
# --------------------------------------------------------------------------- #
def mask_account(account: str) -> str:
    s = str(account or "")
    if len(s) >= 7:
        return s[:3] + "****" + s[-4:]
    return s


def ask(prompt: str, default: str = "") -> str:
    """读一行输入，直接回车就用默认值。"""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        raw = ""
    except KeyboardInterrupt:
        print()
        raise SystemExit("已取消。")
    return raw or default


def ask_secret(prompt: str) -> str:
    if not (sys.stdin and sys.stdin.isatty()):
        # 没有真控制台时 getpass 会去读 CONIN$ 卡住，退回普通输入
        return ask(prompt)
    try:
        return getpass.getpass(f"{prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def wizard(args) -> None:
    """没传参数时走这里：把该问的一次问完，命令行里传过的就不再问。"""
    state = load_state()
    saved = str(state.get("account") or "")
    print("=" * 48)
    print("  南通大学 AIGC 课程平台 刷课")
    print("=" * 48)

    if saved and not args.account:
        name = state.get("name") or ""
        print(f"上次登录的账号：{name} {mask_account(saved)}")
    account = args.account or ask("账号（手机号）", saved)
    if not account:
        raise SystemExit("账号不能为空")
    if args.password:
        password = args.password
    elif account == saved and state.get("password"):
        password = str(state["password"])
        print("密码沿用上次保存的；要换密码加 --password，或先跑 py shua.py forget")
    else:
        password = ask_secret("密码")
        if not password:
            raise SystemExit("密码不能为空")
    args.account, args.password = account, password

    if args.mode is None:
        print("\n刷课方式：1 = 接口模式（快，几分钟刷完，不开浏览器）")
        print("           2 = 播放模式（打开 Chrome 真播放，稳，耗时接近视频时长除以倍速）")
        args.mode = "play" if ask("选哪种？", "1") == "2" else "api"
    if args.elective is None:
        args.elective = ask("连选修课一起刷吗？(y/n)", "y").lower().startswith("y")
    if args.mode == "play" and args.speed is None:
        try:
            args.speed = float(ask("播放倍速（平台最高 3）", "2"))
        except ValueError:
            args.speed = 2.0
    print()


def choose_courses(api: Api, wanted: str | None) -> list[int]:
    """wanted 为空时交互问；"all" 全刷；"21,20" 指定课程。"""
    items = api.courses()
    if not items:
        raise RuntimeError("这个账号下没有课程，确认一下账号对不对。")
    if wanted:
        if wanted.strip().lower() == "all":
            return [int(it["id"]) for it in items]
        # 命令行里传的是课程 id；id 对不上时再退一步当成第几门课
        known = {int(it["id"]) for it in items}
        picked: list[int] = []
        for tok in re.split(r"[,;\s]+", wanted.strip()):
            if not tok.isdigit():
                continue
            n = int(tok)
            if n in known:
                picked.append(n)
            elif 1 <= n <= len(items):
                print(f"  把 {n} 当作第 {n} 门课："
                      f"{items[n - 1].get('course_name')} (id={items[n - 1]['id']})")
                picked.append(int(items[n - 1]["id"]))
            else:
                print(f"  没有 id 或序号是 {n} 的课程，跳过")
        if not picked:
            raise SystemExit(f"没找到课程 {wanted}，先跑 py shua.py courses 看看有哪些课")
        return picked

    interactive = bool(sys.stdin and sys.stdin.isatty())
    if not interactive:
        if len(items) == 1:
            return [int(items[0]["id"])]
        raise SystemExit("非交互模式下要用 --course 指定课程 id，或者 --course all")

    print("这个账号下的课程：")
    for n, it in enumerate(items, 1):
        print(f"  {n}. {it.get('course_name')}  "
              f"(id={it['id']} {it.get('class_type')} 进度 {fmt_pct(it.get('progress_percent'))}%)")
    ans = ask("要选哪几门？输序号，多个用逗号隔开，all 是全选，回车默认全选", "all")
    if ans.strip().lower() == "all":
        return [int(it["id"]) for it in items]
    ids: list[int] = []
    for tok in re.split(r"[,;\s]+", ans.strip()):
        if not tok.isdigit():
            continue
        # 列表里是按序号显示的，这里按序号取
        n = int(tok)
        ids.append(int(items[n - 1]["id"]) if 1 <= n <= len(items) else n)
    return ids or [int(items[0]["id"])]


# --------------------------------------------------------------------------- #
# 刷课结果汇总
# --------------------------------------------------------------------------- #
def course_stats(api: Api, course_id: int, elective: bool = True) -> dict:
    """取一门课的完成节数和已学分钟，用来算本次刷进去多少。

    elective=False 时只统计必修，这样"刷满没刷满"跟本次实际刷的范围一致。
    """
    _, items = flatten(api.course_detail(course_id))
    target = [x for x in items
              if x["need_min"] > 0 and (elective or not x["elective"])]
    return {
        "sections": len(target),
        "finished": sum(1 for x in target if x["done_min"] >= x["need_min"]),
        "minutes": sum(min(x["done_min"], x["need_min"]) for x in target),
        "required": sum(x["need_min"] for x in target),
    }


def print_summary(rows: list[dict], elapsed: float) -> None:
    print("\n" + "=" * 48)
    print("  本次刷课结果")
    print("=" * 48)
    if not rows:
        print("没有刷任何课程。")
        return
    for r in rows:
        if r.get("skipped"):
            print(f"{r['name']}：{r['reason']}")
            continue
        print(f"{r['name']}")
        if not r["gained_sections"] and not r["gained_min"]:
            if r["sections"] and r["after"]["finished"] >= r["sections"]:
                print(f"  没有要刷的课时，{r['sections']} 节已经全部刷满")
            else:
                print("  这一轮没有推进（课时可能还锁着，或已到上报上限）")
        else:
            print(f"  刷了 {r['gained_sections']} 节，新增 {fmt_min(r['gained_min'])}，"
                  f"用时 {r['elapsed'] / 60:.1f} 分钟")
        print(f"  完成节数 {r['before']['finished']}/{r['sections']} -> "
              f"{r['after']['finished']}/{r['sections']}，"
              f"已学 {fmt_min(r['before']['minutes'])} -> {fmt_min(r['after']['minutes'])}"
              f"（这门课要求 {fmt_min(r['after']['required'])}）")
        if r.get("progress_before") is not None and r.get("progress_after") is not None:
            print(f"  平台显示进度 {fmt_pct(r['progress_before'])}% -> "
                  f"{fmt_pct(r['progress_after'])}%")
    touched = sum(1 for r in rows if not r.get("skipped"))
    tot_sec = sum(r.get("gained_sections") or 0 for r in rows)
    tot_min = sum(r.get("gained_min") or 0 for r in rows)
    full = sum(1 for r in rows
               if not r.get("skipped") and r["sections"] and r["after"]["finished"] >= r["sections"])
    moved = sum(1 for r in rows if not r.get("skipped")
                and (r["gained_sections"] or r["gained_min"]))
    print("-" * 48)
    print(f"合计：刷了 {moved} 门课，{tot_sec} 节，新增 {fmt_min(tot_min)}，"
          f"总用时 {elapsed / 60:.1f} 分钟")
    print(f"      本次共处理 {touched} 门课，其中 {full} 门课时已全部刷满")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="南通大学 AIGC 课程平台刷课脚本",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("command", nargs="?", default=None,
                        choices=["run", "status", "courses", "login", "forget"],
                        help="不写就是引导流程；run 直接刷，status 看进度，"
                             "courses 列课程，forget 清掉保存的登录态")
    parser.add_argument("--account", default=None, help="不写则交互询问，其次用状态文件里的")
    parser.add_argument("--password", default=None, help="不写则交互询问（输入不回显）")
    parser.add_argument("--course", default=None,
                        help="课程 id，多个用逗号隔开，all 是全刷；不写则交互询问")
    parser.add_argument("--mode", default=None, choices=["api", "play"],
                        help="api=接口上报（快），play=真实播放视频（稳）")
    parser.add_argument("--speed", type=float, default=None,
                        help="play 模式倍速，上限 3")
    parser.add_argument("--steps", type=int, default=6,
                        help="api 模式每节分几次上报，模拟心跳轨迹")
    parser.add_argument("--gap", type=float, default=2.0, help="api 模式每次上报间隔（秒）")
    parser.add_argument("--max-rounds", type=int, default=8,
                        help="api 模式最多重扫几轮，等待锁定的课时解锁")
    parser.add_argument("--elective", dest="elective", action="store_const", const=True,
                        default=None, help="连选修也一起刷")
    parser.add_argument("--no-elective", dest="elective", action="store_const", const=False,
                        help="只刷必修")
    parser.add_argument("--limit", type=int, default=0, help="本次最多刷几节，0 表示不限")
    parser.add_argument("--no-mute", action="store_true", help="play 模式保留声音")
    parser.add_argument("--stop-at", type=float, default=None,
                        help="课程进度达到该百分比就停止")
    parser.add_argument("--period-timeout", type=float, default=90.0,
                        help="play 模式单节最长等待（分钟）")
    parser.add_argument("--headless", action="store_true",
                        help="play 模式不显示窗口（此时必须保持静音）")
    parser.add_argument("--all", action="store_true", help="status 时列出选修")
    args = parser.parse_args()

    guided = args.command is None
    args.command = args.command or "run"

    if args.command == "forget":
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)
            print("已清掉保存的登录态（刷课状态.json）。")
        else:
            print("没有保存的登录态可清。")
        return 0

    state = load_state()
    if guided:
        wizard(args)
    else:
        # 命令行模式下不提问，缺的东西按老默认值补
        args.account = args.account or DEFAULT_ACCOUNT or str(state.get("account") or "")
        if args.account and args.account == state.get("account"):
            args.password = args.password or str(state.get("password") or "")
        args.mode = args.mode or "api"
        args.elective = False if args.elective is None else bool(args.elective)
        args.speed = 2.0 if args.speed is None else args.speed

    if not args.account or not args.password:
        print("缺账号或密码。可以直接跑 py shua.py 走引导，"
              "或者加 --account 177xxxx xxxx --password xxxxx")
        return 2

    api = Api(args.account, args.password, state)
    try:
        api.login()
    except RuntimeError as exc:
        print(exc)
        print("核对一下账号密码；也可以先跑 py shua.py forget 清掉保存的登录态再来一次。")
        return 2

    if args.command == "login":
        print("登录正常。")
        return 0
    if args.command == "courses":
        for it in api.courses():
            print(f"{it['id']}  {it.get('course_name')}  ({it.get('class_type')}) "
                  f"进度 {fmt_pct(it.get('progress_percent'))}%")
        return 0

    course_ids = choose_courses(api, args.course)
    if not course_ids:
        print("没选中课程。")
        return 2

    if args.command == "status":
        for cid in course_ids:
            print_status(api, cid, show_all=args.all)
        return 0

    rows: list[dict] = []
    started = time.time()
    for n, cid in enumerate(course_ids, 1):
        name = cid
        try:
            course = (api.course_detail(cid).get("course") or {})
            name = course.get("course_name") or f"course_id={cid}"
        except Exception:
            pass
        print("\n" + "-" * 48)
        print(f"[{n}/{len(course_ids)}] {name}  (course_id={cid})")
        print(f"刷课方式：{'接口模式' if args.mode == 'api' else '播放模式'}，"
              f"{'含选修' if args.elective else '只刷必修'}")
        print("-" * 48)
        try:
            before = course_stats(api, cid, args.elective)
            progress_before = course_progress(api, cid)
        except Exception as exc:
            print(f"  读不到这门课的进度，跳过：{exc}")
            rows.append({"skipped": True, "name": name, "reason": f"读不到进度（{exc}）"})
            continue

        t0 = time.time()
        try:
            if args.mode == "api":
                grind_api(api, cid, args)
            else:
                grind_play(api, cid, args)
        except KeyboardInterrupt:
            print("\n已停止（Ctrl+C），已刷进去的进度不会丢。")
        except RuntimeError as exc:
            print("出错：", exc)
            if n < len(course_ids):
                print("后面的课程不再继续。")
        elapsed = time.time() - t0

        try:
            after = course_stats(api, cid, args.elective)
        except Exception as exc:
            print(f"  刷完再读进度失败：{exc}")
            after, progress_after = before, None
        else:
            progress_after = course_progress(api, cid)
        rows.append({
            "name": name,
            "sections": after["sections"],
            "before": before,
            "after": after,
            "gained_sections": max(0, after["finished"] - before["finished"]),
            "gained_min": max(0.0, after["minutes"] - before["minutes"]),
            "elapsed": elapsed,
            "progress_before": progress_before,
            "progress_after": progress_after,
        })
        if args.stop_at and (progress_after or 0) >= args.stop_at:
            print(f"\n整体已到目标进度 {args.stop_at}%，后面的课程不刷了。")
            break

    print_summary(rows, time.time() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
