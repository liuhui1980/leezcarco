"""
北京时间（UTC+8）时间转换工具
将所有时间转换为北京时间显示
"""

from datetime import datetime, timezone, timedelta
import pytz

# 定义时区
UTC = timezone.utc
BEIJING_TZ = pytz.timezone('Asia/Shanghai')  # 中国标准时间（UTC+8）

def to_beijing_time(dt, from_tz=UTC):
    """
    将时间转换为北京时间
    :param dt: datetime对象或时间字符串
    :param from_tz: 原始时区，默认UTC
    :return: 北京时间字符串 (YYYY-MM-DD HH:MM:SS)
    """
    if dt is None:
        return None
    
    # 如果是字符串，先解析
    if isinstance(dt, str):
        try:
            # 尝试多种格式
            formats = [
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d %H:%M',
                '%Y-%m-%d',
                '%H:%M:%S',
                '%H:%M'
            ]
            for fmt in formats:
                try:
                    dt_obj = datetime.strptime(dt, fmt)
                    break
                except ValueError:
                    continue
            else:
                # 如果无法解析，返回原字符串
                return dt
        except Exception:
            return dt
    else:
        dt_obj = dt
    
    # 确保有时区信息
    if dt_obj.tzinfo is None:
        # 如果是UTC时区对象，使用replace方法
        if from_tz == UTC:
            dt_obj = dt_obj.replace(tzinfo=UTC)
        else:
            # 对于pytz时区，使用localize
            try:
                dt_obj = from_tz.localize(dt_obj)
            except AttributeError:
                # 如果不是pytz时区，使用replace
                dt_obj = dt_obj.replace(tzinfo=from_tz)
    
    # 转换为北京时间
    beijing_time = dt_obj.astimezone(BEIJING_TZ)
    
    # 返回格式化的字符串
    return beijing_time.strftime('%Y-%m-%d %H:%M:%S')

def to_beijing_time_short(dt, from_tz=UTC):
    """
    转换为简短的北京时间格式
    :return: 北京时间字符串 (MM-DD HH:MM)
    """
    beijing_str = to_beijing_time(dt, from_tz)
    if beijing_str and len(beijing_str) >= 16:
        return beijing_str[5:16]  # 提取 MM-DD HH:MM
    return beijing_str

def to_beijing_time_only(dt, from_tz=UTC):
    """
    只转换时间部分（不含日期）
    :return: 时间字符串 (HH:MM:SS)
    """
    beijing_str = to_beijing_time(dt, from_tz)
    if beijing_str and len(beijing_str) >= 8:
        return beijing_str[11:]  # 提取 HH:MM:SS
    return beijing_str

def current_beijing_time():
    """
    获取当前北京时间
    :return: 北京时间字符串 (YYYY-MM-DD HH:MM:SS)
    """
    now_utc = datetime.now(UTC)
    return to_beijing_time(now_utc)

def current_beijing_time_short():
    """
    获取当前简短的北京时间
    :return: 北京时间字符串 (MM-DD HH:MM)
    """
    now_utc = datetime.now(UTC)
    return to_beijing_time_short(now_utc)

def format_duration(start_time, end_time=None):
    """
    计算并格式化时长（自动转换为北京时间计算）
    :param start_time: 开始时间
    :param end_time: 结束时间，None表示使用当前时间
    :return: 格式化后的时长字符串
    """
    try:
        # 转换为datetime对象
        if isinstance(start_time, str):
            start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
        else:
            start_dt = start_time
        
        if end_time is None:
            end_dt = datetime.now()
        elif isinstance(end_time, str):
            end_dt = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
        else:
            end_dt = end_time
        
        # 计算时长
        duration = end_dt - start_dt
        total_seconds = int(duration.total_seconds())
        
        # 格式化
        if total_seconds < 60:
            return f"{total_seconds}秒"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes}分{seconds}秒"
        else:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"{hours}时{minutes}分{seconds}秒"
    except Exception:
        return "0秒"

def get_beijing_weekday(dt=None):
    """
    获取北京时间的星期几
    :param dt: 时间，None表示当前时间
    :return: 星期几（0=周一, 6=周日）
    """
    if dt is None:
        dt = current_beijing_time()
    
    if isinstance(dt, str):
        dt = datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
    
    return dt.weekday()  # 0=周一, 1=周二, ..., 6=周日

def get_beijing_hour(dt=None):
    """
    获取北京时间的小时数
    :param dt: 时间，None表示当前时间
    :return: 小时（0-23）
    """
    if dt is None:
        dt = current_beijing_time()
    
    if isinstance(dt, str):
        dt = datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
    
    return dt.hour

# 测试代码
if __name__ == '__main__':
    print("当前北京时间:", current_beijing_time())
    print("当前简短格式:", current_beijing_time_short())
    
    test_time = "2026-03-30 15:30:00"
    print(f"转换测试 ({test_time}):", to_beijing_time(test_time))
    print(f"只显示时间:", to_beijing_time_only(test_time))
    
    # 测试时长计算
    start = "2026-03-30 14:00:00"
    end = "2026-03-30 15:30:00"
    print(f"时长计算 ({start} - {end}):", format_duration(start, end))