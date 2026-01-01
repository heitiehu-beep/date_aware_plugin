from typing import List, Tuple, Type, Any, Dict, Optional
from datetime import datetime, timedelta
import aiohttp
import json
import os

from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseAction,
    BaseCommand,
    BaseTool,
    BaseEventHandler,
    ComponentInfo,
    ActionActivationType,
    ConfigField,
    EventType,
    MaiMessages,
    CustomEventHandlerResult,
)
from src.common.logger import get_logger
from src.plugin_system.apis import llm_api

logger = get_logger("date_aware_plugin")

# LLM 扩展提示词（无换行）
LLM_EXPAND_PROMPT = (
    "你是一个日期信息助手。将以下日期信息整理成自然语言。原始信息: {raw_info}。"
    "输出时必须包含昨天今天明天三天的日期、星期几和节假日。调休工作日需特别说明。"
    "直接输出内容，不要JSON。"
)

# 节假日数据 URL 模板
HOLIDAY_URL_TEMPLATE = "https://unpkg.com/holiday-calendar@1.3.0/data/CN/{year}.json"

# 内置备用节假日（固定节日）
FIXED_HOLIDAYS = {
    "01-01": "元旦",
    "02-14": "情人节",
    "03-08": "妇女节",
    "04-01": "愚人节",
    "05-01": "劳动节",
    "05-04": "青年节",
    "06-01": "儿童节",
    "07-01": "建党节",
    "08-01": "建军节",
    "09-10": "教师节",
    "10-01": "国庆节",
    "12-25": "圣诞节",
}

# 缓存目录
CACHE_DIR = "data/holidays"


def get_weekday_cn(date: datetime) -> str:
    """获取中文星期几"""
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return weekdays[date.weekday()]


def format_date_short(date: datetime) -> str:
    """格式化为 '1月2日' 格式"""
    return f"{date.month}月{date.day}日"


def get_holiday_name(date_str: str, holiday_map: Dict[str, Any]) -> str:
    """从缓存中获取节假日名称"""
    if date_str in holiday_map:
        info = holiday_map[date_str]
        name = info.get("name_cn", "")
        holiday_type = info.get("type", "")
        if holiday_type == "transfer_workday":
            return f"{name}（调休）"
        return name
    # 备用：检查固定节日
    month_day = date_str[5:]  # 格式: "01-01"
    return FIXED_HOLIDAYS.get(month_day, "")


async def download_holiday_data(year: int) -> Dict[str, Any]:
    """下载指定年份的节假日数据"""
    url = HOLIDAY_URL_TEMPLATE.format(year=year)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    # 转换为字典格式，方便查询
                    holiday_map = {}
                    for item in data.get("dates", []):
                        holiday_map[item["date"]] = item
                    return holiday_map
                else:
                    logger.warning(f"下载节假日数据失败: {response.status}")
                    return {}
    except Exception as e:
        logger.error(f"下载节假日数据出错: {e}")
        return {}


def load_cached_holiday(year: int) -> Dict[str, Any]:
    """从本地缓存加载节假日数据"""
    cache_file = os.path.join(CACHE_DIR, f"{year}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载缓存节假日数据失败: {e}")
    return {}


def save_cached_holiday(year: int, data: Dict[str, Any]) -> None:
    """保存节假日数据到本地缓存"""
    cache_file = os.path.join(CACHE_DIR, f"{year}.json")
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存节假日数据失败: {e}")


async def get_holiday_map(year: int) -> Dict[str, Any]:
    """获取节假日数据（优先本地缓存，无则下载）"""
    # 先尝试加载缓存
    holiday_map = load_cached_holiday(year)
    if holiday_map:
        return holiday_map
    
    # 下载并缓存
    holiday_map = await download_holiday_data(year)
    if holiday_map:
        save_cached_holiday(year, holiday_map)
    
    return holiday_map


def get_three_days_raw_info() -> Dict[str, Dict[str, str]]:
    """获取昨天、今天、明天的基础信息（不含节假日）"""
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    
    return {
        "yesterday": {
            "date_str": yesterday.strftime("%Y-%m-%d"),
            "date_short": format_date_short(yesterday),
            "weekday": get_weekday_cn(yesterday),
        },
        "today": {
            "date_str": today.strftime("%Y-%m-%d"),
            "date_short": format_date_short(today),
            "weekday": get_weekday_cn(today),
        },
        "tomorrow": {
            "date_str": tomorrow.strftime("%Y-%m-%d"),
            "date_short": format_date_short(tomorrow),
            "weekday": get_weekday_cn(tomorrow),
        },
    }


async def get_three_days_info() -> str:
    """获取三天完整信息，格式化为字符串"""
    today = datetime.now()
    year = today.year
    
    # 获取节假日数据
    holiday_map = await get_holiday_map(year)
    
    # 获取基础信息
    base_info = get_three_days_raw_info()
    
    # 构建输出（竖线分隔格式，更清晰）
    lines = []
    for day_name, info in [("昨天", base_info["yesterday"]), ("今天", base_info["today"]), ("明天", base_info["tomorrow"])]:
        date_str = info["date_str"]
        holiday = get_holiday_name(date_str, holiday_map)
        if holiday:
            line = f"{day_name} | {info['date_short']} {info['weekday']}【{holiday}】"
        else:
            line = f"{day_name} | {info['date_short']} {info['weekday']}"
        lines.append(line)
    
    return "\n".join(lines)


