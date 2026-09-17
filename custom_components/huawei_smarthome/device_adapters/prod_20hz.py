"""User-contributed protocol for Huawei product 20HZ (达伦吸顶灯).

设备类型: 智能照明 (Lamp), 型号 DL-CLW-1
制造商: 达伦 (DALEN)

核心服务:
   switch.on                    bool RW  开关 (1=开, 0=关)
   brightness.brightness        int  RW  亮度 1-100
   colourMode.mode              enum R   颜色模式(0=彩色,1=单色,2=预置流光,3=自定义流光,4=设备预置模式)
   cct.colorTemperature         int  RW  色温 2000-6000
   lightMode.mode               enum RW  场景推荐
   NightMode.on                 bool RW  小夜灯 (1=开, 0=关)

本适配器暴露:
   1. switch   开关
   2. number   亮度
   3. number   色温
   4. select   灯光模式
   5. switch   小夜灯

投影说明:
   Profile 要求 colourMode 与 lightMode 同时上报。色温与灯光模式仅在
   colourMode 明确为单色 (1) 或设备预置模式 (4) 时才有效；其余取值
   (流光等) 下两者均不代表当前真实输出，此时返回 None 而非回退到旧值。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .api import EntitySpec
from .context import DeviceContext


# ---- 枚举映射 ------------------------------------------------------------

_LIGHT_MODE_OPTIONS = [
    "会客模式", "休闲模式", "观影模式", "用餐模式", "变幻模式",
    "浪漫模式", "工作模式", "睡眠模式", "阅读模式", "清扫模式",
]
_LIGHT_MODE_VALUES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
_MODE_NAME_TO_VAL = dict(zip(_LIGHT_MODE_OPTIONS, _LIGHT_MODE_VALUES))
_MODE_VAL_TO_NAME = dict(zip(_LIGHT_MODE_VALUES, _LIGHT_MODE_OPTIONS))

# colourMode.mode: 0=彩色 1=单色 2=预置流光 3=自定义流光 4=设备预置模式
_COLOUR_MODE_SINGLE = 1
_COLOUR_MODE_DEVICE_PRESET = 4


# ---- 工具函数 ------------------------------------------------------------

def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        try:
            return int(round(float(value.strip())))
        except (TypeError, ValueError):
            return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.casefold() in {"1", "true", "on"}:
            return True
        if value.casefold() in {"0", "false", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def _colour_mode_int(context: DeviceContext) -> int | None:
    """读取 colourMode 投影闸门；未明确上报时返回 None。"""

    if not context.has_service("colourMode"):
        return None
    return _as_int(context.value("colourMode", "mode"))


# ---- 动作函数 ------------------------------------------------------------

async def _turn_on(context: DeviceContext, _data: Mapping[str, Any]) -> None:
    await context.async_send_service("switch", {"on": 1})


async def _turn_off(context: DeviceContext, _data: Mapping[str, Any]) -> None:
    await context.async_send_service("switch", {"on": 0})


async def _set_brightness(context: DeviceContext, data: Mapping[str, Any]) -> None:
    value = _as_int(data.get("value"))
    if value is None:
        return
    value = max(1, min(100, value))
    await context.async_send_service("brightness", {"brightness": value})


async def _set_color_temp(context: DeviceContext, data: Mapping[str, Any]) -> None:
    value = _as_int(data.get("value"))
    if value is None:
        return
    value = max(2000, min(6000, value))
    await context.async_send_service("cct", {"colorTemperature": value})


async def _select_light_mode(context: DeviceContext, data: Mapping[str, Any]) -> None:
    val = _MODE_NAME_TO_VAL.get(str(data.get("option")))
    if val is None:
        raise ValueError(f"unsupported light mode: {data.get('option')}")
    await context.async_send_service("lightMode", {"mode": val})


async def _set_night_mode(context: DeviceContext, data: Mapping[str, Any]) -> None:
    is_on = data.get("is_on")
    if is_on is None:
        return
    await context.async_send_service("NightMode", {"on": 1 if is_on else 0})


# ---- 适配器 --------------------------------------------------------------

class Product20HZAdapter:
    """20HZ 达伦吸顶灯适配器。"""

    prod_id = "20HZ"

    def entities(self, context: DeviceContext) -> tuple[EntitySpec, ...]:
        if context.profile is None or not context.has_service("switch"):
            return ()

        def power_state(device: DeviceContext) -> Mapping[str, Any]:
            return {"is_on": _as_bool(device.value("switch", "on"))}

        def brightness_state(device: DeviceContext) -> Mapping[str, Any]:
            return {"native_value": _as_int(device.value("brightness", "brightness"))}

        def color_temp_state(device: DeviceContext) -> Mapping[str, Any]:
            if _colour_mode_int(device) != _COLOUR_MODE_SINGLE:
                return {"native_value": None}
            return {"native_value": _as_int(device.value("cct", "colorTemperature"))}

        def light_mode_state(device: DeviceContext) -> Mapping[str, Any]:
            if _colour_mode_int(device) != _COLOUR_MODE_DEVICE_PRESET:
                return {"current_option": None}
            val = _as_int(device.value("lightMode", "mode"))
            return {"current_option": _MODE_VAL_TO_NAME.get(val)}

        def night_mode_state(device: DeviceContext) -> Mapping[str, Any]:
            if not device.has_service("NightMode"):
                return {"is_on": None}
            return {"is_on": _as_bool(device.value("NightMode", "on"))}

        return (
            EntitySpec(platform="switch", key="power", name="开关",
                       state=power_state,
                       actions={"turn_on": _turn_on, "turn_off": _turn_off}),
            EntitySpec(platform="number", key="brightness", name="亮度",
                       state=brightness_state,
                       metadata={"min": 1, "max": 100, "step": 1, "unit": "%"},
                       actions={"set_value": _set_brightness}),
            EntitySpec(platform="number", key="color_temp", name="色温",
                       state=color_temp_state,
                       metadata={"min": 2000, "max": 6000, "step": 1, "unit": "K"},
                       actions={"set_value": _set_color_temp}),
            EntitySpec(platform="select", key="light_mode", name="灯光模式",
                       state=light_mode_state,
                       metadata={"options": _LIGHT_MODE_OPTIONS},
                       actions={"select_option": _select_light_mode}),
            EntitySpec(platform="switch", key="night_mode", name="小夜灯",
                       state=night_mode_state,
                       actions={"turn_on": _set_night_mode, "turn_off": _set_night_mode},
                       availability=lambda device: device.has_service("NightMode"),
                       metadata={"icon": "mdi:weather-night"}),
        )


ADAPTER = Product20HZAdapter()