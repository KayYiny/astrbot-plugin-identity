import os
import sqlite3
import random
from datetime import datetime
from typing import List, Dict, Any
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

class RandomIdentityPlugin(Star):
    """
    AstrBot 随机抽身份插件
    功能：
    - 随机抽取群友作为不同身份（狗狗/主人/老婆/老公/爸爸），可排除Bot与白名单用户
    - 支持为不同身份分别设置每日上限（向后兼容 `daily_limit`）
    - 持久化保存抽取记录到 SQLite 数据库
    - 支持带@与不带@两种模式
    - 查看历史记录与合并的今日身份展示
    """
    def __init__(self, context: Context, config: AstrBotConfig): 
        """
        插件初始化方法
        """
        super().__init__(context)
        self.config = config # 保存从框架传入的配置对象，用于后续读取用户配置

        self.data_dir = os.path.join("data", "plugins", "random_identity")
        self.db_path = os.path.join(self.data_dir, "identity_records.db")

        os.makedirs(self.data_dir, exist_ok=True)
        self._init_db()
        logger.info("随机抽身份插件已加载 (SQLite)")

    # 初始化 SQLite 数据库
    def _init_db(self):
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS draw_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    subject_name TEXT NOT NULL,
                    with_at INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'dog',
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_records_lookup ON draw_records(group_id, user_id, kind, created_at)")
            self._cursor = self._conn.cursor()
            self._conn.commit()
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")
            raise

    # 检查是否是新的一天
    def _is_new_day(self) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        self._cursor.execute("SELECT value FROM meta WHERE key='current_date'")
        row = self._cursor.fetchone()
        stored = row[0] if row else ""
        return stored != today

    # 重置每日记录：更新日期标记（旧记录保留在数据库中但不影响当日查询）
    def _reset_daily_records(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self._cursor.execute("REPLACE INTO meta (key, value) VALUES ('current_date', ?)", (today,))
        self._conn.commit()
        logger.info("每日记录已更新")
    # 获取群成员列表(仅aiocqhttp平台)
    async def _get_group_members(self, event: AstrMessageEvent) -> List[Dict[str, Any]]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                logger.warning("无法获取群组ID")
                return []
            
            if event.get_platform_name() == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                client = event.bot
                payloads = {"group_id": group_id, "no_cache": True}
                return await client.api.call_action('get_group_member_list', **payloads)
            else:
                logger.warning(f"不支持的平台: {event.get_platform_name()}")
                return []
        except Exception as e: # 捕获所有可能的异常（不会分开分析）
            logger.error(f"获取群成员失败: {e}")
            return []

    # 获取用户今日已抽取次数（基于 SQLite 日期查询）
    def _get_today_count(self, group_id: str, user_id: str, kind: str = 'dog') -> int:
        if self._is_new_day():
            self._reset_daily_records()
        self._cursor.execute(
            "SELECT COUNT(*) FROM draw_records WHERE group_id=? AND user_id=? AND kind=? AND date(created_at)=date('now','localtime')",
            (group_id, user_id, kind)
        )
        return self._cursor.fetchone()[0]

    # 添加抽取历史记录（写入 SQLite，由数据库保证原子性）
    def _add_record(self, group_id: str, user_id: str, subject_id: str, subject_name: str, with_at: bool, kind: str = 'dog'):
        if self._is_new_day():
            self._reset_daily_records()
        self._cursor.execute(
            "INSERT INTO draw_records (group_id, user_id, subject_id, subject_name, with_at, kind) VALUES (?, ?, ?, ?, ?, ?)",
            (group_id, user_id, subject_id, subject_name, 1 if with_at else 0, kind)
        )
        self._conn.commit()
        logger.info(f"用户{user_id}在群{group_id}抽取了{subject_name}({subject_id}) 类型={kind}")
    
    @filter.command("今日狗狗@", alias={'抽狗狗@'})
    async def draw_dog_with_at(self, event: AstrMessageEvent):
        """抽狗狗（带@），别名：抽狗狗@。会 @ 被抽中的成员并附带头像。"""
        async for r in self._draw_identity_common(event, kind='dog', with_at=True):
            yield r

    @filter.command("今日狗狗", alias={'抽狗狗'})
    async def draw_dog_without_at(self, event: AstrMessageEvent):
        """抽狗狗（不带@），别名：抽狗狗。只显示昵称并附带头像。"""
        async for r in self._draw_identity_common(event, kind='dog', with_at=False):
            yield r

    @filter.command("今日主人@", alias={'抽主人@'})
    async def draw_owner_with_at(self, event: AstrMessageEvent):
        """抽主人（带@），别名：抽主人@。会 @ 被抽中的成员并附带头像。"""
        async for r in self._draw_identity_common(event, kind='owner', with_at=True):
            yield r

    @filter.command("今日主人", alias={'抽主人'})
    async def draw_owner_without_at(self, event: AstrMessageEvent):
        """抽主人（不带@），别名：抽主人。只显示昵称并附带头像。"""
        async for r in self._draw_identity_common(event, kind='owner', with_at=False):
            yield r

    @filter.command("今日老婆@", alias={'抽老婆@'})
    async def draw_wife_with_at(self, event: AstrMessageEvent):
        """抽老婆（带@），别名：抽老婆@。会 @ 被抽中的成员并附带头像。"""
        async for r in self._draw_identity_common(event, kind='wife', with_at=True):
            yield r

    @filter.command("今日老婆", alias={'抽老婆'})
    async def draw_wife_without_at(self, event: AstrMessageEvent):
        """抽老婆（不带@），别名：抽老婆。只显示昵称并附带头像。"""
        async for r in self._draw_identity_common(event, kind='wife', with_at=False):
            yield r

    @filter.command("今日老公@", alias={'抽老公@'})
    async def draw_husband_with_at(self, event: AstrMessageEvent):
        """抽老公（带@），别名：抽老公@。会 @ 被抽中的成员并附带头像。"""
        async for r in self._draw_identity_common(event, kind='husband', with_at=True):
            yield r

    @filter.command("今日老公", alias={'抽老公'})
    async def draw_husband_without_at(self, event: AstrMessageEvent):
        """抽老公（不带@），别名：抽老公。只显示昵称并附带头像。"""
        async for r in self._draw_identity_common(event, kind='husband', with_at=False):
            yield r

    @filter.command("今日爸爸@", alias={'抽爸爸@'})
    async def draw_father_with_at(self, event: AstrMessageEvent):
        """抽爸爸（带@），别名：抽爸爸@。会 @ 被抽中的成员并附带头像。"""
        async for r in self._draw_identity_common(event, kind='father', with_at=True):
            yield r

    @filter.command("今日爸爸", alias={'抽爸爸'})
    async def draw_father_without_at(self, event: AstrMessageEvent):
        """抽爸爸（不带@），别名：抽爸爸。只显示昵称并附带头像。"""
        async for r in self._draw_identity_common(event, kind='father', with_at=False):
            yield r

    async def _draw_identity_common(self, event: AstrMessageEvent, kind: str = 'dog', with_at: bool = True):
        if event.is_private_chat():
            yield event.plain_result("该功能仅在群聊中可用哦~")
            return

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        bot_id = event.get_self_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return

        cfg_key = f"daily_limit_{kind}"
        daily_limit = self.config.get(cfg_key, self.config.get("daily_limit", 3))
        today_count = self._get_today_count(group_id, user_id, kind=kind)
        if today_count >= daily_limit:
            yield event.plain_result(f"你今天已经抽了{today_count}次{kind}了，明天再来吧！")
            return

        members = await self._get_group_members(event)
        if not members:
            yield event.plain_result("暂时无法获取群成员列表，请确保Bot有相应权限")
            return

        excluded = {str(uid) for uid in self.config.get("excluded_users", [])}
        excluded.add(str(bot_id))
        excluded.add(str(user_id))

        available_members = [m for m in members if str(m.get("user_id", "")) not in excluded]
        if not available_members:
            yield event.plain_result("群里没有可以抽取的成员哦~")
            return

        target = random.choice(available_members)
        target_id = target.get("user_id")
        target_name = target.get("card") or target.get("nickname") or f"用户{target.get('user_id')}"

        self._add_record(group_id, user_id, str(target_id), target_name, with_at, kind=kind)

        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={target_id}&spec=100"
        remaining = daily_limit - today_count - 1

        labels = {
            'dog': '狗狗', 'owner': '主人', 'wife': '老婆', 'husband': '老公', 'father': '爸爸'
        }
        label = labels.get(kind, kind)

        text_content = f"  你的今日{label}是：\n"
        if with_at:
            info_text = f"\u200b"
        else:
            info_text = f"\n{target_name}"

        remaining_text = f"\r剩余抽取次数：{remaining}次"

        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(text_content),
            Comp.Image.fromURL(avatar_url),
        ]

        if with_at:
            chain.append(Comp.At(qq=target_id))
            chain.append(Comp.Plain(info_text + remaining_text))
        else:
            chain.append(Comp.Plain(info_text + remaining_text))

        yield event.chain_result(chain)

    # 通用历史查看方法，供各身份历史命令调用
    async def _show_history(self, event: AstrMessageEvent, kind: str, label: str):
        """查看指定身份的今日抽取记录与剩余次数。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return

        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return

        if self._is_new_day():
            self._reset_daily_records()

        self._cursor.execute(
            "SELECT * FROM draw_records WHERE group_id=? AND user_id=? AND kind=? AND date(created_at)=date('now','localtime') ORDER BY created_at",
            (group_id, user_id, kind)
        )
        user_records = [dict(row) for row in self._cursor.fetchall()]

        if not user_records:
            yield event.plain_result(f"你今天还没有抽过{label}哦~")
            return

        cfg_key = f"daily_limit_{kind}"
        daily_limit = self.config.get(cfg_key, self.config.get("daily_limit", 3))
        result = [f"你今天的{label}记录({len(user_records)}/{daily_limit})："]
        for i, record in enumerate(user_records, 1):
            time_str = datetime.fromisoformat(record["created_at"]).strftime("%H:%M:%S")
            at_status = "(@)" if record.get("with_at") else ""
            result.append(f"{i}. {record.get('subject_name')} ({record.get('subject_id')}) 在 {time_str} {at_status}")

        remaining = daily_limit - len(user_records)
        result.append(f"剩余次数：{remaining}次")
        yield event.plain_result("\n".join(result))

    @filter.command("来随机吧")
    async def roll_all_identities(self, event: AstrMessageEvent):
        """一次性按各身份剩余上限全部抽取并合并发送（默认不 @）。"""
        if event.is_private_chat():
            yield event.plain_result("该功能仅在群聊中可用哦~")
            return

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        bot_id = event.get_self_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return

        kinds = [
            ('dog', '狗狗', 'daily_limit_dog'),
            ('owner', '主人', 'daily_limit_owner'),
            ('wife', '老婆', 'daily_limit_wife'),
            ('husband', '老公', 'daily_limit_husband'),
            ('father', '爸爸', 'daily_limit_father'),
        ]

        # 计算每种身份的剩余可抽取次数
        to_draw = []  # list of tuples (kind, label, count)
        for kind, label, cfg_key in kinds:
            limit = self.config.get(cfg_key, self.config.get('daily_limit', 3))
            today_count = self._get_today_count(group_id, user_id, kind=kind)
            remaining = max(0, limit - today_count)
            if remaining > 0:
                to_draw.append((kind, label, remaining))

        if not to_draw:
            yield event.plain_result("你今天已经抽满了所有身份的次数，明天再来吧~")
            return

        members = await self._get_group_members(event)
        if not members:
            yield event.plain_result("暂时无法获取群成员列表，请确保Bot有相应权限")
            return

        excluded = {str(uid) for uid in self.config.get("excluded_users", [])}
        excluded.add(str(bot_id))
        excluded.add(str(user_id))

        available_members = [m for m in members if str(m.get("user_id", "")) not in excluded]
        if not available_members:
            yield event.plain_result("群里没有可以抽取的成员哦~")
            return

        # 执行抽取
        results = []  # list of dicts: {kind,label,target_id,target_name}
        for kind, label, count in to_draw:
            # 如果成员足够，优先不重复抽取
            if len(available_members) >= count:
                chosen = random.sample(available_members, count)
            else:
                chosen = [random.choice(available_members) for _ in range(count)]

            for target in chosen:
                target_id = target.get('user_id')
                target_name = target.get('card') or target.get('nickname') or f"用户{target.get('user_id')}"
                self._add_record(group_id, user_id, str(target_id), target_name, False, kind=kind)
                results.append({
                    'kind': kind,
                    'label': label,
                    'target_id': target_id,
                    'target_name': target_name,
                })

        # 构建合并消息链（默认不 @，仅附带头像和说明）
        header = "为你一次性抽取的今日身份如下：\n"
        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(header),
        ]

        # 按身份分组显示
        grouped = {}
        for r in results:
            grouped.setdefault(r['label'], []).append(r)

        for label, items in grouped.items():
            # 在每个身份组前添加分隔线
            chain.append(Comp.Plain("------\n"))
            for i, it in enumerate(items, 1):
                avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={it['target_id']}&spec=640"
                chain.append(Comp.Image.fromURL(avatar_url))
                # 若该身份只有一项，则显示为 "身份 名称 (id)"，多项时使用编号
                if len(items) == 1:
                    chain.append(Comp.Plain(f"{label} {it['target_name']} ({it['target_id']})\n"))
                else:
                    chain.append(Comp.Plain(f"{label}{i}. {it['target_name']} ({it['target_id']})\n"))

        yield event.chain_result(chain)
    
    @filter.command("我的狗狗", alias={'抽取历史'})
    async def show_my_dogs(self, event: AstrMessageEvent):
        """查看你今天抽到的狗狗记录与剩余次数（按时间排序）。"""
        async for r in self._show_history(event, kind='dog', label='狗狗'):
            yield r

    @filter.command("我的老婆", alias={'老婆历史'})
    async def show_my_wives(self, event: AstrMessageEvent):
        """查看你今天抽到的老婆记录与剩余次数（按时间排序）。"""
        async for r in self._show_history(event, kind='wife', label='老婆'):
            yield r

    @filter.command("我的老公", alias={'老公历史'})
    async def show_my_husbands(self, event: AstrMessageEvent):
        """查看你今天抽到的老公记录与剩余次数（按时间排序）。"""
        async for r in self._show_history(event, kind='husband', label='老公'):
            yield r

    @filter.command("我的爸爸", alias={'爸爸历史'})
    async def show_my_fathers(self, event: AstrMessageEvent):
        """查看你今天抽到的爸爸记录与剩余次数（按时间排序）。"""
        async for r in self._show_history(event, kind='father', label='爸爸'):
            yield r

    @filter.command("我的身份")
    async def show_today_identities(self, event: AstrMessageEvent):
        """显示你今天抽到的所有身份（仅列出已抽取的身份及对应记录）。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        if self._is_new_day():
            self._reset_daily_records()

        self._cursor.execute(
            "SELECT * FROM draw_records WHERE group_id=? AND user_id=? AND date(created_at)=date('now','localtime') ORDER BY kind, created_at",
            (group_id, user_id)
        )
        all_records = [dict(row) for row in self._cursor.fetchall()]

        kinds = [('dog', '狗狗', self.config.get('daily_limit_dog', self.config.get('daily_limit', 3))),
                 ('owner', '主人', self.config.get('daily_limit_owner', self.config.get('daily_limit', 3))),
                 ('wife', '老婆', self.config.get('daily_limit_wife', self.config.get('daily_limit', 3))),
                 ('husband', '老公', self.config.get('daily_limit_husband', self.config.get('daily_limit', 3))),
                 ('father', '爸爸', self.config.get('daily_limit_father', self.config.get('daily_limit', 3)))]

        parts = []
        for kind, label, limit in kinds:
            records = [r for r in all_records if r['kind'] == kind]
            if not records:
                continue
            parts.append(f"{label} ({len(records)}/{limit})：")
            for i, record in enumerate(records, 1):
                time_str = datetime.fromisoformat(record['created_at']).strftime('%H:%M:%S')
                at_status = '(@)' if record.get('with_at') else ''
                parts.append(f"{i}. {record.get('subject_name')} ({record.get('subject_id')}) 在 {time_str} {at_status}")
            parts.append("")

        if not parts:
            yield event.plain_result("你今天还没有抽过任何身份哦~")
            return
        yield event.plain_result('\n'.join(parts))

    @filter.command("我的主人", alias={'主人历史'})
    async def show_my_owners(self, event: AstrMessageEvent):
        """查看你今天抽到的主人记录与剩余次数（按时间排序）。"""
        async for r in self._show_history(event, kind='owner', label='主人'):
            yield r
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置记录")
    async def reset_records(self, event: AstrMessageEvent):
        """管理员命令：重置今日所有群聊的抽取记录（请谨慎使用）。"""
        self._cursor.execute("DELETE FROM draw_records WHERE date(created_at)=date('now','localtime')")
        self._conn.commit()
        self._reset_daily_records()
        yield event.plain_result("今日抽取记录已重置！")

    @filter.command("今日身份帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示插件帮助（命令说明与当前配置）。"""
        dog_limit = self.config.get("daily_limit_dog", self.config.get("daily_limit", 3))
        owner_limit = self.config.get("daily_limit_owner", self.config.get("daily_limit", 3))
        wife_limit = self.config.get("daily_limit_wife", self.config.get("daily_limit", 3))
        husband_limit = self.config.get("daily_limit_husband", self.config.get("daily_limit", 3))
        father_limit = self.config.get("daily_limit_father", self.config.get("daily_limit", 3))
        excluded_count = len(self.config.get("excluded_users", []))
        help_text = f"""=== 抽身份 插件 帮助 v2.2.0 ===
        
    🎯 主要功能：
    • 今日身份 - 列出你今天抽到的所有身份（狗狗/主人/老婆/老公/爸爸）
    • 今日狗狗 / 抽狗狗 - 随机抽取一位群友作为今日狗狗（不带@）
    • 今日狗狗@ / 抽狗狗@ - 随机抽取并 @ 被选中的成员
    • 今日主人 / 抽主人 - 随机抽取一位群友作为今日主人（不带@）
    • 今日主人@ / 抽主人@ - 随机抽取并 @ 被选中的成员
    • 今日老婆 / 抽老婆 - 抽取老婆（不带@）
    • 今日老婆@ / 抽老婆@ - 抽取老婆并 @ 对方
    • 今日老公 / 抽老公 - 抽取老公（不带@）
    • 今日老公@ / 抽老公@ - 抽取老公并 @ 对方
    • 今日爸爸 / 抽爸爸 - 抽取爸爸（不带@）
    • 今日爸爸@ / 抽爸爸@ - 抽取爸爸并 @ 对方
    • 我的狗狗 / 我的主人 / 我的老婆 / 我的老公 / 我的爸爸 - 查看各自的今日记录
    • 来随机吧 - 新增命令：一次性按各身份的剩余每日上限全部抽取并合并发送（默认不 @，会附带头像）。
    • 重置记录 - 管理员专用，重置今日记录

    📝 使用说明：
    • 每人每日可分别抽取：狗狗 {dog_limit} 次，主人 {owner_limit} 次，老婆 {wife_limit} 次，老公 {husband_limit} 次，爸爸 {father_limit} 次
    • 结果会附带被抽中成员的头像
    • 自动排除Bot和发起者本人，以及配置中指定的排除用户
    • 每日0点自动重置记录（第一次触发时）

    ⚙️ 当前配置：
    • 每日限制：狗狗 {dog_limit} 次，主人 {owner_limit} 次，老婆 {wife_limit} 次，老公 {husband_limit} 次，爸爸 {father_limit} 次
    • 排除用户：{excluded_count} 个
    """
        yield event.plain_result(help_text)
    
    async def terminate(self):
        try:
            self._conn.close()
            logger.info("随机抽身份插件资源已清理完毕")
        except Exception as e:
            logger.error(f"插件终止时出现错误: {e}")
