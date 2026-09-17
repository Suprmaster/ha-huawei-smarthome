"""User-contributed protocol for Huawei product 2NMZ (贝昂 落地式循环扇).

Profile:
    https://smarthome-drcn.dbankcdn.com/device/guide/2NMZ/2NMZ.json

设备类型: 风扇 (Fan) — 型号 FZS2-SF3 Pro，WiFi 直连，固件 1.3.0 双机实测

核心服务:
    switch.on           bool   RW (0=关, 1=开)          风扇开关
    neIon.neIon         bool   RW (0=关, 1=开)          负离子
    lock.lock           bool   RW (0=关, 1=开)          童锁
    lightSwitch.lightSwitch bool RW (0=关, 1=开)        灯光
    temperature.current int    RO (-50~80, 单位 ℃)      当前温度
    fan.gear            enum   RW (1~12)                风速档位
    fan.sweepVer        enum   RW (0=关, 1=开)          垂直扫风
    fan.LARangle        enum   RW (0/60/90/120)         左右旋转角度
    mode.mode           enum   RW (0=正常,1=睡眠,2=自然,3=AI) 模式
    track.Track         enum   RW (0=关,1=单人,2=多人)   人形追踪
    schedule.ScheduleOn int    RW (0~12, 单位 小时)      定时开启
    delayOff.DelayOff   int    RW (0~12, 单位 小时)      延时关闭

刻意不映射的服务:
    netInfo / diagnose   实机上报为空对象 {}，没有可读状态
    update               OTA；固件版本已在设备信息里展示
    timeZone / ctlCapability / timer / delay
                         不属于本产品 Profile，且没有可暴露的读写行为

本适配器暴露:
    1. switch 风扇开关
    2. switch 负离子
    3. switch 童锁
    4. switch 灯光
    5. sensor 当前温度 (°C)
    6. select 风速档位
    7. select 垂直扫风
    8. select 左右旋转角度
    9. select 模式
    10. select 人形追踪
    11. number 定时开启 (h)
    12. number 延时关闭 (h)

约定:
    * 选项文案与档位边界一律优先取自 Profile enumList / min / max / step，
      Profile 取不到时才用内置兜底值，保证 Profile 缺失时实体仍在。
    * 未上报的服务返回 None（HA 显示 unknown），不谎报具体数值。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .api import EntitySpec
from .context import DeviceContext


def _service(profile: Mapping[str, Any], sid: str) -> Mapping[str, Any] | None:
    """Return one service definition from the Profile."""

    for service in profile.get("services", ()):
        if isinstance(service, Mapping) and service.get("serviceId") == sid:
            return service
    return None


def _field(
    profile: Mapping[str, Any],
    sid: str,
    name: str,
) -> Mapping[str, Any] | None:
    """Return one characteristic definition from the Profile."""

    service = _service(profile, sid)
    if service is None:
        return None
    for field in service.get("characteristics", ()):
        if isinstance(field, Mapping) and field.get("characteristicName") == name:
            return field
    return None


def _options(
    profile: Mapping[str, Any],
    sid: str,
    name: str,
    fallback: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Return (label, raw value) pairs, preferring the Profile declaration."""

    field = _field(profile, sid, name)
    options = tuple(
        (str(option.get("descCh") or option.get("enumVal")), str(option.get("enumVal")))
        for option in (field or {}).get("enumList", ())
        if isinstance(option, Mapping)
    )
    return options or fallback


def _bool(value: Any) -> bool | None:
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


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _raw_for(value: Any) -> Any:
    """Return the Profile wire value, keeping ints as ints."""

    number = _number(value)
    if number is None:
        return value
    return int(number) if number.is_integer() else number


def _bool_state(sid: str, name: str):
    def state(context: DeviceContext) -> Mapping[str, Any]:
        return {"is_on": _bool(context.value(sid, name))}

    return state


def _bool_action(sid: str, name: str, target: int):
    async def action(context: DeviceContext, _data: Mapping[str, Any]) -> None:
        await context.async_send_service(sid, {name: target})

    return action


def _select_state(sid: str, name: str, options: tuple[tuple[str, str], ...]):
    labels = {raw: label for label, raw in options}

    def state(context: DeviceContext) -> Mapping[str, Any]:
        value = context.value(sid, name)
        if value is None:
            return {"current_option": None}
        return {"current_option": labels.get(str(value), str(value))}

    return state


