import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List
from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *


# 注册插件
@register(name="ReminderPlugin", description="定时提醒插件，支持设置单次和重复提醒", version="1.0", author="Assistant")
class ReminderPlugin(BasePlugin):

    def __init__(self, host: APIHost):
        self.reminders: Dict[str, Dict] = {}  # 存储提醒信息
        self.data_file = "reminders.json"
        self.running_tasks = {}  # 存储运行中的任务
        
    async def initialize(self):
        """异步初始化，加载已保存的提醒"""
        await self._load_reminders()
        # 恢复所有提醒任务
        for reminder_id, reminder_data in self.reminders.items():
            if reminder_data.get('active', True):
                await self._schedule_reminder(reminder_id, reminder_data)

    async def _load_reminders(self):
        """从文件加载提醒数据"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.reminders = json.load(f)
        except Exception as e:
            self.ap.logger.error(f"加载提醒数据失败: {e}")
            self.reminders = {}

    async def _save_reminders(self):
        """保存提醒数据到文件"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.ap.logger.error(f"保存提醒数据失败: {e}")

    @handler(PersonNormalMessageReceived)
    async def person_normal_message_received(self, ctx: EventContext):
        await self._handle_message(ctx, is_group=False)

    @handler(GroupNormalMessageReceived)
    async def group_normal_message_received(self, ctx: EventContext):
        await self._handle_message(ctx, is_group=True)

    async def _handle_message(self, ctx: EventContext, is_group: bool):
        """处理消息"""
        msg = ctx.event.text_message.strip()
        sender_id = ctx.event.sender_id
        
        # 设置提醒命令
        if msg.startswith("提醒我"):
            await self._handle_set_reminder(ctx, msg, sender_id, is_group)
        
        # 查看提醒列表
        elif msg == "查看提醒" or msg == "提醒列表":
            await self._handle_list_reminders(ctx, sender_id)
        
        # 删除提醒
        elif msg.startswith("删除提醒"):
            await self._handle_delete_reminder(ctx, msg, sender_id)
        
        # 暂停/恢复提醒
        elif msg.startswith("暂停提醒"):
            await self._handle_pause_reminder(ctx, msg, sender_id)
        elif msg.startswith("恢复提醒"):
            await self._handle_resume_reminder(ctx, msg, sender_id)
        
        # 帮助信息
        elif msg == "提醒帮助":
            await self._handle_help(ctx)

    async def _handle_set_reminder(self, ctx: EventContext, msg: str, sender_id: str, is_group: bool):
        """处理设置提醒命令"""
        try:
            # 解析命令格式: 提醒我 [时间] [内容] [重复类型]
            parts = msg.split(" ", 3)
            if len(parts) < 3:
                ctx.add_return("reply", ["格式错误！使用方法：\n提醒我 [时间] [内容] [重复类型(可选)]\n例如：提醒我 10分钟后 开会\n或：提醒我 2024-01-01 12:00 新年快乐 每天"])
                ctx.prevent_default()
                return

            time_str = parts[1]
            content = parts[2]
            repeat_type = parts[3] if len(parts) > 3 else "不重复"

            # 解析时间
            target_time = await self._parse_time(time_str)
            if not target_time:
                ctx.add_return("reply", ["时间格式错误！支持的格式：\n- 相对时间：10分钟后, 2小时后, 1天后\n- 绝对时间：2024-01-01 12:00\n- 简单时间：12:00"])
                ctx.prevent_default()
                return

            # 检查时间是否已过
            if target_time <= datetime.now():
                ctx.add_return("reply", ["设置的时间已经过去了，请重新设置！"])
                ctx.prevent_default()
                return

            # 生成提醒ID
            reminder_id = f"{sender_id}_{len(self.reminders)}"
            
            # 创建提醒数据
            reminder_data = {
                'id': reminder_id,
                'sender_id': sender_id,
                'content': content,
                'target_time': target_time.isoformat(),
                'repeat_type': repeat_type,
                'is_group': is_group,
                'active': True,
                'created_at': datetime.now().isoformat()
            }

            # 保存提醒
            self.reminders[reminder_id] = reminder_data
            await self._save_reminders()

            # 安排提醒任务
            await self._schedule_reminder(reminder_id, reminder_data)

            # 回复确认
            time_str_formatted = target_time.strftime("%Y-%m-%d %H:%M")
            repeat_info = f"，重复类型：{repeat_type}" if repeat_type != "不重复" else ""
            ctx.add_return("reply", [f"✅ 提醒设置成功！\n时间：{time_str_formatted}\n内容：{content}{repeat_info}"])
            ctx.prevent_default()

        except Exception as e:
            self.ap.logger.error(f"设置提醒失败: {e}")
            ctx.add_return("reply", ["设置提醒失败，请检查命令格式！"])
            ctx.prevent_default()

    async def _parse_time(self, time_str: str) -> datetime:
        """解析时间字符串"""
        now = datetime.now()
        
        # 相对时间解析
        if "后" in time_str:
            time_str = time_str.replace("后", "")
            if "分钟" in time_str:
                minutes = int(time_str.replace("分钟", ""))
                return now + timedelta(minutes=minutes)
            elif "小时" in time_str:
                hours = int(time_str.replace("小时", ""))
                return now + timedelta(hours=hours)
            elif "天" in time_str:
                days = int(time_str.replace("天", ""))
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

    async def _schedule_reminder(self, reminder_id: str, reminder_data: Dict):
        """安排提醒任务"""
        try:
            target_time = datetime.fromisoformat(reminder_data['target_time'])
            delay = (target_time - datetime.now()).total_seconds()
            
            if delay > 0:
                # 创建异步任务
                task = asyncio.create_task(self._reminder_task(reminder_id, delay))
                self.running_tasks[reminder_id] = task
                
        except Exception as e:
            self.ap.logger.error(f"安排提醒任务失败: {e}")

    async def _reminder_task(self, reminder_id: str, delay: float):
        """提醒任务"""
        try:
            await asyncio.sleep(delay)
            
            # 检查提醒是否仍然存在且活跃
            if reminder_id in self.reminders and self.reminders[reminder_id].get('active', True):
                reminder_data = self.reminders[reminder_id]
                
                # 发送提醒消息
                await self._send_reminder_message(reminder_data)
                
                # 处理重复提醒
                await self._handle_repeat_reminder(reminder_id, reminder_data)
                
        except asyncio.CancelledError:
            self.ap.logger.debug(f"提醒任务 {reminder_id} 被取消")
        except Exception as e:
            self.ap.logger.error(f"提醒任务执行失败: {e}")

    async def _send_reminder_message(self, reminder_data: Dict):
        """发送提醒消息"""
        try:
            message = f"⏰ 提醒：{reminder_data['content']}"
            
            # 这里需要根据实际的API接口来发送消息
            # 由于缺少具体的发送接口，这里只是记录日志
            self.ap.logger.info(f"发送提醒给 {reminder_data['sender_id']}: {message}")
            
            # 实际实现中，你需要调用相应的API来发送消息
            # 例如：await self.ap.send_message(reminder_data['sender_id'], message, reminder_data['is_group'])
            
        except Exception as e:
            self.ap.logger.error(f"发送提醒消息失败: {e}")

    async def _handle_repeat_reminder(self, reminder_id: str, reminder_data: Dict):
        """处理重复提醒"""
        repeat_type = reminder_data.get('repeat_type', '不重复')
        
        if repeat_type == '不重复':
            # 删除一次性提醒
            if reminder_id in self.reminders:
                del self.reminders[reminder_id]
                await self._save_reminders()
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
                await self._save_reminders()
                
                # 安排下次提醒
                await self._schedule_reminder(reminder_id, reminder_data)

    async def _handle_list_reminders(self, ctx: EventContext, sender_id: str):
        """处理查看提醒列表"""
        user_reminders = [r for r in self.reminders.values() if r['sender_id'] == sender_id and r.get('active', True)]
        
        if not user_reminders:
            ctx.add_return("reply", ["您还没有设置任何提醒。"])
        else:
            message = "📋 您的提醒列表：\n"
            for i, reminder in enumerate(user_reminders, 1):
                time_str = datetime.fromisoformat(reminder['target_time']).strftime("%Y-%m-%d %H:%M")
                status = "✅ 活跃" if reminder.get('active', True) else "⏸️ 暂停"
                message += f"{i}. {reminder['content']} - {time_str} ({reminder['repeat_type']}) {status}\n"
            
            ctx.add_return("reply", [message])
        
        ctx.prevent_default()

    async def _handle_delete_reminder(self, ctx: EventContext, msg: str, sender_id: str):
        """处理删除提醒"""
        try:
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                ctx.add_return("reply", ["请指定要删除的提醒序号，例如：删除提醒 1"])
                ctx.prevent_default()
                return
            
            index = int(parts[1]) - 1
            user_reminders = [(k, v) for k, v in self.reminders.items() if v['sender_id'] == sender_id]
            
            if 0 <= index < len(user_reminders):
                reminder_id, reminder_data = user_reminders[index]
                
                # 取消任务
                if reminder_id in self.running_tasks:
                    self.running_tasks[reminder_id].cancel()
                    del self.running_tasks[reminder_id]
                
                # 删除提醒
                del self.reminders[reminder_id]
                await self._save_reminders()
                
                ctx.add_return("reply", [f"✅ 已删除提醒：{reminder_data['content']}"])
            else:
                ctx.add_return("reply", ["提醒序号不存在！"])
                
        except ValueError:
            ctx.add_return("reply", ["请输入有效的提醒序号！"])
        except Exception as e:
            self.ap.logger.error(f"删除提醒失败: {e}")
            ctx.add_return("reply", ["删除提醒失败！"])
        
        ctx.prevent_default()

    async def _handle_pause_reminder(self, ctx: EventContext, msg: str, sender_id: str):
        """处理暂停提醒"""
        await self._toggle_reminder(ctx, msg, sender_id, False)

    async def _handle_resume_reminder(self, ctx: EventContext, msg: str, sender_id: str):
        """处理恢复提醒"""
        await self._toggle_reminder(ctx, msg, sender_id, True)

    async def _toggle_reminder(self, ctx: EventContext, msg: str, sender_id: str, active: bool):
        """切换提醒状态"""
        try:
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                action = "恢复" if active else "暂停"
                ctx.add_return("reply", [f"请指定要{action}的提醒序号，例如：{action}提醒 1"])
                ctx.prevent_default()
                return
            
            index = int(parts[1]) - 1
            user_reminders = [(k, v) for k, v in self.reminders.items() if v['sender_id'] == sender_id]
            
            if 0 <= index < len(user_reminders):
                reminder_id, reminder_data = user_reminders[index]
                
                if active and not reminder_data.get('active', True):
                    # 恢复提醒
                    reminder_data['active'] = True
                    await self._save_reminders()
                    await self._schedule_reminder(reminder_id, reminder_data)
                    ctx.add_return("reply", [f"✅ 已恢复提醒：{reminder_data['content']}"])
                    
                elif not active and reminder_data.get('active', True):
                    # 暂停提醒
                    reminder_data['active'] = False
                    await self._save_reminders()
                    
                    # 取消任务
                    if reminder_id in self.running_tasks:
                        self.running_tasks[reminder_id].cancel()
                        del self.running_tasks[reminder_id]
                    
                    ctx.add_return("reply", [f"⏸️ 已暂停提醒：{reminder_data['content']}"])
                else:
                    status = "已经是活跃状态" if active else "已经是暂停状态"
                    ctx.add_return("reply", [f"提醒{status}！"])
            else:
                ctx.add_return("reply", ["提醒序号不存在！"])
                
        except ValueError:
            ctx.add_return("reply", ["请输入有效的提醒序号！"])
        except Exception as e:
            action = "恢复" if active else "暂停"
            self.ap.logger.error(f"{action}提醒失败: {e}")
            ctx.add_return("reply", [f"{action}提醒失败！"])
        
        ctx.prevent_default()

    async def _handle_help(self, ctx: EventContext):
        """处理帮助命令"""
        help_text = """📖 定时提醒插件使用说明：

🔧 设置提醒：
• 提醒我 [时间] [内容] [重复类型(可选)]
• 时间格式：
  - 相对时间：10分钟后, 2小时后, 1天后
  - 绝对时间：2024-01-01 12:00
  - 简单时间：12:00 (今天，如已过则明天)
• 重复类型：不重复(默认), 每天, 每周, 每月

📋 管理提醒：
• 查看提醒 - 查看所有提醒
• 删除提醒 [序号] - 删除指定提醒
• 暂停提醒 [序号] - 暂停指定提醒
• 恢复提醒 [序号] - 恢复指定提醒

💡 示例：
• 提醒我 30分钟后 开会
• 提醒我 18:00 下班回家 每天
• 提醒我 2024-12-25 12:00 圣诞快乐"""
        
        ctx.add_return("reply", [help_text])
        ctx.prevent_default()

    def __del__(self):
        """插件卸载时取消所有任务"""
        for task in self.running_tasks.values():
            if not task.done():
                task.cancel()