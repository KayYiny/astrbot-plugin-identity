import os
import json
import random
from datetime import datetime
from typing import List, Dict, Any
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent 
import astrbot.api.message_components as Comp

class RandomIdentityPlugin(Star):
    """
    AstrBot 随机抽身份插件
    功能：
    - 随机抽取群友作为不同身份（狗狗/主人/老婆/老公/爸爸），可排除Bot与白名单用户
    - 支持为不同身份分别设置每日上限（向后兼容 `daily_limit`）
    - 持久化保存抽取记录到JSON文件
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
        self.records_file = os.path.join(self.data_dir, "identity_records.json")
        
        os.makedirs(self.data_dir, exist_ok=True)
        self.records = self._load_records()
        logger.info("随机抽身份插件已加载")

    # 从文件加载记录
    def _load_records(self) -> Dict[str, Any]: 
        try: 
            if os.path.exists(self.records_file):
                with open(self.records_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {"date": "", "groups": {}}
        except Exception as e:
            logger.error(f"加载记录文件失败: {e}")
            return {"date": "", "groups": {}}
    # 用于保存记录到文件
    def _save_records(self):
        try:
            with open(self.records_file, 'w', encoding='utf-8') as f:
                json.dump(self.records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存记录文件失败: {e}")

    # 检查是否是新的一天
    def _is_new_day(self) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.records.get("date") != today 
    # 重置每日记录
    def _reset_daily_records(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.records = {"date": today, "groups": {}}
        self._save_records()
        logger.info("每日抽取记录已重置")
    # 获取群成员列表(仅aiocqhttp平台)
    async def _get_group_members(self, event: AstrMessageEvent) -> List[Dict[str, Any]]:
        try:
            group_id = event.get_group_id()
            if not group_id:
                logger.warning("无法获取群组ID")
                return []
            
            if event.get_platform_name() == "aiocqhttp":
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

    # 获取用户今日已抽取次数，若过12点就重置
    def _get_today_count(self, group_id: str, user_id: str, kind: str = 'dog') -> int:
        if self._is_new_day():
            self._reset_daily_records()
            return 0

        group_records = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        return sum(1 for record in group_records if record.get("user_id") == user_id and record.get("type") == kind)

    # 添加抽取历史记录
    def _add_record(self, group_id: str, user_id: str, subject_id: str, subject_name: str, with_at: bool, kind: str = 'dog'):
        if self._is_new_day():
            self._reset_daily_records()
        if group_id not in self.records["groups"]:
            self.records["groups"][group_id] = {"records": []}

        record = {
            "user_id": user_id,
            "subject_id": subject_id,
            "subject_name": subject_name,
            "timestamp": datetime.now().isoformat(),
            "with_at": with_at,
            "type": kind,
        }
        self.records["groups"][group_id]["records"].append(record)
        self._save_records()
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

        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={target_id}&spec=640"
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
    
    @filter.command("我的狗狗", alias={'抽取历史'})
    async def show_my_dogs(self, event: AstrMessageEvent):
        """查看你今天抽到的狗狗记录与剩余次数（按时间排序）。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return # 结束
        
        user_id, group_id = event.get_sender_id(), event.get_group_id() 
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        
        if self._is_new_day():
            self._reset_daily_records()
        
        group_records = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        user_records = [r for r in group_records if r.get("user_id") == user_id and r.get("type") == 'dog']
        
        if not user_records:
            yield event.plain_result("你今天还没有抽过狗狗哦~")
            return
        
        daily_limit = self.config.get("daily_limit_dog", self.config.get("daily_limit", 3))
        result = [f"你今天的狗狗记录({len(user_records)}/{daily_limit})："]
        for i, record in enumerate(user_records, 1):
            time_str = datetime.fromisoformat(record["timestamp"]).strftime("%H:%M:%S")
            at_status = "(@)" if record.get("with_at", False) else ""
            result.append(f"{i}. {record.get('subject_name')} ({record.get('subject_id')}) 在 {time_str} {at_status}")
        
        remaining = daily_limit - len(user_records)
        result.append(f"剩余次数：{remaining}次")
        yield event.plain_result("\n".join(result))

    @filter.command("我的老婆", alias={'老婆历史'})
    async def show_my_wifes(self, event: AstrMessageEvent):
        """查看你今天抽到的老婆记录与剩余次数（按时间排序）。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        if self._is_new_day():
            self._reset_daily_records()
        group_records = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        user_records = [r for r in group_records if r.get("user_id") == user_id and r.get("type") == 'wife']
        if not user_records:
            yield event.plain_result("你今天还没有抽过老婆哦~")
            return
        daily_limit = self.config.get("daily_limit_wife", self.config.get("daily_limit", 3))
        result = [f"你今天的老婆记录({len(user_records)}/{daily_limit})："]
        for i, record in enumerate(user_records, 1):
            time_str = datetime.fromisoformat(record["timestamp"]).strftime("%H:%M:%S")
            at_status = "(@)" if record.get("with_at", False) else ""
            result.append(f"{i}. {record.get('subject_name')} ({record.get('subject_id')}) 在 {time_str} {at_status}")
        remaining = daily_limit - len(user_records)
        result.append(f"剩余次数：{remaining}次")
        yield event.plain_result("\n".join(result))

    @filter.command("我的老公", alias={'老公历史'})
    async def show_my_husbands(self, event: AstrMessageEvent):
        """查看你今天抽到的老公记录与剩余次数（按时间排序）。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        if self._is_new_day():
            self._reset_daily_records()
        group_records = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        user_records = [r for r in group_records if r.get("user_id") == user_id and r.get("type") == 'husband']
        if not user_records:
            yield event.plain_result("你今天还没有抽过老公哦~")
            return
        daily_limit = self.config.get("daily_limit_husband", self.config.get("daily_limit", 3))
        result = [f"你今天的老公记录({len(user_records)}/{daily_limit})："]
        for i, record in enumerate(user_records, 1):
            time_str = datetime.fromisoformat(record["timestamp"]).strftime("%H:%M:%S")
            at_status = "(@)" if record.get("with_at", False) else ""
            result.append(f"{i}. {record.get('subject_name')} ({record.get('subject_id')}) 在 {time_str} {at_status}")
        remaining = daily_limit - len(user_records)
        result.append(f"剩余次数：{remaining}次")
        yield event.plain_result("\n".join(result))

    @filter.command("我的爸爸", alias={'爸爸历史'})
    async def show_my_fathers(self, event: AstrMessageEvent):
        """查看你今天抽到的爸爸记录与剩余次数（按时间排序）。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return
        user_id, group_id = event.get_sender_id(), event.get_group_id()
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        if self._is_new_day():
            self._reset_daily_records()
        group_records = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        user_records = [r for r in group_records if r.get("user_id") == user_id and r.get("type") == 'father']
        if not user_records:
            yield event.plain_result("你今天还没有抽过爸爸哦~")
            return
        daily_limit = self.config.get("daily_limit_father", self.config.get("daily_limit", 3))
        result = [f"你今天的爸爸记录({len(user_records)}/{daily_limit})："]
        for i, record in enumerate(user_records, 1):
            time_str = datetime.fromisoformat(record["timestamp"]).strftime("%H:%M:%S")
            at_status = "(@)" if record.get("with_at", False) else ""
            result.append(f"{i}. {record.get('subject_name')} ({record.get('subject_id')}) 在 {time_str} {at_status}")
        remaining = daily_limit - len(user_records)
        result.append(f"剩余次数：{remaining}次")
        yield event.plain_result("\n".join(result))

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
        group_records = self.records.get("groups", {}).get(group_id, {}).get("records", [])

        kinds = [('dog', '狗狗', self.config.get('daily_limit_dog', self.config.get('daily_limit', 3))),
                 ('owner', '主人', self.config.get('daily_limit_owner', self.config.get('daily_limit', 3))),
                 ('wife', '老婆', self.config.get('daily_limit_wife', self.config.get('daily_limit', 3))),
                 ('husband', '老公', self.config.get('daily_limit_husband', self.config.get('daily_limit', 3))),
                 ('father', '爸爸', self.config.get('daily_limit_father', self.config.get('daily_limit', 3)))]

        parts = []
        for kind, label, limit in kinds:
            records = [r for r in group_records if r.get('user_id') == user_id and r.get('type') == kind]
            if not records:
                continue
            parts.append(f"{label} ({len(records)}/{limit})：")
            for i, record in enumerate(records, 1):
                time_str = datetime.fromisoformat(record['timestamp']).strftime('%H:%M:%S')
                at_status = '(@)' if record.get('with_at', False) else ''
                parts.append(f"{i}. {record.get('subject_name')} ({record.get('subject_id')}) 在 {time_str} {at_status}")
            parts.append("")

        if not parts:
            yield event.plain_result("你今天还没有抽过任何身份哦~")
            return
        yield event.plain_result('\n'.join(parts))

    @filter.command("我的主人", alias={'主人历史'})
    async def show_my_owners(self, event: AstrMessageEvent):
        """查看你今天抽到的主人记录与剩余次数（按时间排序）。"""
        if event.is_private_chat():
            yield event.plain_result("此功能仅在群聊中可用哦~")
            return
        
        user_id, group_id = event.get_sender_id(), event.get_group_id() 
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        
        if self._is_new_day():
            self._reset_daily_records()
        
        group_records = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        user_records = [r for r in group_records if r.get("user_id") == user_id and r.get("type") == 'owner']
        
        if not user_records:
            yield event.plain_result("你今天还没有抽过主人哦~")
            return
        
        daily_limit = self.config.get("daily_limit_owner", self.config.get("daily_limit", 3))
        result = [f"你今天的主人记录({len(user_records)}/{daily_limit})："]
        for i, record in enumerate(user_records, 1):
            time_str = datetime.fromisoformat(record["timestamp"]).strftime("%H:%M:%S")
            at_status = "(@)" if record.get("with_at", False) else ""
            result.append(f"{i}. {record.get('subject_name')} ({record.get('subject_id')}) 在 {time_str} {at_status}")
        
        remaining = daily_limit - len(user_records)
        result.append(f"剩余次数：{remaining}次")
        yield event.plain_result("\n".join(result))
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置记录")
    async def reset_records(self, event: AstrMessageEvent):
        """管理员命令：重置今日所有群聊的抽取记录（请谨慎使用）。"""
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
        help_text = f"""=== 抽身份 插件 帮助 v2.0.9 ===
        
🎯 主要功能：
• 今日身份 - 列出你今天抽到的所有身份（狗狗/主人/老婆/老公/爸爸）
• 今日狗狗 / 抽狗狗 - 随机抽取群友作为今日狗狗（带@）
• 抽狗狗-@ / 今日狗狗-@ - 不带@
• 今日主人 / 抽主人 - 随机抽取群友作为今日主人（带@）
• 抽主人-@ / 今日主人-@ - 不带@
• 抽老婆 / 抽老婆-@ - 抽取老婆（带/不带@）
• 抽老公 / 抽老公-@ - 抽取老公（带/不带@）
• 抽爸爸 / 抽爸爸-@ - 抽取爸爸（带/不带@）
• 我的狗狗 / 我的主人 / 我的老婆 / 我的老公 / 我的爸爸 - 查看各自的今日记录
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
            self._save_records()
            logger.info("随机抽身份插件资源已清理完毕")
        except Exception as e:
            logger.error(f"插件终止时出现错误: {e}")
