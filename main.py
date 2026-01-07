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

class RandomDogPlugin(Star):
    """
    AstrBot随机抽狗狗插件
    功能：
    1. 随机抽取群友作为"狗狗"（排除Bot和指定用户）
    2. 支持每日抽取次数限制（可配置）
    3. 持久化保存抽取记录到JSON文件
    4. 支持@和不@的命令选项
    5. 查看历史记录功能
    6. 管理员重置记录功能
    7. 帮助菜单
    8. 输出被抽中成员的头像
    """
    def __init__(self, context: Context, config: AstrBotConfig): 
        """
        插件初始化方法
        """
        super().__init__(context)
        self.config = config # 保存从框架传入的配置对象，用于后续读取用户配置
        
        self.data_dir = os.path.join("data", "plugins", "random_dog") # 构建数据存储目录路径（/bot目录/data/plugins/random_dog）
        self.records_file = os.path.join(self.data_dir, "dog_records.json")
        
        os.makedirs(self.data_dir, exist_ok=True)
        self.records = self._load_records()
        logger.info("随机抽狗狗插件已加载")

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
    def _get_today_count(self, group_id: str, user_id: str) -> int:
        if self._is_new_day():
            self._reset_daily_records()
            return 0
        
        group_records = self.records.get("groups", {}).get(group_id, {}).get("records", [])
        return sum(1 for record in group_records if record["user_id"] == user_id)

    # 添加抽取历史记录
    def _add_record(self, group_id: str, user_id: str, dog_id: str, dog_name: str, with_at: bool):
        if self._is_new_day():
            self._reset_daily_records()
        if group_id not in self.records["groups"]:
            self.records["groups"][group_id] = {"records": []}
        
        record = {
            "user_id": user_id, "dog_id": dog_id, "dog_name": dog_name,
            "timestamp": datetime.now().isoformat(), "with_at": with_at
        }
        self.records["groups"][group_id]["records"].append(record)
        self._save_records()
        logger.info(f"用户{user_id}在群{group_id}抽取了{dog_name}({dog_id})")
    
    @filter.command("今日狗狗", alias={'抽狗狗'})
    async def draw_dog_with_at(self, event: AstrMessageEvent):
        """抽取今日狗狗（带@功能），别名“抽狗狗”"""
        async for result in self._draw_dog_common(event, with_at=True):
            yield result
    
    @filter.command("抽狗狗-@",alias={'今日狗狗-@'})
    async def draw_dog_without_at(self, event: AstrMessageEvent):
        """抽取今日狗狗（不带@功能），别名“今日狗狗-@”"""
        async for result in self._draw_dog_common(event, with_at=False):
            yield result
    
    # 抽取方法
    async def _draw_dog_common(self, event: AstrMessageEvent, with_at: bool):
        if event.is_private_chat():
            yield event.plain_result("抽狗狗功能仅在群聊中可用哦~")
            return
        
        user_id = event.get_sender_id() # 获取发送者ID
        group_id = event.get_group_id() # 获取群聊ID
        bot_id = event.get_self_id() # 获取机器人自身ID
        
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        
        daily_limit = self.config.get("daily_limit", 3)
        today_count = self._get_today_count(group_id, user_id)
        if today_count >= daily_limit:
            yield event.plain_result(f"你今天已经抽了{today_count}次狗狗了，明天再来吧！")
            return
        
        members = await self._get_group_members(event)
        if not members:
            yield event.plain_result("暂时无法获取群成员列表，请确保Bot有相应权限（可能在获取的时候就被踢了？）")
            return
        
        excluded = {str(uid) for uid in self.config.get("excluded_users", [])}
        excluded.add(str(bot_id))
        excluded.add(str(user_id))
        
        available_members = [m for m in members if str(m.get("user_id", "")) not in excluded]
        if not available_members:
            yield event.plain_result("群里没有可以抽取的成员哦~")
            return
        
        dog = random.choice(available_members)
        dog_id, dog_name = dog.get("user_id"), dog.get("card") or dog.get("nickname") or f"用户{dog.get('user_id')}"
        
        self._add_record(group_id, user_id, str(dog_id), dog_name, with_at) 
        
        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={dog_id}&spec=640"
        remaining = daily_limit - today_count - 1
        
        text_content = f" 你的今日狗狗是：\n"

        if with_at:
            dog_info_text = f"\u200b"
        else:
            dog_info_text = f"\n{dog_name}"

        remaining_text = f"\r剩余抽取次数：{remaining}次"


        chain = [
            Comp.At(qq=user_id),
            Comp.Plain(text_content),
            Comp.Image.fromURL(avatar_url), 
]

        if with_at:
            chain.append(Comp.At(qq=dog_id))
            chain.append(Comp.Plain(dog_info_text + remaining_text))
        else:
            chain.append(Comp.Plain(dog_info_text + remaining_text))

        yield event.chain_result(chain)
    
    @filter.command("我的狗狗", alias={'抽取历史'})
    async def show_my_dogs(self, event: AstrMessageEvent):
        """显示用户的抽取历史（我的狗狗），别名“抽取历史”"""
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
        user_records = [r for r in group_records if r["user_id"] == user_id]
        
        if not user_records:
            yield event.plain_result("你今天还没有抽过狗狗哦~")
            return
        
        daily_limit = self.config.get("daily_limit", 3)
        result = [f"你今天的狗狗记录({len(user_records)}/{daily_limit})："]
        for i, record in enumerate(user_records, 1):
            time_str = datetime.fromisoformat(record["timestamp"]).strftime("%H:%M:%S")
            at_status = "(@)" if record.get("with_at", False) else ""
            result.append(f"{i}. {record['dog_name']} ({record['dog_id']}) 在 {time_str} {at_status}")
        
        remaining = daily_limit - len(user_records)
        result.append(f"剩余次数：{remaining}次")
        yield event.plain_result("\n".join(result))
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置记录")
    async def reset_records(self, event: AstrMessageEvent):
        """仅bot管理员可使用：重置所有群聊的所有记录（慎用！）"""
        self._reset_daily_records()
        yield event.plain_result("今日抽取记录已重置！")

    @filter.command("抽狗狗帮助" , alias={'今日狗狗帮助'})
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助，别名“今日狗狗帮助”"""
        daily_limit = self.config.get("daily_limit", 3)
        excluded_count = len(self.config.get("excluded_users", []))
        help_text = f"""=== 抽狗狗帮助 v2.0.5 ===
        
🎯 主要功能：
• 今日狗狗 / 抽狗狗 - 随机抽取群友作为今日狗狗（带@）
• 抽狗狗-@ / 今日狗狗-@
   - 随机抽取群友（不带@）
• 我的狗狗 / 抽取历史 
   - 查看今天的抽取记录
• 重置记录
   - 管理员专用，重置今日记录
• 抽狗狗帮助 / 今日狗狗帮助
   - 查看该帮助

📝 使用说明：
• 每人每日可抽取 {daily_limit} 次
• 结果会附带被抽中成员的头像
• 自动排除Bot和发起者本人
• 每日0点自动重置记录

⚙️ 当前配置：
• 每日限制：{daily_limit} 次
• 排除用户：{excluded_count} 个
"""
        yield event.plain_result(help_text)
    
    async def terminate(self):
        try:
            self._save_records()
            logger.info("抽狗狗插件资源已清理完毕")
        except Exception as e:
            logger.error(f"插件终止时出现错误: {e}")
