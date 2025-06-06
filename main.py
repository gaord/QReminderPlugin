import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List
import dateparser
import logging
from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *
import pkg.platform.types as platform_types


# 注册插件
@register(name="ReminderPlugin", description="智能定时提醒插件，支持设置单次和重复提醒，基于自然语言理解", version="1.0", author="Assistant")
class ReminderPlugin(BasePlugin):

    def __init__(self, host: APIHost):
        self.host = host
        self.reminders: Dict[str, Dict] = {}  # 存储提醒信息
        self.data_file = "reminders.json"
        self.running_tasks = {}  # 存储运行中的任务
        self.adapter_available = False  # 适配器可用状态
        
    async def initialize(self):
        """异步初始化，加载已保存的提醒"""
        # 检查适配器可用性
        await self._check_adapter_availability()
        
        await self._load_reminders()
        # 恢复所有提醒任务
        for reminder_id, reminder_data in self.reminders.items():
            if reminder_data.get('active', True):
                await self._schedule_reminder(reminder_id, reminder_data)

    async def _check_adapter_availability(self):
        """检查适配器可用性"""
        try:
            adapters = self.host.get_platform_adapters()
            if adapters and len(adapters) > 0:
                adapter = adapters[0]
                if hasattr(adapter, 'send_message'):
                    self.adapter_available = True
                    self.ap.logger.info(f"适配器检查通过，类型: {type(adapter).__name__}")
                else:
                    self.ap.logger.warning("适配器不支持send_message方法")
            else:
                self.ap.logger.warning("没有找到可用的平台适配器")
        except Exception as e:
            self.ap.logger.error(f"检查适配器时出错: {e}")
            self.adapter_available = False

    async def _load_reminders(self):
        """从文件加载提醒数据"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.reminders = json.load(f)
                    # 转换旧格式的时间字符串为datetime对象
                    for reminder_data in self.reminders.values():
                        if isinstance(reminder_data.get('target_time'), str):
                            reminder_data['target_time'] = reminder_data['target_time']
        except Exception as e:
            self.ap.logger.error(f"加载提醒数据失败: {e}")
            self.reminders = {}

    async def _save_reminders(self):
        """保存提醒数据到文件"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            self.ap.logger.error(f"保存提醒数据失败: {e}")

    @llm_func("set_reminder")
    async def set_reminder_llm(self, query, content: str, time_description: str, repeat_type: str = "不重复"):
        """AI函数调用接口：设置提醒
        当用户说要设置提醒、定时任务等时调用此函数
        
        Args:
            content(str): 提醒内容，例如："开会"、"吃药"、"买菜"等
            time_description(str): 时间描述，支持自然语言，例如："30分钟后"、"明天下午3点"、"今晚8点"等
            repeat_type(str): 重复类型，可选值："不重复"、"每天"、"每周"、"每月"
            
        Returns:
            str: 设置结果信息
        """
        try:
            # 检查适配器可用性
            await self._check_adapter_availability()
            if not self.adapter_available:
                return "⚠️ 当前消息发送功能不可用，提醒可能无法正常发送。请稍后重试或联系管理员检查机器人连接状态。"
            
            # 获取目标信息
            target_info = {
                "target_id": str(query.launcher_id),
                "sender_id": str(query.sender_id), 
                "target_type": str(query.launcher_type).split(".")[-1].lower(),
            }
            
            # 解析时间
            target_time = await self._parse_time_natural(time_description)
            if not target_time:
                return f"⚠️ 时间格式无法识别：{time_description}。请使用如'30分钟后'、'明天下午3点'、'今晚8点'等格式"

            # 检查时间是否已过
            if target_time <= datetime.now():
                return "⚠️ 设置的时间已经过去了，请重新设置！"

            # 生成提醒ID
            reminder_id = f"{target_info['sender_id']}_{int(datetime.now().timestamp())}"
            
            # 创建提醒数据
            reminder_data = {
                'id': reminder_id,
                'sender_id': target_info['sender_id'],
                'target_id': target_info['target_id'],
                'target_type': target_info['target_type'],
                'content': content,
                'target_time': target_time.isoformat(),
                'repeat_type': repeat_type,
                'active': True,
                'created_at': datetime.now().isoformat()
            }

            # 保存提醒
            self.reminders[reminder_id] = reminder_data
            await self._save_reminders()

            # 安排提醒任务
            await self._schedule_reminder(reminder_id, reminder_data)

            # 返回确认信息
            time_str_formatted = target_time.strftime("%Y-%m-%d %H:%M")
            repeat_info = f"，重复类型：{repeat_type}" if repeat_type != "不重复" else ""
            return f"✅ 提醒设置成功！\n时间：{time_str_formatted}\n内容：{content}{repeat_info}"

        except Exception as e:
            self.ap.logger.error(f"设置提醒失败: {e}")
            import traceback
            self.ap.logger.error(traceback.format_exc())
            return f"❌ 设置提醒失败：{str(e)}"

    @handler(PersonNormalMessageReceived)
    async def person_normal_message_received(self, ctx: EventContext):
        await self._handle_message(ctx, False)

    @handler(GroupNormalMessageReceived)
    async def group_normal_message_received(self, ctx: EventContext):
        await self._handle_message(ctx, True)

    async def _handle_message(self, ctx: EventContext, is_group: bool):
        """处理消息"""
        msg = ctx.event.text_message.strip()
        sender_id = str(ctx.event.sender_id)
        
        # 查看提醒列表
        if msg in ["查看提醒", "提醒列表", "我的提醒"]:
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
        elif msg in ["提醒帮助", "定时提醒帮助"]:
            await self._handle_help(ctx)

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

    async def _schedule_reminder(self, reminder_id: str, reminder_data: Dict):
        """安排提醒任务"""
        try:
            target_time = datetime.fromisoformat(reminder_data['target_time'])
            delay = (target_time - datetime.now()).total_seconds()
            
            if delay > 0:
                # 创建异步任务
                task = asyncio.create_task(self._reminder_task(reminder_id, delay))
                self.running_tasks[reminder_id] = task
                self.ap.logger.debug(f"安排提醒任务 {reminder_id}，延迟 {delay} 秒")
                
        except Exception as e:
            self.ap.logger.error(f"安排提醒任务失败: {e}")

    async def _reminder_task(self, reminder_id: str, delay: float):
        """提醒任务"""
        try:
            await asyncio.sleep(delay)
            
            # 检查提醒是否仍然存在且活跃
            if reminder_id in self.reminders and self.reminders[reminder_id].get('active', True):
                reminder_data = self.reminders[reminder_id]
                
                # 重新检查适配器可用性
                await self._check_adapter_availability()
                
                if not self.adapter_available:
                    self.ap.logger.warning(f"适配器不可用，提醒 {reminder_id} 暂时无法发送，将重试")
                    # 延迟重试
                    await asyncio.sleep(60)  # 等待1分钟后重试
                    await self._check_adapter_availability()
                
                if self.adapter_available:
                    # 发送提醒消息
                    await self._send_reminder_message(reminder_data)
                    
                    # 处理重复提醒
                    await self._handle_repeat_reminder(reminder_id, reminder_data)
                else:
                    self.ap.logger.error(f"适配器仍然不可用，提醒 {reminder_id} 发送失败")
                    # 可以选择稍后重试或者记录失败日志
                    
        except asyncio.CancelledError:
            self.ap.logger.debug(f"提醒任务 {reminder_id} 被取消")
        except Exception as e:
            self.ap.logger.error(f"提醒任务执行失败: {e}")
            import traceback
            self.ap.logger.error(traceback.format_exc())

    async def _send_reminder_message(self, reminder_data: Dict):
        """发送提醒消息"""
        try:
            message_content = f"⏰ 提醒：{reminder_data['content']}"
            
            # 获取可用的适配器
            adapters = self.host.get_platform_adapters()
            if not adapters:
                self.ap.logger.error("没有可用的平台适配器")
                return
            
            # 选择第一个可用的适配器
            adapter = adapters[0]
            
            # 检查适配器状态
            if not hasattr(adapter, 'send_message'):
                self.ap.logger.error("适配器不支持send_message方法")
                return
            
            # 构建消息链
            if reminder_data['target_type'] == 'group':
                # 群聊中@用户
                message_chain = platform_types.MessageChain([
                    platform_types.At(reminder_data['sender_id']),
                    platform_types.Plain(f" {message_content}")
                ])
            else:
                # 私聊直接发送
                message_chain = platform_types.MessageChain([
                    platform_types.Plain(message_content)
                ])
            
            # 尝试发送消息
            await adapter.send_message(
                target_type=reminder_data['target_type'],
                target_id=reminder_data['target_id'],
                message=message_chain
            )
            
            self.ap.logger.info(f"发送提醒给 {reminder_data['sender_id']}: {message_content}")
            
        except Exception as e:
            self.ap.logger.error(f"发送提醒消息失败: {e}")
            
            # 尝试备用发送方法
            try:
                await self._fallback_send_message(reminder_data, message_content)
            except Exception as fallback_error:
                self.ap.logger.error(f"备用发送方法也失败: {fallback_error}")

    async def _fallback_send_message(self, reminder_data: Dict, message_content: str):
        """备用消息发送方法"""
        try:
            # 使用host的send_active_message方法
            adapters = self.host.get_platform_adapters()
            if adapters:
                message_chain = platform_types.MessageChain([
                    platform_types.Plain(message_content)
                ])
                
                await self.host.send_active_message(
                    adapter=adapters[0],
                    target_type=reminder_data['target_type'],
                    target_id=reminder_data['target_id'],
                    message=message_chain
                )
                
                self.ap.logger.info(f"通过备用方法发送提醒: {message_content}")
            else:
                self.ap.logger.error("没有可用的适配器进行备用发送")
                
        except Exception as e:
            self.ap.logger.error(f"备用发送方法失败: {e}")
            raise

    async def _handle_repeat_reminder(self, reminder_id: str, reminder_data: Dict):
        """处理重复提醒"""
        repeat_type = reminder_data.get('repeat_type', '不重复')
        
        if repeat_type == '不重复':
            # 删除一次性提醒
            if reminder_id in self.reminders:
                del self.reminders[reminder_id]
                await self._save_reminders()
                if reminder_id in self.running_tasks:
                    del self.running_tasks[reminder_id]
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

⏰ 支持的时间格式：
• 相对时间：30分钟后、2小时后、明天
• 绝对时间：今晚8点、明天下午3点
• 重复类型：每天、每周、每月

💡 使用技巧：
AI会自动理解你的自然语言，无需记忆复杂命令格式！"""
        
        ctx.add_return("reply", [help_text])
        ctx.prevent_default()

    def __del__(self):
        """插件卸载时取消所有任务"""
        for task in self.running_tasks.values():
            if not task.done():
                task.cancel()