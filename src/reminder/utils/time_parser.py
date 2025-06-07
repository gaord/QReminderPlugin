import re
from datetime import datetime, timedelta
import dateparser
import logging

logger = logging.getLogger(__name__)

class TimeParser:
    def __init__(self):
        self.weekday_map = {
            '周一': '星期一', '周二': '星期二', '周三': '星期三',
            '周四': '星期四', '周五': '星期五', '周六': '星期六',
            '周日': '星期日', '周天': '星期日', '礼拜': '星期',
            '这周': '本周', '这个周': '本周', '这星期': '本周'
        }
        
        self.time_map = {
            '早上': '上午', '早晨': '上午', '中午': '12点',
            '下午': '下午', '傍晚': '下午6点', '晚上': '晚上',
            '夜里': '晚上', '凌晨': '凌晨'
        }
        
        self.chinese_nums = {
            '零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
            '五': '5', '六': '6', '七': '7', '八': '8', '九': '9',
            '十': '10', '十一': '11', '十二': '12'
        }

    async def parse_time(self, time_str: str) -> datetime:
        """增强的自然语言时间解析"""
        try:
            logger.debug(f"开始解析时间: '{time_str}'")
            
            # 预处理时间字符串
            processed_time = await self._preprocess_time_string(time_str)
            logger.debug(f"预处理后: '{processed_time}'")
            
            # 尝试多种解析策略
            parsers = [
                self._parse_weekday_time,
                self._parse_relative_days,
                self._parse_specific_time,
                self._parse_with_dateparser,
                self._parse_time_manual
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
        for old, new in self.weekday_map.items():
            time_str = time_str.replace(old, new)
        
        # 统一时间表达
        for old, new in self.time_map.items():
            time_str = time_str.replace(old, new)
        
        # 转换中文数字为阿拉伯数字
        for cn, num in self.chinese_nums.items():
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