async def expand_with_llm(raw_info: str) -> str:
    """使用 LLM 扩展日期信息"""
    try:
        prompt = LLM_EXPAND_PROMPT.format(raw_info=raw_info)
        models = llm_api.get_available_models()
        chat_model_config = models.get("replyer")
        if not chat_model_config:
            logger.warning("无可用的 LLM 模型")
            return raw_info
        
        success, result, _, _ = await llm_api.generate_with_model(
            prompt, model_config=chat_model_config, request_type="date_expand"
        )
        if success and result:
            return result.strip()
        return raw_info
    except Exception as e:
        logger.error(f"LLM 扩展日期信息失败: {e}")
        return raw_info


class DateTool(BaseTool):
    """获取日期信息的工具"""

    name = "get_date_info"
    description = "获取昨天、今天、明天的日期、星期几和节假日信息。LLM 可根据需要调用此工具。"
    parameters = []
    available_for_llm = True

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行获取日期信息"""
        try:
            info = await get_three_days_info()
            return {
                "content": info,
                "description": "日期信息已获取",
            }
        except Exception as e:
            logger.error(f"获取日期信息失败: {e}")
            return {"content": "", "error": str(e)}


class TodayInfoAction(BaseAction):
    """自动注入日期信息的 Action"""

    action_name = "inject_date_context"
    action_description = "自动获取并注入日期信息到对话上下文中，让 Bot 感知当前日期"
    activation_type = ActionActivationType.ALWAYS

    action_parameters = {}
    action_require = ["每次对话前触发", "用于让 Bot 感知当前日期"]
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行注入日期信息"""
        try:
            # 获取原始日期信息
            raw_info = await get_three_days_info()

            # 检查是否需要 LLM 扩展
            if self.get_config("date.enable_llm_expand", False):
                expanded_info = await expand_with_llm(raw_info)
            else:
                expanded_info = raw_info

            # 发送到上下文（修改系统提示词）
            # 注意：这里通过发送消息的方式间接实现，因为 Action 组件的限制
            await self.send_text(f"[日期信息注入]{expanded_info}[/日期信息注入]")

            return True, "日期信息已注入"
        except Exception as e:
            logger.error(f"注入日期信息失败: {e}")
            return False, f"注入失败: {e}"


class DateInjectEventHandler(BaseEventHandler):
    """日期注入事件处理器 - 在 LLM 调用前自动注入日期信息到 prompt"""

    event_type = EventType.POST_LLM
    handler_name = "date_inject_handler"
    handler_description = "在 LLM 调用前自动注入日期信息到 prompt"
    weight = 10
    intercept_message = True

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """执行日期注入

        在 LLM 调用前，将日期信息注入到 prompt 中，让 Bot 感知当前日期。
        这是一个"软注入"方式 - 将日期信息作为上下文附加，不强制 Bot 使用。
        """
        if not message or not message.llm_prompt:
            return True, True, None, None, None

        try:
            # 获取日期信息
            date_info = await get_three_days_info()

            # 构建注入内容
            inject_content = f"\n\n【日期】\n{date_info}\n\n提示：以上是当前日期信息，可以根据需要融入回复中。"

            # 直接修改 LLM prompt（软注入方式）
            new_prompt = message.llm_prompt + inject_content
            message.modify_llm_prompt(new_prompt, suppress_warning=True)

            logger.debug(f"日期信息已注入到 prompt")
            return True, True, None, None, message

        except Exception as e:
            logger.error(f"日期注入失败: {e}")
            return True, True, None, None, None


class DateCommand(BaseCommand):
    """手动查询日期命令"""

    command_name = "date_query"
    command_description = "查询昨天、今天、明天的日期信息，包括星期几和节假日"
    command_pattern = r"^/date$"

    async def execute(self) -> Tuple[bool, str, bool]:
        """执行日期查询"""
        try:
            # 获取原始日期信息
            raw_info = await get_three_days_info()
            
            # 检查是否需要 LLM 扩展
            if self.get_config("date.enable_llm_expand", False):
                expanded_info = await expand_with_llm(raw_info)
                message = expanded_info
            else:
                message = raw_info
            
            await self.send_text(message)
            
            return True, f"显示了日期信息: {message}", True
        except Exception as e:
            logger.error(f"日期查询失败: {e}")
            await self.send_text("查询日期信息失败，请稍后再试")
            return True, f"查询失败: {e}", True


@register_plugin
class DateAwarePlugin(BasePlugin):
    """日期感知插件 - 让 Bot 能够感知并展示日期信息"""

    plugin_name: str = "date_aware_plugin"
    enable_plugin: bool = True
    dependencies: list[str] = []
    python_dependencies: list[str] = []
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "plugin": "插件配置",
        "date": "日期功能配置",
    }

    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="1.0.0", description="配置文件版本"),
        },
        "date": {
            "enable_llm_expand": ConfigField(type=bool, default=False, description="是否启用 LLM 扩展日期信息"),
            "llm_model": ConfigField(type=str, default="replyer", description="使用的模型名称"),
            "enable_action": ConfigField(type=bool, default=True, description="是否启用自动注入 Action"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""
        components = []

        # 添加 Tool 组件
        components.append((DateTool.get_tool_info(), DateTool))

        # 根据配置添加 Action 组件
        if self.get_config("date.enable_action", True):
            components.append((TodayInfoAction.get_action_info(), TodayInfoAction))

        # 添加 EventHandler 组件（自动注入日期到 prompt）
        components.append((DateInjectEventHandler.get_handler_info(), DateInjectEventHandler))

        # 添加 Command 组件
        components.append((DateCommand.get_command_info(), DateCommand))

        return components