import asyncio
import json
import os
import re
import traceback
from datetime import datetime, timedelta
from typing import Dict, List
import dateparser
import logging
from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *
import pkg.platform.types as platform_types


# 注册插件
@register(name="ReminderPlugin", description="智能定时提醒插件，支持设置单次和重复提醒，基于自然语言理解 (v1.0.1 已修复发送问题)", version="1.0.1", author="Assistant")
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
        
        # 加载已保存的提醒
        await self._load_reminders()
        
        # 恢复所有活跃的提醒任务
        restored_count = 0
        for reminder_id, reminder_data in self.reminders.items():
            if reminder_data.get('active', True):
                # 检查提醒时间是否还未到
                target_time = datetime.fromisoformat(reminder_data['target_time'])
                if target_time > datetime.now():
                    await self._schedule_reminder(reminder_id, reminder_data)
                    restored_count += 1
                else:
                    self.ap.logger.info(f"⏰ 跳过已过期的提醒: {reminder_data['content']}")
        
        self.ap.logger.info(f"🚀 提醒插件初始化完成，恢复了 {restored_count} 个活跃提醒任务")

    async def _check_adapter_availability(self):
        """检查适配器可用性"""
        try:
            adapters = self.host.get_platform_adapters()
            if adapters and len(adapters) > 0:
                self.adapter_available = True
                self.ap.logger.info(f"✅ 适配器检查通过，共找到 {len(adapters)} 个适配器")
            else:
                self.ap.logger.warning("⚠️ 没有找到可用的平台适配器")
                self.adapter_available = False
        except Exception as e:
            self.ap.logger.error(f"❌ 检查适配器时出错: {e}")
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
            # 获取目标信息 - 参考Async_Task_runner的实现
            target_info = {
                "target_id": str(query.launcher_id),
                "sender_id": str(query.sender_id), 
                "target_type": str(query.launcher_type).split(".")[-1].lower(),
            }
            
            # 智能检测重复类型
            detected_repeat_type = self._detect_repeat_type(time_description)
            if detected_repeat_type != "不重复":
                repeat_type = detected_repeat_type
                self.ap.logger.info(f"🔄 自动检测到重复类型: {repeat_type}")
            
            # 解析时间
            target_time = await self._parse_time_natural(time_description)
            if not target_time:
                # 给出更详细的时间格式提示
                return f"""⚠️ 时间格式无法识别：{time_description}

📝 支持的时间格式示例：
• 相对时间：30分钟后、2小时后、明天
• 具体时间：明天上午9点、今晚8点、后天下午3点
• 星期时间：下周四晚上9点、周五上午10点
• 重复时间：每天早上7点、每周一下午2点

💡 请尝试使用更明确的时间表达，如"明天上午9点"或"下周四晚上9点"。"""

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
            
            self.ap.logger.info(f"🎯 用户 {target_info['sender_id']} 设置提醒成功: {content} 在 {time_str_formatted}")
            
            return f"✅ 提醒设置成功！\n📅 时间：{time_str_formatted}\n📝 内容：{content}{repeat_info}"

        except Exception as e:
            self.ap.logger.error(f"❌ 设置提醒失败: {e}")
            self.ap.logger.error(traceback.format_exc())
            return f"❌ 设置提醒失败：{str(e)}"

    def _detect_repeat_type(self, time_description: str) -> str:
        """智能检测重复类型"""
        time_lower = time_description.lower()
        
        # 检测每天
        if any(word in time_lower for word in ['每天', '每日', '天天']):
            return "每天"
        
        # 检测每周
        if any(word in time_lower for word in ['每周', '每星期', '周周']):
            return "每周"
        
        # 检测每月
        if any(word in time_lower for word in ['每月', '每个月', '月月']):
            return "每月"
        
        # 检测特定星期几（暗示每周重复）
        weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日', 
                   '星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        
        for weekday in weekdays:
            if weekday in time_lower:
                # 如果没有明确说"下周"等限定词，默认为每周重复
                if not any(word in time_lower for word in ['下周', '下个', '本周', '这周']):
                    return "每周"
        
        return "不重复"

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
        """增强的自然语言时间解析"""
        try:
            # 预处理时间字符串
            processed_time_str = self._preprocess_time_string(time_str)
            
            # 先尝试使用dateparser，配置更多选项
            parsed_time = dateparser.parse(
                processed_time_str, 
                languages=['zh', 'en'],
                settings={
                    'PREFER_FUTURE': True,
                    'RETURN_AS_TIMEZONE_AWARE': False,
                    'DATE_ORDER': 'YMD',
                    'PREFER_LANGUAGE_DATE_ORDER': True,
                }
            )
            
            if parsed_time:
                self.ap.logger.info(f"✅ dateparser解析成功: {time_str} -> {parsed_time}")
                return parsed_time
            
            # 如果dateparser失败，使用增强的手动解析
            manual_result = await self._parse_time_manual_enhanced(time_str)
            if manual_result:
                self.ap.logger.info(f"✅ 手动解析成功: {time_str} -> {manual_result}")
                return manual_result
                
            self.ap.logger.warning(f"⚠️ 时间解析失败: {time_str}")
            return None
            
        except Exception as e:
            self.ap.logger.error(f"❌ 解析时间异常: {e}")
            return None

    def _preprocess_time_string(self, time_str: str) -> str:
        """预处理时间字符串，转换中文表达为更易识别的格式"""
        # 数字转换
        time_str = time_str.replace('一', '1').replace('二', '2').replace('三', '3') \
                          .replace('四', '4').replace('五', '5').replace('六', '6') \
                          .replace('七', '7').replace('八', '8').replace('九', '9') \
                          .replace('十', '10').replace('零', '0')
        
        # 时间词汇转换
        time_str = time_str.replace('早上', '上午').replace('早晨', '上午') \
                          .replace('晚上', '下午').replace('夜里', '下午') \
                          .replace('中午', '12:00').replace('午夜', '00:00')
        
        # 添加冒号
        time_str = time_str.replace('点', ':00').replace('时', ':00')
        
        # 处理半点
        time_str = time_str.replace(':00半', ':30')
        
        return time_str

    async def _parse_time_manual_enhanced(self, time_str: str) -> datetime:
        """增强的手动时间解析"""
        now = datetime.now()
        time_str_lower = time_str.lower()
        
        # 处理相对时间
        if "后" in time_str:
            return self._parse_relative_time(time_str, now)
        
        # 处理明天/后天等
        if "明天" in time_str:
            return self._parse_tomorrow_time(time_str, now)
        elif "后天" in time_str:
            return self._parse_day_after_tomorrow_time(time_str, now)
        
        # 处理今天的时间
        if "今天" in time_str or "今晚" in time_str or "今早" in time_str:
            return self._parse_today_time(time_str, now)
        
        # 处理星期
        for i, day_name in enumerate(['周一', '周二', '周三', '周四', '周五', '周六', '周日']):
            if day_name in time_str or f'星期{["一","二","三","四","五","六","日"][i]}' in time_str:
                return self._parse_weekday_time(time_str, i, now)
        
        # 处理具体时间点（如"21点"、"上午9点"）
        return self._parse_specific_time(time_str, now)

    def _parse_relative_time(self, time_str: str, now: datetime) -> datetime:
        """解析相对时间（X分钟后、X小时后等）"""
        time_str = time_str.replace("后", "")
        
        # 提取数字
        numbers = re.findall(r'\d+', time_str)
        if not numbers:
            return None
        
        value = int(numbers[0])
        
        if "分钟" in time_str or "分" in time_str:
            return now + timedelta(minutes=value)
        elif "小时" in time_str or "时" in time_str:
            return now + timedelta(hours=value)
        elif "天" in time_str:
            return now + timedelta(days=value)
        elif "周" in time_str or "星期" in time_str:
            return now + timedelta(weeks=value)
        
        return None

    def _parse_tomorrow_time(self, time_str: str, now: datetime) -> datetime:
        """解析明天的时间"""
        tomorrow = now + timedelta(days=1)
        time_part = self._extract_time_from_string(time_str)
        
        if time_part:
            return datetime.combine(tomorrow.date(), time_part)
        else:
            # 如果没有具体时间，默认为明天9点
            return datetime.combine(tomorrow.date(), datetime.strptime("09:00", "%H:%M").time())

    def _parse_day_after_tomorrow_time(self, time_str: str, now: datetime) -> datetime:
        """解析后天的时间"""
        day_after_tomorrow = now + timedelta(days=2)
        time_part = self._extract_time_from_string(time_str)
        
        if time_part:
            return datetime.combine(day_after_tomorrow.date(), time_part)
        else:
            return datetime.combine(day_after_tomorrow.date(), datetime.strptime("09:00", "%H:%M").time())

    def _parse_today_time(self, time_str: str, now: datetime) -> datetime:
        """解析今天的时间"""
        time_part = self._extract_time_from_string(time_str)
        
        if time_part:
            target = datetime.combine(now.date(), time_part)
            # 如果时间已过，设为明天
            if target <= now:
                target = target + timedelta(days=1)
            return target
        
        return None

    def _parse_weekday_time(self, time_str: str, weekday: int, now: datetime) -> datetime:
        """解析星期X的时间"""
        # 计算下一个指定星期几
        days_ahead = weekday - now.weekday()
        if days_ahead <= 0:  # 如果是今天或已过，取下周
            days_ahead += 7
        
        target_date = now.date() + timedelta(days=days_ahead)
        time_part = self._extract_time_from_string(time_str)
        
        if time_part:
            return datetime.combine(target_date, time_part)
        else:
            # 默认为晚上8点
            return datetime.combine(target_date, datetime.strptime("20:00", "%H:%M").time())

    def _parse_specific_time(self, time_str: str, now: datetime) -> datetime:
        """解析具体时间点"""
        time_part = self._extract_time_from_string(time_str)
        
        if time_part:
            target = datetime.combine(now.date(), time_part)
            # 如果时间已过，设为明天
            if target <= now:
                target = target + timedelta(days=1)
            return target
        
        return None

    def _extract_time_from_string(self, time_str: str):
        """从字符串中提取时间部分"""
        # 处理各种时间格式
        patterns = [
            r'(\d{1,2}):(\d{2})',  # 21:00, 9:30
            r'(\d{1,2})点(\d{1,2})',  # 9点30
            r'(\d{1,2})时(\d{1,2})',  # 9时30
            r'(\d{1,2})点',  # 21点, 9点
            r'(\d{1,2})时',  # 21时, 9时
        ]
        
        for pattern in patterns:
            match = re.search(pattern, time_str)
            if match:
                try:
                    if len(match.groups()) == 2:
                        hour, minute = int(match.group(1)), int(match.group(2))
                    else:
                        hour, minute = int(match.group(1)), 0
                    
                    # 处理上午下午
                    if "上午" in time_str or "早上" in time_str or "早晨" in time_str:
                        if hour == 12:
                            hour = 0
                    elif "下午" in time_str or "晚上" in time_str:
                        if hour < 12:
                            hour += 12
                    
                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        return datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()
                except ValueError:
                    continue
        
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
                
                # 发送提醒消息
                try:
                    await self._send_reminder_message(reminder_data)
                    self.ap.logger.info(f"🎯 提醒任务 {reminder_id} 执行成功")
                except Exception as send_error:
                    self.ap.logger.error(f"❌ 提醒任务 {reminder_id} 发送失败: {send_error}")
                    # 如果发送失败，可以选择重试一次
                    await asyncio.sleep(30)  # 等待30秒
                    try:
                        await self._send_reminder_message(reminder_data)
                        self.ap.logger.info(f"🎯 提醒任务 {reminder_id} 重试成功")
                    except Exception as retry_error:
                        self.ap.logger.error(f"❌ 提醒任务 {reminder_id} 重试也失败: {retry_error}")
                
                # 处理重复提醒
                await self._handle_repeat_reminder(reminder_id, reminder_data)
                    
        except asyncio.CancelledError:
            self.ap.logger.debug(f"⏹️ 提醒任务 {reminder_id} 被取消")
        except Exception as e:
            self.ap.logger.error(f"❌ 提醒任务执行失败: {e}")
            self.ap.logger.error(traceback.format_exc())

    async def _send_reminder_message(self, reminder_data: Dict):
        """发送提醒消息"""
        try:
            message_content = f"⏰ 提醒：{reminder_data['content']}"
            
            # 获取适配器
            adapters = self.host.get_platform_adapters()
            if not adapters:
                self.ap.logger.error("没有可用的平台适配器")
                return
            
            # 构建消息链 - 参考Waifu插件的实现
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
            
            # 使用 host.send_active_message 方法 - 参考Waifu和Async_Task_runner的实现
            await self.host.send_active_message(
                adapter=adapters[0],
                target_type=reminder_data['target_type'],
                target_id=reminder_data['target_id'],
                message=message_chain
            )
            
            self.ap.logger.info(f"✅ 成功发送提醒给 {reminder_data['sender_id']}: {message_content}")
            
        except Exception as e:
            self.ap.logger.error(f"❌ 发送提醒消息失败: {e}")
            self.ap.logger.error(traceback.format_exc())
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