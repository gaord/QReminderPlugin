import asyncio
import json
import os
import traceback
import typing
from datetime import datetime, timedelta
import dateparser
import logging
from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *
import pkg.platform.types as platform_types
from pkg.provider import entities as llm_entities


class ReminderCache:
    """提醒缓存类，参考WaifuCache的设计"""
    
    def __init__(self, ap, launcher_id: str, launcher_type: str):
        self.ap = ap
        self.launcher_id = launcher_id
        self.launcher_type = launcher_type
        self.reminders: typing.Dict[str, typing.Dict] = {}
        self.running_tasks: typing.Dict[str, asyncio.Task] = {}
        self.data_file = f"data/plugins/ReminderPlugin/reminders_{launcher_id}.json"
        self.response_timer_flag = False
        
    async def load_reminders(self):
        """加载提醒数据"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.reminders = json.load(f)
                self.ap.logger.info(f"已加载 {len(self.reminders)} 条提醒记录")
        except Exception as e:
            self.ap.logger.error(f"加载提醒数据失败: {e}")
            self.reminders = {}

    async def save_reminders(self):
        """保存提醒数据"""
        try:
            os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            self.ap.logger.error(f"保存提醒数据失败: {e}")


@register(name="ReminderPlugin", description="智能定时提醒插件，支持自然语言设置提醒", version="1.1", author="Assistant")
class ReminderPlugin(BasePlugin):

    def __init__(self, host: APIHost):
        super().__init__(host)
        self.ap = host.ap
        self.host = host
        self.reminder_cache: typing.Dict[str, ReminderCache] = {}
        self._ensure_required_files_exist()
        
    async def initialize(self):
        """异步初始化"""
        await super().initialize()
        self.ap.logger.info("ReminderPlugin 初始化完成")

    def _ensure_required_files_exist(self):
        """确保必要的目录存在"""
        directories = ["data/plugins/ReminderPlugin"]
        for directory in directories:
            if not os.path.exists(directory):
                os.makedirs(directory)
                self.ap.logger.info(f"创建目录: {directory}")

    async def _access_control_check(self, ctx: EventContext) -> bool:
        """访问控制检查，参考Waifu的设计"""
        text_message = str(ctx.event.query.message_chain)
        launcher_id = ctx.event.launcher_id
        launcher_type = ctx.event.launcher_type
        
        # 检查黑白名单
        mode = self.ap.instance_config.data.get("pipeline", {}).get("access-control", {}).get("mode")
        sess_list = set(self.ap.instance_config.data.get("pipeline", {}).get("access-control", {}).get(mode, []))
        
        found = (launcher_type == "group" and "group_*" in sess_list) or \
                (launcher_type == "person" and "person_*" in sess_list) or \
                f"{launcher_type}_{launcher_id}" in sess_list
        
        if (mode == "whitelist" and not found) or (mode == "blacklist" and found):
            return False
        
        # 排除主项目命令
        cmd_prefix = self.ap.instance_config.data.get("command", {}).get("command-prefix", [])
        if any(text_message.startswith(prefix) for prefix in cmd_prefix):
            return False
            
        return True

    async def _load_cache(self, launcher_id: str, launcher_type: str):
        """加载或创建提醒缓存"""
        if launcher_id not in self.reminder_cache:
            cache = ReminderCache(self.ap, launcher_id, launcher_type)
            await cache.load_reminders()
            self.reminder_cache[launcher_id] = cache
            
            # 恢复运行中的提醒任务
            await self._restore_reminders(cache)

    async def _restore_reminders(self, cache: ReminderCache):
        """恢复运行中的提醒任务"""
        current_time = datetime.now()
        
        for reminder_id, reminder_data in cache.reminders.items():
            if not reminder_data.get('active', True):
                continue
                
            try:
                target_time = datetime.fromisoformat(reminder_data['target_time'])
                if target_time > current_time:
                    await self._schedule_reminder(cache, reminder_id, reminder_data)
                else:
                    # 过期的一次性提醒直接删除
                    if reminder_data.get('repeat_type') == '不重复':
                        cache.reminders.pop(reminder_id, None)
                        self.ap.logger.info(f"删除过期提醒: {reminder_data['content']}")
            except Exception as e:
                self.ap.logger.error(f"恢复提醒任务失败: {e}")

    @llm_func("set_reminder")
    async def set_reminder_llm(self, query, content: str, time_description: str, repeat_type: str = "不重复"):
        """AI函数调用接口：设置提醒
        
        Args:
            content(str): 提醒内容，例如："开会"、"吃药"、"买菜"等
            time_description(str): 时间描述，支持自然语言，例如："30分钟后"、"明天下午3点"、"今晚8点"等
            repeat_type(str): 重复类型，可选值："不重复"、"每天"、"每周"、"每月"
            
        Returns:
            str: 设置结果信息
        """
        try:
            launcher_id = str(query.launcher_id)
            launcher_type = str(query.launcher_type).split(".")[-1].lower()
            
            # 确保缓存已加载
            await self._load_cache(launcher_id, launcher_type)
            cache = self.reminder_cache[launcher_id]
            
            # 解析时间
            target_time = await self._parse_time_natural(time_description)
            if not target_time:
                return f"时间格式无法识别：{time_description}。请使用如'30分钟后'、'明天下午3点'、'今晚8点'等格式"

            # 检查时间是否已过
            if target_time <= datetime.now():
                return "设置的时间已经过去了，请重新设置！"

            # 生成提醒ID
            reminder_id = f"{launcher_id}_{int(datetime.now().timestamp())}"
            
            # 创建提醒数据
            reminder_data = {
                'id': reminder_id,
                'launcher_id': launcher_id,
                'launcher_type': launcher_type,
                'sender_id': str(query.sender_id),
                'content': content,
                'target_time': target_time.isoformat(),
                'repeat_type': repeat_type,
                'active': True,
                'created_at': datetime.now().isoformat()
            }

            # 保存提醒
            cache.reminders[reminder_id] = reminder_data
            await cache.save_reminders()

            # 安排提醒任务
            await self._schedule_reminder(cache, reminder_id, reminder_data)

            # 返回确认信息
            time_str_formatted = target_time.strftime("%Y-%m-%d %H:%M")
            repeat_info = f"，重复类型：{repeat_type}" if repeat_type != "不重复" else ""
            return f"✅ 提醒设置成功！\n时间：{time_str_formatted}\n内容：{content}{repeat_info}"

        except Exception as e:
            self.ap.logger.error(f"设置提醒失败: {e}")
            return f"设置提醒失败：{str(e)}"

    async def _parse_time_natural(self, time_str: str) -> datetime:
        """使用dateparser解析自然语言时间"""
        try:
            # 使用dateparser解析自然语言时间
            parsed_time = dateparser.parse(time_str, languages=['zh', 'en'])
            if parsed_time:
                return parsed_time
            
            # 如果dateparser失败，尝试手动解析一些常见格式
            return await self._parse_time_manual(time_str)
            
        except Exception as e:
            self.ap.logger.error(f"解析时间失败: {e}")
            return None

    async def _parse_time_manual(self, time_str: str) -> datetime:
        """手动解析时间字符串"""
        now = datetime.now()
        
        # 相对时间解析
        if "后" in time_str:
            time_str = time_str.replace("后", "")
            if "分钟" in time_str:
                minutes = int(''.join(filter(str.isdigit, time_str)))
                return now + timedelta(minutes=minutes)
            elif "小时" in time_str:
                hours = int(''.join(filter(str.isdigit, time_str)))
                return now + timedelta(hours=hours)
            elif "天" in time_str:
                days = int(''.join(filter(str.isdigit, time_str)))
                return now + timedelta(days=days)
        
        # 绝对时间解析
        try:
            # 完整日期时间格式
            if " " in time_str and ":" in time_str:
                return datetime.strptime(time_str, "%Y-%m-%d %H:%M")
            
            # 只有时间，默认为今天
            elif ":" in time_str:
                time_part = datetime.strptime(time_str, "%H:%M").time()
                target = datetime.combine(now.date(), time_part)
                # 如果时间已过，设为明天
                if target <= now:
                    target = target + timedelta(days=1)
                return target
                
        except ValueError:
            pass
        
        return None

    async def _schedule_reminder(self, cache: ReminderCache, reminder_id: str, reminder_data: typing.Dict):
        """安排提醒任务"""
        try:
            target_time = datetime.fromisoformat(reminder_data['target_time'])
            delay = (target_time - datetime.now()).total_seconds()
            
            if delay > 0:
                # 创建异步任务
                task = asyncio.create_task(self._reminder_task(cache, reminder_id, delay))
                cache.running_tasks[reminder_id] = task
                self.ap.logger.info(f"安排提醒任务 {reminder_id}，延迟 {delay:.0f} 秒")
                
        except Exception as e:
            self.ap.logger.error(f"安排提醒任务失败: {e}")

    async def _reminder_task(self, cache: ReminderCache, reminder_id: str, delay: float):
        """提醒任务执行"""
        try:
            await asyncio.sleep(delay)
            
            # 检查提醒是否仍然存在且活跃
            if reminder_id in cache.reminders and cache.reminders[reminder_id].get('active', True):
                reminder_data = cache.reminders[reminder_id]
                
                # 发送提醒消息
                await self._send_reminder_message(reminder_data)
                
                # 处理重复提醒
                await self._handle_repeat_reminder(cache, reminder_id, reminder_data)
                
        except asyncio.CancelledError:
            self.ap.logger.debug(f"提醒任务 {reminder_id} 被取消")
        except Exception as e:
            self.ap.logger.error(f"提醒任务执行失败: {e}")
        finally:
            # 清理任务引用
            cache.running_tasks.pop(reminder_id, None)

    async def _send_reminder_message(self, reminder_data: typing.Dict):
        """发送提醒消息"""
        try:
            message_content = f"⏰ 提醒：{reminder_data['content']}"
            
            # 构建消息链
            message_chain = platform_types.MessageChain([
                platform_types.At(reminder_data['sender_id']),
                platform_types.Plain(f" {message_content}")
            ])
            
            # 获取适配器并发送消息
            adapters = self.host.get_platform_adapters()
            if adapters:
                await adapters[0].send_message(
                    target_type=reminder_data['launcher_type'],
                    target_id=reminder_data['launcher_id'],
                    message=message_chain
                )
                
                self.ap.logger.info(f"发送提醒给 {reminder_data['sender_id']}: {message_content}")
            else:
                self.ap.logger.error("没有可用的平台适配器")
            
        except Exception as e:
            self.ap.logger.error(f"发送提醒消息失败: {e}")
            traceback.print_exc()

    async def _handle_repeat_reminder(self, cache: ReminderCache, reminder_id: str, reminder_data: typing.Dict):
        """处理重复提醒"""
        repeat_type = reminder_data.get('repeat_type', '不重复')
        
        if repeat_type == '不重复':
            # 删除一次性提醒
            cache.reminders.pop(reminder_id, None)
            await cache.save_reminders()
            self.ap.logger.info(f"删除一次性提醒: {reminder_data['content']}")
        else:
            # 计算下次提醒时间
            current_time = datetime.fromisoformat(reminder_data['target_time'])
            next_time = None
            
            if repeat_type == '每天':
                next_time = current_time + timedelta(days=1)
            elif repeat_type == '每周':
                next_time = current_time + timedelta(weeks=1)
            elif repeat_type == '每月':
                next_time = current_time + timedelta(days=30)  # 简化处理
            
            if next_time:
                # 更新提醒时间
                reminder_data['target_time'] = next_time.isoformat()
                await cache.save_reminders()
                
                # 安排下次提醒
                await self._schedule_reminder(cache, reminder_id, reminder_data)
                self.ap.logger.info(f"安排重复提醒: {reminder_data['content']} -> {next_time}")

    @handler(PersonMessageReceived)
    async def person_message_received(self, ctx: EventContext):
        if not await self._access_control_check(ctx):
            return

        need_reply = await self._handle_command(ctx)
        if need_reply:
            ctx.prevent_default()

    @handler(GroupMessageReceived)
    @handler(GroupNormalMessageReceived)  
    async def group_message_received(self, ctx: EventContext):
        if not await self._access_control_check(ctx):
            return

        need_reply = await self._handle_command(ctx)
        if need_reply:
            ctx.prevent_default()

    async def _handle_command(self, ctx: EventContext) -> bool:
        """处理命令消息"""
        msg = str(ctx.event.query.message_chain).strip()
        launcher_id = str(ctx.event.launcher_id)
        launcher_type = str(ctx.event.launcher_type).split(".")[-1].lower()
        sender_id = str(ctx.event.sender_id)
        
        # 确保缓存已加载
        await self._load_cache(launcher_id, launcher_type)
        cache = self.reminder_cache[launcher_id]
        
        response = ""
        
        # 查看提醒列表
        if msg in ["查看提醒", "提醒列表", "我的提醒"]:
            response = await self._list_reminders(cache, sender_id)
        
        # 删除提醒
        elif msg.startswith("删除提醒"):
            response = await self._delete_reminder(cache, msg, sender_id)
        
        # 暂停/恢复提醒
        elif msg.startswith("暂停提醒"):
            response = await self._pause_reminder(cache, msg, sender_id)
        elif msg.startswith("恢复提醒"):
            response = await self._resume_reminder(cache, msg, sender_id)
        
        # 清除所有提醒
        elif msg == "清除所有提醒":
            response = await self._clear_all_reminders(cache, sender_id)
        
        # 帮助信息
        elif msg in ["提醒帮助", "定时提醒帮助"]:
            response = self._get_help_text()
        
        if response:
            await ctx.event.query.adapter.reply_message(
                ctx.event.query.message_event, 
                platform_types.MessageChain([platform_types.Plain(response)]), 
                False
            )
            return True
            
        return False

    async def _list_reminders(self, cache: ReminderCache, sender_id: str) -> str:
        """查看提醒列表"""
        user_reminders = [r for r in cache.reminders.values() 
                         if r['sender_id'] == sender_id and r.get('active', True)]
        
        if not user_reminders:
            return "您还没有设置任何提醒。"
        
        message = "📋 您的提醒列表：\n"
        for i, reminder in enumerate(user_reminders, 1):
            time_str = datetime.fromisoformat(reminder['target_time']).strftime("%Y-%m-%d %H:%M")
            status = "✅ 活跃" if reminder.get('active', True) else "⏸️ 暂停"
            message += f"{i}. {reminder['content']} - {time_str} ({reminder['repeat_type']}) {status}\n"
        
        return message

    async def _delete_reminder(self, cache: ReminderCache, msg: str, sender_id: str) -> str:
        """删除提醒"""
        try:
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                return "请指定要删除的提醒序号，例如：删除提醒 1"
            
            index = int(parts[1]) - 1
            user_reminders = [(k, v) for k, v in cache.reminders.items() if v['sender_id'] == sender_id]
            
            if 0 <= index < len(user_reminders):
                reminder_id, reminder_data = user_reminders[index]
                
                # 取消任务
                if reminder_id in cache.running_tasks:
                    cache.running_tasks[reminder_id].cancel()
                    cache.running_tasks.pop(reminder_id, None)
                
                # 删除提醒
                cache.reminders.pop(reminder_id, None)
                await cache.save_reminders()
                
                return f"✅ 已删除提醒：{reminder_data['content']}"
            else:
                return "提醒序号不存在！"
                
        except ValueError:
            return "请输入有效的提醒序号！"
        except Exception as e:
            self.ap.logger.error(f"删除提醒失败: {e}")
            return "删除提醒失败！"

    async def _pause_reminder(self, cache: ReminderCache, msg: str, sender_id: str) -> str:
        """暂停提醒"""
        return await self._toggle_reminder(cache, msg, sender_id, False)

    async def _resume_reminder(self, cache: ReminderCache, msg: str, sender_id: str) -> str:
        """恢复提醒"""
        return await self._toggle_reminder(cache, msg, sender_id, True)

    async def _toggle_reminder(self, cache: ReminderCache, msg: str, sender_id: str, active: bool) -> str:
        """切换提醒状态"""
        try:
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                action = "恢复" if active else "暂停"
                return f"请指定要{action}的提醒序号，例如：{action}提醒 1"
            
            index = int(parts[1]) - 1
            user_reminders = [(k, v) for k, v in cache.reminders.items() if v['sender_id'] == sender_id]
            
            if 0 <= index < len(user_reminders):
                reminder_id, reminder_data = user_reminders[index]
                
                if active and not reminder_data.get('active', True):
                    # 恢复提醒
                    reminder_data['active'] = True
                    await cache.save_reminders()
                    await self._schedule_reminder(cache, reminder_id, reminder_data)
                    return f"✅ 已恢复提醒：{reminder_data['content']}"
                    
                elif not active and reminder_data.get('active', True):
                    # 暂停提醒
                    reminder_data['active'] = False
                    await cache.save_reminders()
                    
                    # 取消任务
                    if reminder_id in cache.running_tasks:
                        cache.running_tasks[reminder_id].cancel()
                        cache.running_tasks.pop(reminder_id, None)
                    
                    return f"⏸️ 已暂停提醒：{reminder_data['content']}"
                else:
                    status = "已经是活跃状态" if active else "已经是暂停状态"
                    return f"提醒{status}！"
            else:
                return "提醒序号不存在！"
                
        except ValueError:
            return "请输入有效的提醒序号！"
        except Exception as e:
            action = "恢复" if active else "暂停"
            self.ap.logger.error(f"{action}提醒失败: {e}")
            return f"{action}提醒失败！"

    async def _clear_all_reminders(self, cache: ReminderCache, sender_id: str) -> str:
        """清除所有提醒"""
        try:
            user_reminders = [(k, v) for k, v in cache.reminders.items() if v['sender_id'] == sender_id]
            
            if not user_reminders:
                return "您没有任何提醒可以清除。"
            
            count = 0
            for reminder_id, reminder_data in user_reminders:
                # 取消任务
                if reminder_id in cache.running_tasks:
                    cache.running_tasks[reminder_id].cancel()
                    cache.running_tasks.pop(reminder_id, None)
                
                # 删除提醒
                cache.reminders.pop(reminder_id, None)
                count += 1
            
            await cache.save_reminders()
            return f"✅ 已清除 {count} 条提醒。"
            
        except Exception as e:
            self.ap.logger.error(f"清除提醒失败: {e}")
            return "清除提醒失败！"

    def _get_help_text(self) -> str:
        """获取帮助文本"""
        return """📖 智能定时提醒插件使用说明：

🤖 AI智能设置（推荐）：
直接对我说话，例如：
• "提醒我30分钟后开会"
• "明天下午3点提醒我买菜"
• "每天晚上8点提醒我吃药"

📋 手动管理命令：
• 查看提醒 - 查看所有提醒
• 删除提醒 [序号] - 删除指定提醒
• 暂停提醒 [序号] - 暂停指定提醒
• 恢复提醒 [序号] - 恢复指定提醒
• 清除所有提醒 - 清除您的所有提醒

⏰ 支持的时间格式：
• 相对时间：30分钟后、2小时后、明天
• 绝对时间：今晚8点、明天下午3点
• 重复类型：每天、每周、每月

💡 使用技巧：
AI会自动理解你的自然语言，无需记忆复杂命令格式！"""

    def __del__(self):
        """插件卸载时取消所有任务"""
        for cache in self.reminder_cache.values():
            for task in cache.running_tasks.values():
                if not task.done():
                    task.cancel()