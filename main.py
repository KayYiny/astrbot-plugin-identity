import os
import re
import sqlite3
import random
from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Any
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp


class RandomIdentityPlugin(Star):
    """
    AstrBot 随机抽身份插件
    功能：
    - 在插件面板中配置身份角色列表，动态生成抽取指令
    - 随机抽取群友作为不同身份，支持@与不带@两种模式
    - 每个身份每天只能抽取一次
    - 活跃群友优先抽取（可配置）
    """
    def __init__(self, context: Context, config: AstrBotConfig):
        """
        插件初始化方法
        """
        super().__init__(context)
        self.config = config

        # 使用插件自身目录下的 data/ 存储数据库，
        # 这样面板删除插件时数据也会被一并清理
        self.data_dir = os.path.join("data", "plugin_data", "随机抽身份")
        self.db_path = os.path.join(self.data_dir, "identity_records.db")

        # 自动迁移旧数据目录（random_identity → 插件内 data/）
        self._migrate_old_data()

        os.makedirs(self.data_dir, exist_ok=True)
        self._init_db()
        self._cleanup_stale_records()
        logger.info("随机抽身份插件已加载 (SQLite)")

    # ---------- 数据库 ----------

    def _migrate_old_data(self):
        """从旧版路径迁移数据库文件。"""
        old_paths = [
            os.path.join("data", "plugins", "random_identity", "identity_records.db"),
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "data", "identity_records.db"
            ),
        ]
        for old_db in old_paths:
            if os.path.exists(old_db) and not os.path.exists(self.db_path):
                os.makedirs(self.data_dir, exist_ok=True)
                import shutil
                shutil.copy2(old_db, self.db_path)
                logger.info(f"已迁移旧数据库: {old_db} → {self.db_path}")

    def _cleanup_stale_records(self):
        """清理配置中已删除身份的遗留记录。"""
        identities = self.config.get("identities", [])
        if not identities:
            return
        placeholders = ",".join("?" for _ in identities)
        self._cursor.execute(
            f"DELETE FROM draw_records WHERE kind NOT IN ({placeholders})",
            identities,
        )
        deleted = self._cursor.rowcount
        if deleted:
            self._conn.commit()
            logger.info(f"已清理 {deleted} 条已删除身份的遗留记录")

    def _init_db(self):
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS draw_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    subject_name TEXT NOT NULL,
                    with_at INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_lookup "
                "ON draw_records(group_id, user_id, kind, created_at)"
            )
            self._cursor = self._conn.cursor()
            self._conn.commit()
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")
            raise

    def _is_new_day(self) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        self._cursor.execute("SELECT value FROM meta WHERE key='current_date'")
        row = self._cursor.fetchone()
        stored = row[0] if row else ""
        return stored != today

    def _reset_daily_records(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self._cursor.execute(
            "REPLACE INTO meta (key, value) VALUES ('current_date', ?)", (today,)
        )
        self._conn.commit()
        logger.info("每日记录已更新")

    # ---------- 群成员 ----------

    async def _get_group_members(self, event: AstrMessageEvent) -> List[Dict[str, Any]]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                logger.warning("无法获取群组ID")
                return []

            if event.get_platform_name() == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )
                assert isinstance(event, AiocqhttpMessageEvent)
                client = event.bot
                payloads = {"group_id": group_id, "no_cache": True}
                return await client.api.call_action(
                    "get_group_member_list", **payloads
                )
            else:
                logger.warning(f"不支持的平台: {event.get_platform_name()}")
                return []
        except Exception as e:
            logger.error(f"获取群成员失败: {e}")
            return []

    # ---------- 活跃概率抽取 ----------

    def _pick_member(self, available: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        按活跃概率从可用成员中抽取。

        若 active_days > 0，90% 概率从活跃群友（近 N 天有发言）中抽取，
        10% 从不活跃群友中抽取。某个池为空时自动降级到另一池。
        若 active_days = 0，纯随机。
        """
        active_days = self.config.get("active_days", 0)
        if active_days <= 0:
            return self._pick_member(available)

        now = datetime.now().timestamp()
        cutoff = now - active_days * 86400

        active = []
        inactive = []
        for m in available:
            last = m.get("last_sent_time")
            if last and last >= cutoff:
                active.append(m)
            else:
                inactive.append(m)

        # 如果某个池为空，自动降级
        if not active:
            return random.choice(inactive)
        if not inactive:
            return random.choice(active)

        return random.choice(active if random.random() < 0.9 else inactive)

    # ---------- 记录 ----------

    def _get_today_count(self, group_id: str, user_id: str, kind: str) -> int:
        if self._is_new_day():
            self._reset_daily_records()
        self._cursor.execute(
            "SELECT COUNT(*) FROM draw_records "
            "WHERE group_id=? AND user_id=? AND kind=? "
            "AND date(created_at)=date('now','localtime')",
            (group_id, user_id, kind),
        )
        return self._cursor.fetchone()[0]

    def _add_record(
        self,
        group_id: str,
        user_id: str,
        subject_id: str,
        subject_name: str,
        with_at: bool,
        kind: str,
    ):
        if self._is_new_day():
            self._reset_daily_records()
        self._cursor.execute(
            "INSERT INTO draw_records "
            "(group_id, user_id, subject_id, subject_name, with_at, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (group_id, user_id, subject_id, subject_name, 1 if with_at else 0, kind),
        )
        self._conn.commit()
        logger.info(
            f"用户{user_id}在群{group_id}抽取了{subject_name}({subject_id}) 类型={kind}"
        )


    # ---------- 核心抽取逻辑 ----------

    async def _draw_identity_common(
        self, event: AstrMessageEvent, kind: str, with_at: bool = True
    ):
        if event.is_private_chat():
            yield event.plain_result("该功能仅在群聊中可用哦~")
            return

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        bot_id = event.get_self_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return

        # 每个身份每天只能抽一次
        today_count = self._get_today_count(group_id, user_id, kind=kind)
        if today_count >= 1:
            # 查询今天已经抽到的人
            self._cursor.execute(
                "SELECT subject_name FROM draw_records "
                "WHERE group_id=? AND user_id=? AND kind=? "
                "AND date(created_at)=date('now','localtime') "
                "LIMIT 1",
                (group_id, user_id, kind),
            )
            row = self._cursor.fetchone()
            name = row["subject_name"] if row else ""
            yield event.plain_result(
                f"你今天已经抽过{kind}了，明天再来吧！\n"
                f"当前{kind}是：{name}"
            )
            return

        members = await self._get_group_members(event)
        if not members:
            yield event.plain_result("暂时无法获取群成员列表，请确保Bot有相应权限")
            return

        excluded = {str(uid) for uid in self.config.get("excluded_users", [])}
        excluded.add(str(bot_id))
        excluded.add(str(user_id))

        available = [
            m for m in members if str(m.get("user_id", "")) not in excluded
        ]
        if not available:
            yield event.plain_result("群里没有可以抽取的成员哦~")
            return

        target = self._pick_member(available)
        target_id = target.get("user_id")
        target_name = (
            target.get("card")
            or target.get("nickname")
            or f"用户{target.get('user_id')}"
        )

        self._add_record(
            group_id, user_id, str(target_id), target_name, with_at, kind=kind
        )

        avatar_url = (
            f"https://q4.qlogo.cn/headimg_dl?dst_uin={target_id}&spec=100"
        )

        text_content = f"  你的今日{kind}是：\n"

        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(text_content),
            Comp.Image.fromURL(avatar_url),
        ]

        if with_at:
            chain.append(Comp.At(qq=target_id))
            chain.append(Comp.Plain("​"))
        else:
            chain.append(Comp.Plain(target_name))

        yield event.chain_result(chain)

    # ---------- 动态指令分发 ----------

    @filter.regex(r'^(?:今日|抽)(.+?)(@?)$')
    async def on_dynamic_draw(self, event: AstrMessageEvent):
        """
        动态匹配「今日{身份}」「抽{身份}」指令。
        如果身份已在配置中则执行抽取，否则跳过。
        """
        text = event.message_str.strip()
        m = re.match(r'^(?:今日|抽)(.+?)(@?)$', text)
        if not m:
            return

        identity_name = m.group(1)
        with_at = bool(m.group(2))

        identities = self.config.get("identities", [])
        if identity_name not in identities:
            return

        async for r in self._draw_identity_common(
            event, kind=identity_name, with_at=with_at
        ):
            yield r

    # ---------- 一次性抽取所有身份 ----------

    @filter.command("来随机吧")
    async def roll_all_identities(self, event: AstrMessageEvent):
        """一次性抽取所有配置身份（每个身份一次）。"""
        if event.is_private_chat():
            yield event.plain_result("该功能仅在群聊中可用哦~")
            return

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        bot_id = event.get_self_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return

        identities = self.config.get("identities", [])
        if not identities:
            yield event.plain_result("暂无配置身份，请在插件面板中添加身份角色。")
            return

        # 查询今日已抽取的身份记录
        if self._is_new_day():
            self._reset_daily_records()
        self._cursor.execute(
            "SELECT * FROM draw_records "
            "WHERE group_id=? AND user_id=? "
            "AND date(created_at)=date('now','localtime') "
            "ORDER BY created_at",
            (group_id, user_id),
        )
        drawn_records = {row["kind"]: row for row in self._cursor.fetchall()}

        # 过滤出尚未抽取的身份
        to_draw = [i for i in identities if i not in drawn_records]

        if not to_draw:
            # 已抽满所有身份，列出已有身份
            parts = ["你今天已经抽满了所有身份，明天再来吧~\n\n当前拥有的身份："]
            for identity in identities:
                r = drawn_records[identity]
                parts.append(f"✅ {identity}: {r['subject_name']}")
            yield event.plain_result("\n".join(parts))
            return

        members = await self._get_group_members(event)
        if not members:
            yield event.plain_result("暂时无法获取群成员列表")
            return

        excluded = {str(uid) for uid in self.config.get("excluded_users", [])}
        excluded.add(str(bot_id))
        excluded.add(str(user_id))

        available = [
            m for m in members if str(m.get("user_id", "")) not in excluded
        ]
        if not available:
            yield event.plain_result("群里没有可以抽取的成员哦~")
            return

        results = []
        for identity in to_draw:
            target = self._pick_member(available)
            target_id = target.get("user_id")
            target_name = (
                target.get("card")
                or target.get("nickname")
                or f"用户{target.get('user_id')}"
            )
            self._add_record(
                group_id, user_id, str(target_id), target_name, False, kind=identity
            )
            results.append(
                {"label": identity, "id": target_id, "name": target_name}
            )

        header = "为你一次性抽取的今日身份如下：\n"
        chain = [Comp.At(qq=user_id), Comp.Plain(header)]

        for r in results:
            avatar_url = (
                f"https://q4.qlogo.cn/headimg_dl?dst_uin={r['id']}&spec=100"
            )
            chain.append(Comp.Image.fromURL(avatar_url))
            chain.append(Comp.Plain(f" {r['label']}: {r['name']}\n"))

        # 展示之前已拥有的身份
        if drawn_records:
            chain.append(Comp.Plain("\n你已拥有的身份：\n"))
            for identity in identities:
                if identity in drawn_records:
                    r = drawn_records[identity]
                    chain.append(Comp.Plain(f"✅ {identity}: {r['subject_name']}\n"))

        yield event.chain_result(chain)

    # ---------- 今日身份概览 ----------

    @filter.command("我的身份")
    async def show_today_identities(self, event: AstrMessageEvent):
        """显示你今天抽到的所有身份状态。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        if self._is_new_day():
            self._reset_daily_records()

        identities = self.config.get("identities", [])
        if not identities:
            yield event.plain_result("暂无配置身份。")
            return

        self._cursor.execute(
            "SELECT * FROM draw_records "
            "WHERE group_id=? AND user_id=? "
            "AND date(created_at)=date('now','localtime') "
            "ORDER BY created_at",
            (group_id, user_id),
        )
        drawn = {row["kind"]: row for row in self._cursor.fetchall()}

        parts = ["你今天的身份状态："]
        for identity in identities:
            if identity in drawn:
                r = drawn[identity]
                parts.append(f"✅ {identity}: {r['subject_name']}")
            else:
                parts.append(f"⬜ {identity}: 未抽取")

        yield event.plain_result("\n".join(parts))

    # ---------- 今日身份榜单 ----------

    @filter.regex(r"^今日身份榜单(.*)$")
    async def show_leaderboard(self, event: AstrMessageEvent, match):
        """显示今日指定身份的抽取关系榜。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        target = match.group(1).strip()
        if not target:
            identities = self.config.get("identities", [])
            hint = "、".join(identities) if identities else "（暂无配置身份）"
            yield event.plain_result(
                f"请在指令后加上身份名称，如：今日身份榜单 老婆\n"
                f"当前可查：{hint}"
            )
            return

        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        if self._is_new_day():
            self._reset_daily_records()

        identities = self.config.get("identities", [])
        if target not in identities:
            yield event.plain_result(f"「{target}」不在已配置的身份列表中。")
            return

        # 查询今日该身份的所有抽取记录
        self._cursor.execute(
            "SELECT user_id, subject_id, subject_name FROM draw_records "
            "WHERE group_id=? AND kind=? "
            "AND date(created_at)=date('now','localtime') "
            "ORDER BY created_at",
            (group_id, target),
        )
        rows = self._cursor.fetchall()

        if not rows:
            yield event.plain_result(f"🏆 {target} 榜单\n\n今日暂无记录")
            return

        # 构建昵称映射
        user_names = {}
        for row in rows:
            user_names[row["subject_id"]] = row["subject_name"]

        # 补充发起者昵称
        drawer_ids = list({row["user_id"] for row in rows})
        for uid in drawer_ids:
            if uid not in user_names:
                self._cursor.execute(
                    "SELECT subject_name FROM draw_records "
                    "WHERE group_id=? AND user_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (group_id, uid),
                )
                r = self._cursor.fetchone()
                if r:
                    user_names[uid] = r["subject_name"]

        # 按 subject 分组：{被抽中者: [抽到TA的人, ...]}
        subjects = defaultdict(list)
        for row in rows:
            subject_name = user_names.get(row["subject_id"], row["subject_id"])
            drawer_name = user_names.get(row["user_id"], row["user_id"])
            subjects[subject_name].append(drawer_name)

        # 按被抽中次数排序
        sorted_subjects = sorted(subjects.items(), key=lambda x: -len(x[1]))

        lines = [f"🏆 {target} 榜单", ""]
        for subject, drawers in sorted_subjects:
            names = "、".join(drawers)
            lines.append(f"{names} 的{target}是 {subject}")

        yield event.plain_result("\n".join(lines))

    # ---------- 管理 ----------

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置记录")
    async def reset_records(self, event: AstrMessageEvent):
        """管理员命令：重置今日所有抽取记录。"""
        self._cursor.execute(
            "DELETE FROM draw_records WHERE date(created_at)=date('now','localtime')"
        )
        self._conn.commit()
        self._reset_daily_records()
        yield event.plain_result("今日抽取记录已重置！")

    @filter.command("今日身份帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示插件帮助。"""
        identities = self.config.get("identities", [])
        active_days = self.config.get("active_days", 0)
        active_status = f"已启用（{active_days} 天，90% 优先抽活跃成员）" if active_days > 0 else "已关闭"
        excluded_count = len(self.config.get("excluded_users", []))

        identity_cmds = "\n".join(
            f"    • 今日{iden} / 抽{iden} - 抽取{iden}（不带@）\n"
            f"    • 今日{iden}@ / 抽{iden}@ - 抽取{iden}并 @ 对方"
            for iden in identities
        )

        help_text = f"""=== 随机抽身份 帮助 v1.6.2 ===

🎯 已配置身份（{len(identities)} 个）：
{identity_cmds if identity_cmds else "    （暂无配置，请在插件面板中添加身份角色）"}

📋 其他指令：
    • 我的身份 - 查看你今天所有身份的抽取状态
    • 来随机吧 - 一次性抽取所有尚未抽取的身份
    • 今日身份榜单 XX - 查看指定身份今日抽取关系
    • 重置记录 - 管理员专用，重置今日记录

📝 使用说明：
    • 每人每天每个身份最多抽取 1 次
    • 结果会附带被抽中成员的头像
    • 自动排除 Bot、发起者本人以及配置中指定的排除用户
    • 每日 0 点自动重置

⚙️ 当前配置：
    • 身份列表：{'、'.join(identities) if identities else '（空）'}
    • 排除用户：{excluded_count} 个
"""
        yield event.plain_result(help_text)

    # ---------- 清理 ----------

    async def terminate(self):
        try:
            self._conn.close()
            logger.info("随机抽身份插件资源已清理完毕")
        except Exception as e:
            logger.error(f"插件终止时出现错误: {e}")