def _select_action(sid: str, name: str, options: tuple[tuple[str, str], ...], field: Mapping[str, Any] | None):
    values = {label: raw for label, raw in options}

    async def action(context: DeviceContext, data: Mapping[str, Any]) -> None:
        option = data.get("option")
        raw = values.get(str(option))
        if raw is None:
            return
        data_type = str((field or {}).get("characteristicType") or "").casefold()
        if data_type == "enum":
            number = _number(raw)
            await context.async_send_service(sid, {name: int(number) if number is not None else raw})
            return
        await context.async_send_service(sid, {name: raw})

    return action


def _numeric_state(sid: str, name: str):
    def state(context: DeviceContext) -> Mapping[str, Any]:
        return {"native_value": _number(context.value(sid, name))}

    return state


def _hours_action(sid: str, name: str, maximum: float):
    async def action(context: DeviceContext, data: Mapping[str, Any]) -> None:
        value = _number(data.get("value"))
        if value is None:
            return
        hours = int(min(max(value, 0), maximum))
        await context.async_send_service(sid, {name: hours})

    return action


class Product2NMZAdapter:
    """Keep all 2NMZ entity and command choices in this file."""

    prod_id = "2NMZ"

    def entities(self, context: DeviceContext) -> tuple[EntitySpec, ...]:
        profile = context.profile or {}
        entities: list[EntitySpec] = []

        # --- binary read/write switches ------------------------------------
        for sid, key, name, attr in (
            ("switch", "switch", "风扇开关", "on"),
            ("neIon", "neIon", "负离子", "neIon"),
            ("lock", "lock", "童锁", "lock"),
            ("lightSwitch", "lightSwitch", "灯光", "lightSwitch"),
        ):
            if not context.has_service(sid):
                continue
            entities.append(
                EntitySpec(
                    platform="switch",
                    key=key,
                    name=name,
                    state=_bool_state(sid, attr),
                    actions={
                        "turn_on": _bool_action(sid, attr, 1),
                        "turn_off": _bool_action(sid, attr, 0),
                    },
                )
            )

        # --- temperature ---------------------------------------------------
        if context.has_service("temperature"):
            entities.append(
                EntitySpec(
                    platform="sensor",
                    key="temperature",
                    name="当前温度",
                    state=_numeric_state("temperature", "current"),
                    metadata={
                        "unit": "°C",
                        "device_class": "temperature",
                        "state_class": "measurement",
                    },
                )
            )

        # --- enumerated controls -------------------------------------------
        # Every option label comes from the Profile; the fallback keeps the
        # entities alive if the Profile is unavailable.
        for sid, attr, key, name, field_name, fallback in (
            (
                "fan",
                "gear",
                "fan.gear",
                "风速档位",
                "gear",
                tuple((f"{index}档", str(index)) for index in range(1, 13)),
            ),
            (
                "fan",
                "sweepVer",
                "fan.sweepVer",
                "垂直扫风",
                "sweepVer",
                (("关闭", "0"), ("开启", "1")),
            ),
            (
                "fan",
                "LARangle",
                "fan.LARangle",
                "左右旋转角度",
                "LARangle",
                (("60°角度", "60"), ("90°角度", "90"), ("120°角度", "120"), ("固定", "0")),
            ),
            (
                "mode",
                "mode",
                "mode.mode",
                "模式",
                "mode",
                (("正常风", "0"), ("睡眠风", "1"), ("自然风", "2"), ("AI风", "3")),
            ),
            (
                "track",
                "Track",
                "track",
                "人形追踪",
                "Track",
                (("关闭", "0"), ("单人", "1"), ("多人", "2")),
            ),
        ):
            if not context.has_service(sid):
                continue
            options = _options(profile, sid, field_name, fallback)
            field = _field(profile, sid, field_name)
            entities.append(
                EntitySpec(
                    platform="select",
                    key=key,
                    name=name,
                    state=_select_state(sid, attr, options),
                    metadata={"options": tuple(label for label, _ in options)},
                    actions={"select_option": _select_action(sid, attr, options, field)},
                )
            )

        # --- hour timers ----------------------------------------------------
        # Profile: min=0, max=12, step=1, unit declared as "小时".
        for sid, key, name, attr in (
            ("schedule", "schedule", "定时开启", "ScheduleOn"),
            ("delayOff", "delayOff", "延时关闭", "DelayOff"),
        ):
            if not context.has_service(sid):
                continue
            field = _field(profile, sid, attr) or {}
            maximum = _number(field.get("max")) or 12
            entities.append(
                EntitySpec(
                    platform="number",
                    key=key,
                    name=name,
                    state=_numeric_state(sid, attr),
                    metadata={"min": 0, "max": maximum, "step": 1, "unit": "h"},
                    actions={"set_value": _hours_action(sid, attr, maximum)},
                )
            )

        return tuple(entities)


ADAPTER = Product2NMZAdapter()
