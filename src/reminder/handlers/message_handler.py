import logging
from typing import Optional
import pkg.platform.types as platform_types
from pkg.plugin.context import EventContext, APIHost

from ..core.reminder_manager import ReminderManager

logger = logging.getLogger(__name__)

class MessageHandler:
    def __init__(self, reminder_manager: ReminderManager, host: APIHost):
        self.reminder_manager = reminder_manager
        self.host = host
        self.adapter_cache = None
        self.last_adapter_check = None

    async def handle_message(self, ctx: EventContext, is_group: bool):
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

    async def _handle_list_reminders(self, ctx: EventContext, sender_id: str):
        """处理查看提醒列表"""
        reminders = self.reminder_manager.get_user_reminders(sender_id)
        
        if not reminders:
            ctx.add_return("reply", ["您还没有设置任何提醒。"])
        else:
            message = "📋 您的提醒列表：\n"
            for i, reminder in enumerate(reminders, 1):
                time_str = reminder.target_time.strftime("%Y-%m-%d %H:%M")
                status = "✅ 活跃" if reminder.active else "⏸️ 暂停"
                message += f"{i}. {reminder.content} - {time_str} ({reminder.repeat_type}) {status}\n"
            
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
            reminders = self.reminder_manager.get_user_reminders(sender_id)
            
            if 0 <= index < len(reminders):
                reminder = reminders[index]
                
                if await self.reminder_manager.delete_reminder(reminder.id):
                    ctx.add_return("reply", [f"✅ 已删除提醒：{reminder.content}"])
                else:
                    ctx.add_return("reply", ["删除提醒失败！"])
            else:
                ctx.add_return("reply", ["提醒序号不存在！"])
                
        except ValueError:
            ctx.add_return("reply", ["请输入有效的提醒序号！"])
        except Exception as e:
            logger.error(f"删除提醒失败: {e}")
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
            reminders = self.reminder_manager.get_user_reminders(sender_id)
            
            if 0 <= index < len(reminders):
                reminder = reminders[index]
                
                if await self.reminder_manager.toggle_reminder(reminder.id, active):
                    action = "恢复" if active else "暂停"
                    ctx.add_return("reply", [f"{'✅' if active else '⏸️'} 已{action}提醒：{reminder.content}"])
                else:
                    action = "恢复" if active else "暂停"
                    ctx.add_return("reply", [f"{action}提醒失败！"])
            else:
                ctx.add_return("reply", ["提醒序号不存在！"])
                
        except ValueError:
            ctx.add_return("reply", ["请输入有效的提醒序号！"])
        except Exception as e:
            action = "恢复" if active else "暂停"
            logger.error(f"{action}提醒失败: {e}")
            ctx.add_return("reply", [f"{action}提醒失败！"])
        
        ctx.prevent_default()

    async def _handle_help(self, ctx: EventContext):
        """处理帮助命令"""
        help_text = """📖 定时提醒插件使用说明：

🤖 AI智能设置（推荐）：
直接对我说话，例如：
- "提醒我30分钟后开会"
- "明天下午3点提醒我买菜"
- "每天晚上8点提醒我吃药"

📋 手动管理命令：
- 查看提醒 - 查看所有提醒
- 删除提醒 [序号] - 删除指定提醒
- 暂停提醒 [序号] - 暂停指定提醒
- 恢复提醒 [序号] - 恢复指定提醒

⏰ 支持的时间格式：
- 相对时间：30分钟后、2小时后、明天
- 绝对时间：今晚8点、明天下午3点
- 重复类型：每天、每周、每月

💡 使用技巧：
AI会自动理解你的自然语言，无需记忆复杂命令格式！"""
        
        ctx.add_return("reply", [help_text])
        ctx.prevent_default()

    async def _get_available_adapter(self):
        """获取可用的适配器，带缓存机制"""
        try:
            # 如果缓存存在且在5分钟内，直接返回
            if self.adapter_cache and self.last_adapter_check:
                if (datetime.now() - self.last_adapter_check).seconds < 300:
                    return self.adapter_cache
            
            # 重新获取适配器
            adapters = self.host.get_platform_adapters()
            if adapters and len(adapters) > 0:
                self.adapter_cache = adapters[0]
                self.last_adapter_check = datetime.now()
                logger.debug(f"✅ 成功获取适配器: {type(self.adapter_cache)}")
                return self.adapter_cache
            else:
                logger.warning("⚠️ 没有找到可用的平台适配器")
                return None
                
        except Exception as e:
            logger.error(f"❌ 获取适配器时出错: {e}")
            return None

    async def send_reminder_message(self, reminder):
        """发送提醒消息"""
        try:
            message_content = f"⏰ 提醒：{reminder.content}"
            
            # 获取可用的适配器
            adapter = await self._get_available_adapter()
            if not adapter:
                raise Exception("没有可用的平台适配器")
            
            # 检查适配器状态
            try:
                if hasattr(adapter, 'is_connected'):
                    if not await adapter.is_connected():
                        raise Exception("适配器未连接")
            except Exception as e:
                logger.warning(f"适配器状态检查失败: {e}")
                self.adapter_cache = None
                adapter = await self._get_available_adapter()
                if not adapter:
                    raise Exception("重新获取适配器失败")
            
            # 构建消息链
            if reminder.target_type == 'group':
                message_chain = platform_types.MessageChain([
                    platform_types.At(reminder.sender_id),
                    platform_types.Plain(f" {message_content}")
                ])
            else:
                message_chain = platform_types.MessageChain([
                    platform_types.Plain(message_content)
                ])
            
            # 记录详细信息用于调试
            logger.debug(f"准备发送消息: target_type={reminder.target_type}, target_id={reminder.target_id}")
            
            # 使用 host.send_active_message 方法
            try:
                await self.host.send_active_message(
                    adapter=adapter,
                    target_type=reminder.target_type,
                    target_id=reminder.target_id,
                    message=message_chain
                )
                
                logger.info(f"✅ 成功发送提醒给 {reminder.sender_id}: {message_content}")
                
            except Exception as send_error:
                # 如果是ApiNotAvailable错误，尝试使用备用方法
                if "ApiNotAvailable" in str(send_error):
                    logger.warning("API不可用，尝试备用发送方法...")
                    
                    # 清除适配器缓存
                    self.adapter_cache = None
                    
                    # 等待一下再重试
                    await asyncio.sleep(2)
                    
                    # 重新获取适配器
                    adapter = await self._get_available_adapter()
                    if not adapter:
                        raise Exception("无法获取可用的适配器")
                    
                    # 再次尝试发送
                    await self.host.send_active_message(
                        adapter=adapter,
                        target_type=reminder.target_type,
                        target_id=reminder.target_id,
                        message=message_chain
                    )
                    
                    logger.info(f"✅ 备用方法成功发送提醒")
                else:
                    raise send_error
            
        except Exception as e:
            logger.error(f"❌ 发送提醒消息失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise 