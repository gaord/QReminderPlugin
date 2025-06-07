import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from ..models.reminder import Reminder
from ..utils.time_parser import TimeParser

logger = logging.getLogger(__name__)

class ReminderManager:
    def __init__(self, data_file: str = "reminders.json"):
        self.reminders: Dict[str, Reminder] = {}
        self.data_file = data_file
        self.running_tasks = {}
        self.time_parser = TimeParser()

    async def initialize(self):
        """异步初始化，加载已保存的提醒"""
        await self._load_reminders()
        
        # 恢复所有活跃的提醒任务
        restored_count = 0
        for reminder_id, reminder in self.reminders.items():
            if reminder.active:
                # 检查提醒时间是否还未到
                if reminder.target_time > datetime.now():
                    await self._schedule_reminder(reminder)
                    restored_count += 1
                else:
                    logger.info(f"⏰ 跳过已过期的提醒: {reminder.content}")
        
        logger.info(f"🚀 提醒管理器初始化完成，恢复了 {restored_count} 个活跃提醒任务")

    async def _load_reminders(self):
        """从文件加载提醒数据"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.reminders = {
                        reminder_id: Reminder.from_dict(reminder_data)
                        for reminder_id, reminder_data in data.items()
                    }
        except Exception as e:
            logger.error(f"加载提醒数据失败: {e}")
            self.reminders = {}

    async def _save_reminders(self):
        """保存提醒数据到文件"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(
                    {id: reminder.to_dict() for id, reminder in self.reminders.items()},
                    f,
                    ensure_ascii=False,
                    indent=2
                )
        except Exception as e:
            logger.error(f"保存提醒数据失败: {e}")

    async def create_reminder(
        self,
        sender_id: str,
        target_id: str,
        target_type: str,
        content: str,
        time_description: str,
        repeat_type: str = "不重复"
    ) -> Optional[Reminder]:
        """创建新的提醒"""
        try:
            # 解析时间
            target_time = await self.time_parser.parse_time(time_description)
            if not target_time:
                return None

            # 检查时间是否已过
            if target_time <= datetime.now():
                return None

            # 生成提醒ID
            reminder_id = f"{sender_id}_{int(datetime.now().timestamp())}"
            
            # 创建提醒对象
            reminder = Reminder(
                reminder_id=reminder_id,
                sender_id=sender_id,
                target_id=target_id,
                target_type=target_type,
                content=content,
                target_time=target_time,
                repeat_type=repeat_type
            )

            # 保存提醒
            self.reminders[reminder_id] = reminder
            await self._save_reminders()

            # 安排提醒任务
            await self._schedule_reminder(reminder)

            return reminder

        except Exception as e:
            logger.error(f"创建提醒失败: {e}")
            return None

    async def _schedule_reminder(self, reminder: Reminder):
        """安排提醒任务"""
        try:
            delay = (reminder.target_time - datetime.now()).total_seconds()
            
            if delay > 0:
                # 创建异步任务
                task = asyncio.create_task(self._reminder_task(reminder))
                self.running_tasks[reminder.id] = task
                logger.debug(f"安排提醒任务 {reminder.id}，延迟 {delay} 秒")
                
        except Exception as e:
            logger.error(f"安排提醒任务失败: {e}")

    async def _reminder_task(self, reminder: Reminder):
        """提醒任务"""
        try:
            delay = (reminder.target_time - datetime.now()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            
            # 检查提醒是否仍然存在且活跃
            if reminder.id in self.reminders and self.reminders[reminder.id].active:
                # 处理重复提醒
                await self._handle_repeat_reminder(reminder)
                    
        except asyncio.CancelledError:
            logger.debug(f"⏹️ 提醒任务 {reminder.id} 被取消")
        except Exception as e:
            logger.error(f"❌ 提醒任务执行失败: {e}")

    async def _handle_repeat_reminder(self, reminder: Reminder):
        """处理重复提醒"""
        if reminder.repeat_type == '不重复':
            # 删除一次性提醒
            if reminder.id in self.reminders:
                del self.reminders[reminder.id]
                await self._save_reminders()
                if reminder.id in self.running_tasks:
                    del self.running_tasks[reminder.id]
        else:
            # 计算下次提醒时间
            next_time = None
            
            if reminder.repeat_type == '每天':
                next_time = reminder.target_time + timedelta(days=1)
            elif reminder.repeat_type == '每周':
                next_time = reminder.target_time + timedelta(weeks=1)
            elif reminder.repeat_type == '每月':
                # 更准确的月份计算
                if reminder.target_time.month == 12:
                    next_time = reminder.target_time.replace(year=reminder.target_time.year + 1, month=1)
                else:
                    next_time = reminder.target_time.replace(month=reminder.target_time.month + 1)
            
            if next_time:
                # 更新提醒时间
                reminder.target_time = next_time
                await self._save_reminders()
                
                # 安排下次提醒
                await self._schedule_reminder(reminder)

    def get_user_reminders(self, sender_id: str) -> List[Reminder]:
        """获取用户的所有提醒"""
        return [
            reminder for reminder in self.reminders.values()
            if reminder.sender_id == sender_id
        ]

    async def delete_reminder(self, reminder_id: str) -> bool:
        """删除提醒"""
        try:
            if reminder_id in self.reminders:
                # 取消任务
                if reminder_id in self.running_tasks:
                    self.running_tasks[reminder_id].cancel()
                    del self.running_tasks[reminder_id]
                
                # 删除提醒
                del self.reminders[reminder_id]
                await self._save_reminders()
                return True
            return False
        except Exception as e:
            logger.error(f"删除提醒失败: {e}")
            return False

    async def toggle_reminder(self, reminder_id: str, active: bool) -> bool:
        """切换提醒状态"""
        try:
            if reminder_id in self.reminders:
                reminder = self.reminders[reminder_id]
                if reminder.active != active:
                    reminder.active = active
                    await self._save_reminders()
                    
                    if active:
                        # 恢复提醒
                        await self._schedule_reminder(reminder)
                    else:
                        # 暂停提醒
                        if reminder_id in self.running_tasks:
                            self.running_tasks[reminder_id].cancel()
                            del self.running_tasks[reminder_id]
                    return True
            return False
        except Exception as e:
            logger.error(f"切换提醒状态失败: {e}")
            return False 