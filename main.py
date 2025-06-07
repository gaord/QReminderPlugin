import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import calendar
import dateparser
import logging
from pkg.plugin import register, host, logger
from pkg.plugin.context import EventContext
from pkg.plugin.host import APIHost
import pkg.platform.types as platform_types

from .src.reminder.core.reminder_manager import ReminderManager
from .src.reminder.handlers.message_handler import MessageHandler

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

@register(name="QReminderPlugin", desc="智能定时提醒插件", version="1.3.0", author="admin")
class ReminderPlugin:
    def __init__(self):
        self.reminder_manager = ReminderManager()
        self.message_handler = MessageHandler(self.reminder_manager)
        self.data_file = "reminders.json"
        self.running_tasks = {}  # 存储运行中的任务
        self.adapter_cache = None  # 缓存适配器
        self.last_adapter_check = None  # 最后检查适配器的时间
        
    async def initialize(self):
        """初始化插件"""
        await self.reminder_manager.initialize()
        logger.info("QReminderPlugin initialization completed")

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
            logger.error(f"加载提醒数据失败: {e}")
            self.reminders = {}

    async def _save_reminders(self):
        """保存提醒数据到文件"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"保存提醒数据失败: {e}")

    @register(name="set_reminder_llm", desc="设置提醒")
    async def set_reminder_llm(self, ctx: EventContext, args: Dict[str, Any]) -> Optional[str]:
        """设置提醒的LLM函数"""
        try:
            # 获取参数
            content = args.get("content", "")
            time_desc = args.get("time", "")
            repeat_type = args.get("repeat_type", "不重复")
            sender_id = args.get("sender_id", "")
            target_id = args.get("target_id", "")
            target_type = args.get("target_type", "person")

            # 创建提醒
            reminder = await self.reminder_manager.create_reminder(
                sender_id=sender_id,
                target_id=target_id,
                target_type=target_type,
                content=content,
                time_desc=time_desc,
                repeat_type=repeat_type
            )

            if reminder:
                return f"已设置提醒：{content}，时间：{time_desc}，重复类型：{repeat_type}"
            else:
                return "设置提醒失败，请检查时间格式是否正确"

        except Exception as e:
            logger.error(f"设置提醒时发生错误: {str(e)}")
            return f"设置提醒失败：{str(e)}"

    @register(name="on_normal_message", desc="处理普通消息")
    async def on_normal_message(self, ctx: EventContext, args: Dict[str, Any]) -> Optional[str]:
        """处理普通消息"""
        return await self.message_handler.handle_message(ctx, args)

    @register(name="on_group_message", desc="处理群组消息")
    async def on_group_message(self, ctx: EventContext, args: Dict[str, Any]) -> Optional[str]:
        """处理群组消息"""
        return await self.message_handler.handle_message(ctx, args)

    async def _parse_time_natural(self, time_str: str) -> datetime:
        """增强的自然语言时间解析"""
        try:
            logger.debug(f"开始解析时间: '{time_str}'")
            
            # 预处理时间字符串
            processed_time = await self._preprocess_time_string(time_str)
            logger.debug(f"预处理后: '{processed_time}'")
            
            # 尝试多种解析策略
            parsers = [
                self._parse_weekday_time,      # 星期相关
                self._parse_relative_days,      # 相对日期
                self._parse_specific_time,      # 具体时间
                self._parse_with_dateparser,    # dateparser库
                self._parse_time_manual         # 手动解析
            ]
            
            for parser in parsers:
                result = await parser(processed_time)
                if result and result > datetime.now():
                    logger.debug(f"解析成功 ({parser.__name__}): {result}")
                    return result
            
            # 如果所有方法都失败，尝试原始字符串
            for parser in parsers:
                result = await parser(time_str)
                if result and result > datetime.now():
                    logger.debug(f"原始字符串解析成功 ({parser.__name__}): {result}")
                    return result
                    
            return None
            
        except Exception as e:
            logger.error(f"解析时间失败: {e}")
            return None

    async def _preprocess_time_string(self, time_str: str) -> str:
        """预处理时间字符串，统一格式"""
        # 移除多余的空格
        time_str = ' '.join(time_str.split())
        
        # 统一星期表达
        weekday_map = {
            '周一': '星期一', '周二': '星期二', '周三': '星期三',
            '周四': '星期四', '周五': '星期五', '周六': '星期六',
            '周日': '星期日', '周天': '星期日', '礼拜': '星期',
            '这周': '本周', '这个周': '本周', '这星期': '本周'
        }
        
        for old, new in weekday_map.items():
            time_str = time_str.replace(old, new)
        
        # 统一时间表达
        time_map = {
            '早上': '上午', '早晨': '上午', '中午': '12点',
            '下午': '下午', '傍晚': '下午6点', '晚上': '晚上',
            '夜里': '晚上', '凌晨': '凌晨'
        }
        
        for old, new in time_map.items():
            time_str = time_str.replace(old, new)
        
        # 转换中文数字为阿拉伯数字
        chinese_nums = {
            '零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
            '五': '5', '六': '6', '七': '7', '八': '8', '九': '9',
            '十': '10', '十一': '11', '十二': '12'
        }
        
        for cn, num in chinese_nums.items():
            time_str = time_str.replace(cn + '点', num + '点')
        
        return time_str

    async def _parse_weekday_time(self, time_str: str) -> datetime:
        """解析星期相关的时间表达"""
        weekdays = {
            '星期一': 0, '星期二': 1, '星期三': 2, '星期四': 3,
            '星期五': 4, '星期六': 5, '星期日': 6, '星期天': 6
        }
        
        # 解析 "下周X" 模式
        next_week_pattern = r'下周(.*?)(\d{1,2})[点时]'
        match = re.search(next_week_pattern, time_str)
        if match:
            weekday_str = match.group(1).strip()
            hour = int(match.group(2))
            
            for wd_name, wd_num in weekdays.items():
                if wd_name in weekday_str or wd_name in time_str:
                    target_date = self._get_next_weekday(wd_num, weeks_ahead=1)
                    return self._combine_date_time(target_date, hour, time_str)
        
        # 解析 "本周X" 或 "这周X" 模式
        this_week_pattern = r'(本周|这周)(.*?)(\d{1,2})[点时]'
        match = re.search(this_week_pattern, time_str)
        if match:
            weekday_str = match.group(2).strip()
            hour = int(match.group(3))
            
            for wd_name, wd_num in weekdays.items():
                if wd_name in weekday_str or wd_name in time_str:
                    target_date = self._get_next_weekday(wd_num, weeks_ahead=0)
                    return self._combine_date_time(target_date, hour, time_str)
        
        # 解析普通 "星期X" 模式（默认为下一个该星期）
        for wd_name, wd_num in weekdays.items():
            if wd_name in time_str:
                # 提取时间
                time_match = re.search(r'(\d{1,2})[点时]', time_str)
                if time_match:
                    hour = int(time_match.group(1))
                    target_date = self._get_next_weekday(wd_num)
                    return self._combine_date_time(target_date, hour, time_str)
        
        return None

    async def _parse_relative_days(self, time_str: str) -> datetime:
        """解析相对日期表达"""
        now = datetime.now()
        
        # 相对日期映射
        relative_days = {
            '今天': 0, '明天': 1, '后天': 2, '大后天': 3,
            '明日': 1, '后日': 2
        }
        
        for day_name, days_offset in relative_days.items():
            if day_name in time_str:
                target_date = now + timedelta(days=days_offset)
                
                # 提取时间
                time_match = re.search(r'(\d{1,2})[点时]', time_str)
                if time_match:
                    hour = int(time_match.group(1))
                    return self._combine_date_time(target_date, hour, time_str)
                
                # 如果没有具体时间，根据上下文推测
                if '上午' in time_str:
                    return target_date.replace(hour=9, minute=0, second=0, microsecond=0)
                elif '下午' in time_str:
                    return target_date.replace(hour=15, minute=0, second=0, microsecond=0)
                elif '晚上' in time_str:
                    return target_date.replace(hour=20, minute=0, second=0, microsecond=0)
        
        return None

    async def _parse_specific_time(self, time_str: str) -> datetime:
        """解析具体时间表达"""
        now = datetime.now()
        
        # 解析 "X点X分" 格式
        time_pattern = r'(\d{1,2})[点时](?:(\d{1,2})分?)?'
        match = re.search(time_pattern, time_str)
        
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.group(2) else 0
            
            # 处理上下午
            if '下午' in time_str and hour < 12:
                hour += 12
            elif '晚上' in time_str and hour < 12:
                hour += 12
            
            # 创建目标时间
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # 如果时间已过，推到明天
            if target <= now:
                target += timedelta(days=1)
            
            return target
        
        return None

    async def _parse_with_dateparser(self, time_str: str) -> datetime:
        """使用dateparser库解析"""
        try:
            settings = {
                'TIMEZONE': 'Asia/Shanghai',
                'PREFER_DATES_FROM': 'future',
                'PREFER_DAY_OF_MONTH': 'first',
                'RETURN_AS_TIMEZONE_AWARE': False
            }
            
            parsed_time = dateparser.parse(
                time_str, 
                languages=['zh', 'en'],
                settings=settings
            )
            
            if parsed_time:
                return parsed_time
        except Exception as e:
            logger.debug(f"dateparser解析失败: {e}")
        
        return None

    async def _parse_time_manual(self, time_str: str) -> datetime:
        """手动解析时间字符串（增强版）"""
        now = datetime.now()
        
        # 相对时间解析
        if "后" in time_str:
            # 提取数字
            numbers = re.findall(r'\d+', time_str)
            if numbers:
                value = int(numbers[0])
                
                if "分钟" in time_str:
                    return now + timedelta(minutes=value)
                elif "小时" in time_str:
                    return now + timedelta(hours=value)
                elif "天" in time_str:
                    return now + timedelta(days=value)
                elif "周" in time_str:
                    return now + timedelta(weeks=value)
                elif "月" in time_str:
                    return now + timedelta(days=value * 30)
        
        # 尝试解析标准格式
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M",
            "%m-%d %H:%M",
            "%m月%d日 %H点%M分",
            "%m月%d日 %H点",
            "%H:%M",
            "%H点%M分",
            "%H点"
        ]
        
        for fmt in formats:
            try:
                if "%Y" not in fmt and "%m" not in fmt:
                    # 只有时间，默认今天
                    parsed = datetime.strptime(time_str, fmt)
                    target = now.replace(
                        hour=parsed.hour,
                        minute=parsed.minute if "%M" in fmt else 0,
                        second=0,
                        microsecond=0
                    )
                    if target <= now:
                        target += timedelta(days=1)
                    return target
                else:
                    return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        
        return None

    def _get_next_weekday(self, weekday: int, weeks_ahead: int = 0) -> datetime:
        """获取下一个指定星期的日期"""
        today = datetime.now().date()
        days_ahead = weekday - today.weekday()
        
        if weeks_ahead > 0:
            days_ahead += 7 * weeks_ahead
        elif days_ahead <= 0:  # 如果是今天或之前，推到下周
            days_ahead += 7
        
        return today + timedelta(days=days_ahead)

    def _combine_date_time(self, date, hour: int, time_str: str) -> datetime:
        """组合日期和时间"""
        # 处理分钟
        minute = 0
        minute_match = re.search(r'(\d{1,2})[点时](\d{1,2})分?', time_str)
        if minute_match:
            minute = int(minute_match.group(2))
        
        # 处理上下午
        if '下午' in time_str and hour < 12:
            hour += 12
        elif '晚上' in time_str and hour < 12:
            hour += 12
        
        # 如果是date对象，转换为datetime
        if isinstance(date, datetime):
            result = date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            result = datetime.combine(date, datetime.min.time())
            result = result.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        return result

    async def _schedule_reminder(self, reminder_id: str, reminder_data: Dict):
        """安排提醒任务"""
        try:
            target_time = datetime.fromisoformat(reminder_data['target_time'])
            delay = (target_time - datetime.now()).total_seconds()
            
            if delay > 0:
                # 创建异步任务
                task = asyncio.create_task(self._reminder_task(reminder_id, delay))
                self.running_tasks[reminder_id] = task
                logger.debug(f"安排提醒任务 {reminder_id}，延迟 {delay} 秒")
                
        except Exception as e:
            logger.error(f"安排提醒任务失败: {e}")

    async def _reminder_task(self, reminder_id: str, delay: float):
        """提醒任务"""
        try:
            await asyncio.sleep(delay)
            
            # 检查提醒是否仍然存在且活跃
            if reminder_id in self.reminders and self.reminders[reminder_id].get('active', True):
                reminder_data = self.reminders[reminder_id]
                
                # 发送提醒消息，最多重试3次
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        await self._send_reminder_message(reminder_data)
                        logger.info(f"🎯 提醒任务 {reminder_id} 执行成功")
                        break
                    except Exception as send_error:
                        logger.error(f"❌ 提醒任务 {reminder_id} 发送失败 (尝试 {attempt + 1}/{max_retries}): {send_error}")
                        if attempt < max_retries - 1:
                            # 等待时间递增：30秒、60秒、90秒
                            wait_time = 30 * (attempt + 1)
                            logger.info(f"⏳ 等待 {wait_time} 秒后重试...")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"❌ 提醒任务 {reminder_id} 所有重试均失败")
                            # 可以考虑保存失败的提醒到一个特殊列表中
                
                # 处理重复提醒
                await self._handle_repeat_reminder(reminder_id, reminder_data)
                    
        except asyncio.CancelledError:
            logger.debug(f"⏹️ 提醒任务 {reminder_id} 被取消")
        except Exception as e:
            logger.error(f"❌ 提醒任务执行失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

    async def _send_reminder_message(self, reminder_data: Dict):
        """发送提醒消息（改进版）"""
        try:
            message_content = f"⏰ 提醒：{reminder_data['content']}"
            
            # 获取可用的适配器
            adapter = await self._get_available_adapter()
            if not adapter:
                raise Exception("没有可用的平台适配器")
            
            # 检查适配器状态
            try:
                # 尝试一个简单的API调用来检查连接
                # 这个方法可能需要根据你使用的适配器类型调整
                if hasattr(adapter, 'is_connected'):
                    if not await adapter.is_connected():
                        raise Exception("适配器未连接")
            except Exception as e:
                logger.warning(f"适配器状态检查失败: {e}")
                # 清除缓存，下次重新获取
                self.adapter_cache = None
                adapter = await self._get_available_adapter()
                if not adapter:
                    raise Exception("重新获取适配器失败")
            
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
            
            # 记录详细信息用于调试
            logger.debug(f"准备发送消息: target_type={reminder_data['target_type']}, target_id={reminder_data['target_id']}")
            
            # 使用 host.send_active_message 方法
            try:
                await self.host.send_active_message(
                    adapter=adapter,
                    target_type=reminder_data['target_type'],
                    target_id=reminder_data['target_id'],
                    message=message_chain
                )
                
                logger.info(f"✅ 成功发送提醒给 {reminder_data['sender_id']}: {message_content}")
                
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
                        target_type=reminder_data['target_type'],
                        target_id=reminder_data['target_id'],
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
                # 更准确的月份计算
                if current_time.month == 12:
                    next_time = current_time.replace(year=current_time.year + 1, month=1)
                else:
                    next_time = current_time.replace(month=current_time.month + 1)
            
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

    def __del__(self):
        """插件卸载时取消所有运行中的任务"""
        if hasattr(self, 'reminder_manager'):
            self.reminder_manager.cancel_all_tasks()