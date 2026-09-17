"""User-contributed protocol for Huawei product 2P3N (720智能空气净化器3s增强版).

Profile: https://smarthome-drcn.dbankcdn.com/device/guide/2P3N/2P3N.json
设备型号 KJ520F-E520, deviceTypeId 013 (Air Cleaner), WiFi 直连.

写命令证据全部来自官方 App H5 插件 (smarthome-drcn.dbankcdn.com
/device/guide/2P3N/h5_001/, main bundle + chunk 0/1/6/7), 每个
``$store.dispatch("setDevInfo", {sid:{field:value}})`` 的 dispatch 点:

    controlAppliance   -> {switch:{on:1-N}}            电源开关
    autoClick          -> {mode:{mode:1}}              自动
    sleepClick         -> {mode:{mode:2}}              睡眠
    smartClick         -> {mode:{mode:5}}              节能
    onGearChange       -> {fan:{gear:A+1}}             风速档位 1..5
    autoFanClick       -> {fan:{autoFanSwitch:N}}      自动风速
    uvClick            -> {UVSwitch:{on:N}}            UV 杀菌
    anionClick         -> {anionSwitch:{on:N}}         负离子
    screenClick        -> {screenSwitch:{on:N}}        屏幕开关
    switchRes(lock)    -> {childLockSwitch:{on:N}}     童锁
    openBeep           -> {keyToneSwitch:{on:N}}       按键音
    setMonitoringSw.   -> {monitoringSwitch:{on:N}}    关机监测
    getItemVal         -> {lightSwitch:{lightSwitch:0}}(关) 或
                          {ambientLight:{level:A}}     屏幕亮度 0低/1中/2强
    ecoSet             -> {ecoSetting:{autoSet:N}} / {ecoSetting:{standbySet:N}}
                          1优/2良/3差
    ChangeFilter 确认  -> {filterElement1:{reset:1}}   滤芯复位 (chunk 7)

读换算 (实机动态数据 + H5 渲染交叉验证):
    pm25.current         原样, µg/m³
    hcho.current         /1000 -> mg/m³  (H5: (this.hcho/1e3).toFixed(2))
    temperature.current  原样, ℃
    humidity.current     原样, %RH
    cav.current          *1000 -> m³ 累计净化空气量 (H5: Math.round(1e3*cav))
    cmw.weight           原样, mg 累计过滤颗粒物
    airQuality.level     1优/2良/3差
    workStatus.status    0已关闭/1已开启/2待机中
    filterElement1.leftPercentage  %
    faultDetection.code  0正常/100仓门盖未关闭/101滤芯寿命剩余10/102耗尽...
    faultDetection.status 0运行正常/1运行异常

模式语义: mode 0手动(档位直控) / 1自动 / 2睡眠 / 5节能; HA fan 的
preset_mode 暴露 自动/睡眠/节能, 手动档由 percentage (gear 1..5 ->
20..100%) 表达. 百分比/预设写入前设备若为关, 先发 switch.on=1 再发
目标值 (H5 在关机态禁用这些按钮, 组合顺序无直接捕获, 属待验证项).

不暴露 (失败关闭 / 无 HA 对应):
    humidity.level       Profile 枚举(优/良/差)与 H5 schema(干燥/略干/
                         舒适/潮湿)冲突, 语义无法裁决.
    pm25.level/hcho.level 与 airQuality.level 重复且前者无独立验证.
    lightSwitch 的读语义 (0 是否唯一表示关) 无完整证据, 仅按 H5 写路径
    建模: 0=关, 非 0 按 ambientLight.level 显示.
    timer/delay          全对象 CRUD + UTC 时间戳, HA 自动化可覆盖.
    update/netInfo/timeZone/diagnose/capabilitySet/ctlCapability/
    ecoSetting.workStatus/outdoorPM25.cityCode  诊断或 App 侧配置.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .api import EntitySpec
from .context import DeviceContext

_GEAR_MIN = 1
_GEAR_MAX = 5
_PRESET_LABELS = ("自动", "睡眠", "节能")
_BRIGHTNESS_OFF = "关"


def _service(profile: Mapping[str, Any], sid: str) -> Mapping[str, Any] | None:
    for service in profile.get("services", ()):
        if isinstance(service, Mapping) and service.get("serviceId") == sid:
            return service
    return None


def _field(
    profile: Mapping[str, Any],
    sid: str,
    name: str,
) -> Mapping[str, Any] | None:
    service = _service(profile, sid)
    if service is None:
        return None
    for field in service.get("characteristics", ()):
        if isinstance(field, Mapping) and field.get("characteristicName") == name:
            return field
    return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


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


def _enum_labels(field: Mapping[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for option in field.get("enumList", ()) or ():
        if not isinstance(option, Mapping):
            continue
        key = option.get("enumVal")
        if key is None:
            continue
        labels[str(key)] = str(option.get("descCh") or option.get("enumVal"))
    return labels


def _enum_key(value: Any) -> str | None:
    number = _number(value)
    return str(int(number)) if number is not None else (
        value if isinstance(value, str) else None
    )


def _enum_label(field: Mapping[str, Any], value: Any) -> str | None:
    """Map a raw characteristic value to its Profile enum label."""

    if value is None or isinstance(value, bool):
        return None
    return _enum_labels(field).get(_enum_key(value) or "")


def _enum_payload(label: str, field: Mapping[str, Any]) -> int | str | None:
    """Convert a display label back to its wire value (int when numeric)."""

    for key, text in _enum_labels(field).items():
        if text == label:
            try:
                return int(float(key))
            except (TypeError, ValueError):
                return key
    return None


def _flag_state(sid: str, field_name: str):
    def state(device: DeviceContext) -> Mapping[str, Any]:
        return {"is_on": _bool(device.value(sid, field_name))}

    return state


def _flag_action(sid: str, field_name: str, on_value: int):
    """Pre-bind the written value: switch platforms invoke actions with data={}."""

    async def action(context: DeviceContext, data: Mapping[str, Any]) -> None:
        del data
        await context.async_send_service(sid, {field_name: on_value})

    return action


def _make_switch(
    context: DeviceContext,
    profile: Mapping[str, Any],
    sid: str,
    field_name: str,
    key: str,
    name: str,
) -> EntitySpec | None:
    if not context.has_service(sid):
        return None
    if _field(profile, sid, field_name) is None:
        return None
    return EntitySpec(
        platform="switch",
        key=key,
        name=name,
        state=_flag_state(sid, field_name),
        metadata={},
        actions={
            "turn_on": _flag_action(sid, field_name, 1),
            "turn_off": _flag_action(sid, field_name, 0),
        },
    )


def _make_enum_select(
    context: DeviceContext,
    profile: Mapping[str, Any],
    sid: str,
    field_name: str,
    key: str,
    name: str,
) -> EntitySpec | None:
    """Shared builder for one enum characteristic exposed as a select."""

    if not context.has_service(sid):
        return None
    field = _field(profile, sid, field_name)
    if field is None:
        return None
    labels = _enum_labels(field)
    options = tuple(
        labels[str(option.get("enumVal"))]
        for option in field.get("enumList", ()) or ()
        if isinstance(option, Mapping)
        and option.get("enumVal") is not None
        and str(option.get("enumVal")) in labels
    )
    if not options:
        return None

    def state(device: DeviceContext) -> Mapping[str, Any]:
        return {"current_option": _enum_label(field, device.value(sid, field_name))}

    async def select_option(
        context: DeviceContext,
        data: Mapping[str, Any],
        field: Mapping[str, Any] = field,
        field_name: str = field_name,
    ) -> None:
        value = _enum_payload(str(data.get("option") or ""), field)
        if value is None:
            raise ValueError(
                f"2P3N unknown {field_name} option: {data.get('option')!r}"
            )
        await context.async_send_service(sid, {field_name: value})

    return EntitySpec(
        platform="select",
        key=key,
        name=name,
        state=state,
        metadata={"options": options},
        actions={"select_option": select_option},
    )


def _make_sensor(
    context: DeviceContext,
    profile: Mapping[str, Any],
    sid: str,
    field_name: str,
    key: str,
    name: str,
    unit: str | None = None,
    device_class: str | None = None,
    state_class: str | None = None,
    scale: float | None = None,
) -> EntitySpec | None:
    if not context.has_service(sid):
        return None
    if _field(profile, sid, field_name) is None:
        return None

    def state(device: DeviceContext) -> Mapping[str, Any]:
        number = _number(device.value(sid, field_name))
        if number is None:
            return {"native_value": None}
        if scale is not None:
            return {"native_value": round(number * scale, 3)}
        return {"native_value": number}

    metadata: dict[str, Any] = {}
    if unit:
        metadata["unit"] = unit
    if device_class:
        metadata["device_class"] = device_class
    if state_class:
        metadata["state_class"] = state_class
    return EntitySpec(
        platform="sensor",
        key=key,
        name=name,
        state=state,
        metadata=metadata,
    )


def _is_on(device: DeviceContext) -> bool:
    return bool(_bool(device.value("switch", "on")))


def _gear_to_percentage(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    number = min(max(number, _GEAR_MIN), _GEAR_MAX)
    return round(number * 100 / _GEAR_MAX)


def _percentage_to_gear(value: Any) -> int:
    number = _number(value)
    if number is None:
        return _GEAR_MIN
    number = min(max(float(number), 0.0), 100.0)
    return min(_GEAR_MAX, max(_GEAR_MIN, math.ceil(number * _GEAR_MAX / 100)))


def _preset_value(mode_field: Mapping[str, Any] | None) -> dict[str, int]:
    """Map preset labels to wire values using the Profile mode enum."""

    if mode_field is None:
        return {}
    labels = _enum_labels(mode_field)
    values: dict[str, int] = {}
    for label in _PRESET_LABELS:
        for key, text in labels.items():
            if text == label:
                number = _number(key)
                if number is not None:
                    values[label] = int(number)
    return values


def _mode_preset(mode_field: Mapping[str, Any] | None, value: Any) -> str | None:
    """Map the current mode to a fan preset label; 手动 returns None."""

    if mode_field is None:
        return None
    label = _enum_label(mode_field, value)
    return label if label in _PRESET_LABELS else None


class Product2P3NAdapter:
    """Keep all 2P3N entity and command choices in this file."""

    prod_id = "2P3N"

    def entities(self, context: DeviceContext) -> tuple[EntitySpec, ...]:
        profile = context.profile
        if profile is None or not context.has_service("switch"):
            return ()
        entities: list[EntitySpec] = [self._fan_entity(context, profile)]
        entities.extend(self._switch_entities(context, profile))
        entities.extend(self._sensor_entities(context, profile))
        entities.extend(self._select_entities(context, profile))
        entities.extend(self._filter_entities(context, profile))
        return tuple(entity for entity in entities if entity is not None)

    # ---------------------------------------------------------------- fan

    def _fan_entity(
        self,
        context: DeviceContext,
        profile: Mapping[str, Any],
    ) -> EntitySpec:
        mode_field = _field(profile, "mode", "mode")
        preset_values = _preset_value(mode_field)
        gear_field = _field(profile, "fan", "gear")

        def fan_state(device: DeviceContext) -> Mapping[str, Any]:
            return {
                "is_on": _bool(device.value("switch", "on")),
                "percentage": _gear_to_percentage(device.value("fan", "gear")),
                "preset_mode": _mode_preset(mode_field, device.value("mode", "mode")),
            }

        async def ensure_on(context: DeviceContext) -> None:
            if not _is_on(context):
                await context.async_send_service("switch", {"on": 1})

        async def write_gear(
            context: DeviceContext,
            percentage: Any,
        ) -> None:
            number = _number(percentage)
            if number is None:
                raise ValueError(f"2P3N invalid fan percentage: {percentage!r}")
            if number <= 0:
                await context.async_send_service("switch", {"on": 0})
                return
            gear = _percentage_to_gear(number)
            if gear_field is not None:
                # Keep the written gear inside the Profile enum range.
                gear = min(
                    max(
                        gear,
                        _number(gear_field.get("min")) or _GEAR_MIN,
                    ),
                    _number(gear_field.get("max")) or _GEAR_MAX,
                )
            await context.async_send_service("fan", {"gear": gear})

        async def write_mode(
            context: DeviceContext,
            preset: Any,
        ) -> None:
            value = preset_values.get(str(preset or ""))
            if value is None:
                raise ValueError(f"2P3N unknown preset mode: {preset!r}")
            await context.async_send_service("mode", {"mode": value})

        async def turn_on(
            context: DeviceContext,
            data: Mapping[str, Any],
        ) -> None:
            # Write the power switch once: the inner writers must not re-send
            # it, because the local state only updates after the device
            # reports back (repeat same-value writes can be rejected).
            if not _is_on(context):
                await context.async_send_service("switch", {"on": 1})
            if data.get("percentage") is not None:
                await write_gear(context, data["percentage"])
            if data.get("preset_mode") is not None:
                await write_mode(context, data["preset_mode"])

        async def turn_off(
            context: DeviceContext,
            data: Mapping[str, Any],
        ) -> None:
            del data
            await context.async_send_service("switch", {"on": 0})

        async def set_percentage(
            context: DeviceContext,
            data: Mapping[str, Any],
        ) -> None:
            await ensure_on(context)
            await write_gear(context, data.get("percentage"))

        async def set_preset_mode(
            context: DeviceContext,
            data: Mapping[str, Any],
        ) -> None:
            await ensure_on(context)
            await write_mode(context, data.get("preset_mode"))

        return EntitySpec(
            platform="fan",
            key="purifier",
            name=None,
            state=fan_state,
            metadata={
                "supports_percentage": True,
                "percentage_step": round(100 / _GEAR_MAX),
                "preset_modes": tuple(
                    label for label in _PRESET_LABELS if label in preset_values
                ),
            },
            actions={
                "turn_on": turn_on,
                "turn_off": turn_off,
                "set_percentage": set_percentage,
                "set_preset_mode": set_preset_mode,
            },
        )

    # ------------------------------------------------------------- switch

    def _switch_entities(
        self,
        context: DeviceContext,
        profile: Mapping[str, Any],
    ) -> tuple[EntitySpec | None, ...]:
        return (
            _make_switch(context, profile, "childLockSwitch", "on", "child_lock", "童锁"),
            _make_switch(context, profile, "keyToneSwitch", "on", "key_tone", "按键音"),
            _make_switch(context, profile, "UVSwitch", "on", "uv", "UV杀菌"),
            _make_switch(context, profile, "anionSwitch", "on", "anion", "负离子"),
            _make_switch(context, profile, "screenSwitch", "on", "screen", "屏幕"),
            _make_switch(
                context, profile, "monitoringSwitch", "on", "monitoring", "关机监测"
            ),
            _make_switch(
                context, profile, "fan", "autoFanSwitch", "auto_fan", "自动风速"
            ),
        )

    # ------------------------------------------------------------- sensor

    def _sensor_entities(
        self,
        context: DeviceContext,
        profile: Mapping[str, Any],
    ) -> tuple[EntitySpec | None, ...]:
        entities: list[EntitySpec | None] = [
            _make_sensor(
                context, profile, "pm25", "current", "pm25", "室内PM2.5",
                unit="µg/m³", device_class="pm25", state_class="measurement",
            ),
            _make_sensor(
                context, profile, "hcho", "current", "hcho", "甲醛",
                unit="mg/m³", state_class="measurement", scale=0.001,
            ),
            _make_sensor(
                context, profile, "temperature", "current", "temperature", "温度",
                unit="°C", device_class="temperature", state_class="measurement",
            ),
            _make_sensor(
                context, profile, "humidity", "current", "humidity", "湿度",
                unit="%", device_class="humidity", state_class="measurement",
            ),
            _make_sensor(
                context, profile, "outdoorPM25", "current", "outdoor_pm25", "室外PM2.5",
                unit="µg/m³", device_class="pm25", state_class="measurement",
            ),
            _make_sensor(
                context, profile, "cav", "current", "clean_air_volume", "累计净化空气量",
                unit="m³", state_class="total_increasing", scale=1000,
            ),
            _make_sensor(
                context, profile, "cmw", "weight", "filtered_particles", "累计过滤颗粒物",
                unit="mg", state_class="total_increasing",
            ),
        ]
        # 枚举型传感器: 空气质量 / 工作状态 / 故障码.
        if context.has_service("airQuality"):
            level_field = _field(profile, "airQuality", "level")

            def air_state(device: DeviceContext) -> Mapping[str, Any]:
                return {
                    "native_value": _enum_label(
                        level_field or {}, device.value("airQuality", "level")
                    )
                }

            entities.append(
                EntitySpec(
                    platform="sensor",
                    key="air_quality",
                    name="空气质量",
                    state=air_state,
                )
            )
        if context.has_service("workStatus"):
            work_field = _field(profile, "workStatus", "status")

            def work_state(
                device: DeviceContext,
                field: Mapping[str, Any] | None = work_field,
            ) -> Mapping[str, Any]:
                return {
                    "native_value": _enum_label(
                        field or {}, device.value("workStatus", "status")
                    )
                }

            entities.append(
                EntitySpec(
                    platform="sensor",
                    key="work_status",
                    name="工作状态",
                    state=work_state,
                )
            )
        if context.has_service("faultDetection"):
            code_field = _field(profile, "faultDetection", "code")

            def fault_state(
                device: DeviceContext,
                field: Mapping[str, Any] | None = code_field,
            ) -> Mapping[str, Any]:
                return {
                    "native_value": _enum_label(
                        field or {}, device.value("faultDetection", "code")
                    )
                }

            def fault_on(device: DeviceContext) -> Mapping[str, Any]:
                status = _number(device.value("faultDetection", "status"))
                return {"is_on": status is not None and status != 0}

            entities.extend(
                (
                    EntitySpec(
                        platform="sensor",
                        key="fault",
                        name="故障",
                        state=fault_state,
                    ),
                    EntitySpec(
                        platform="binary_sensor",
                        key="fault_status",
                        name="运行异常",
                        state=fault_on,
                        metadata={"device_class": "problem"},
                    ),
                )
            )
        return tuple(entities)

    # ------------------------------------------------------------- select

    def _select_entities(
        self,
        context: DeviceContext,
        profile: Mapping[str, Any],
    ) -> tuple[EntitySpec | None, ...]:
        entities: list[EntitySpec | None] = [
            self._screen_brightness_spec(context, profile),
            _make_enum_select(
                context, profile, "ecoSetting", "autoSet",
                "eco_auto_sensitivity", "自动模式灵敏度",
            ),
            _make_enum_select(
                context, profile, "ecoSetting", "standbySet",
                "eco_standby_sensitivity", "待机灵敏度",
            ),
        ]
        return tuple(entities)

    def _screen_brightness_spec(
        self,
        context: DeviceContext,
        profile: Mapping[str, Any],
    ) -> EntitySpec | None:
        """屏幕亮度: 关 -> lightSwitch.lightSwitch=0, 低/中/强 -> ambientLight.level."""

        if not (
            context.has_service("ambientLight") and context.has_service("lightSwitch")
        ):
            return None
        level_field = _field(profile, "ambientLight", "level")
        if level_field is None or _field(profile, "lightSwitch", "lightSwitch") is None:
            return None
        labels = _enum_labels(level_field)
        options = (_BRIGHTNESS_OFF,) + tuple(
            labels[str(option.get("enumVal"))]
            for option in level_field.get("enumList", ()) or ()
            if isinstance(option, Mapping)
            and option.get("enumVal") is not None
            and str(option.get("enumVal")) in labels
        )

        def state(device: DeviceContext) -> Mapping[str, Any]:
            light = _number(device.value("lightSwitch", "lightSwitch"))
            if light is not None and light == 0:
                return {"current_option": _BRIGHTNESS_OFF}
            level = device.value("ambientLight", "level")
            return {"current_option": _enum_label(level_field, level)}

        async def select_option(
            context: DeviceContext,
            data: Mapping[str, Any],
        ) -> None:
            option = str(data.get("option") or "")
            if option == _BRIGHTNESS_OFF:
                await context.async_send_service(
                    "lightSwitch", {"lightSwitch": 0}
                )
                return
            value = _enum_payload(option, level_field)
            if value is None:
                raise ValueError(f"2P3N unknown brightness option: {option!r}")
            await context.async_send_service("ambientLight", {"level": value})

        return EntitySpec(
            platform="select",
            key="screen_brightness",
            name="屏幕亮度",
            state=state,
            metadata={"options": options},
            actions={"select_option": select_option},
        )

    # ------------------------------------------------------------- filter

    def _filter_entities(
        self,
        context: DeviceContext,
        profile: Mapping[str, Any],
    ) -> tuple[EntitySpec | None, ...]:
        if not context.has_service("filterElement1"):
            return ()
        alarm_field = _field(profile, "filterElement1", "alarm")
        entities: list[EntitySpec | None] = [
            _make_sensor(
                context, profile, "filterElement1", "leftPercentage",
                "filter_life", "滤芯寿命",
                unit="%", state_class="measurement",
            )
        ]
        if alarm_field is not None:

            def alarm_state(device: DeviceContext) -> Mapping[str, Any]:
                alarm = _number(device.value("filterElement1", "alarm"))
                return {"is_on": alarm is not None and alarm != 0}

            entities.append(
                EntitySpec(
                    platform="binary_sensor",
                    key="filter_alarm",
                    name="滤芯告警",
                    state=alarm_state,
                    metadata={"device_class": "problem"},
                )
            )
        if _field(profile, "filterElement1", "reset") is not None:

            async def reset_filter(
                context: DeviceContext,
                data: Mapping[str, Any],
            ) -> None:
                del data
                await context.async_send_service("filterElement1", {"reset": 1})

            entities.append(
                EntitySpec(
                    platform="button",
                    key="filter_reset",
                    name="滤芯复位",
                    state=lambda device: {},
                    metadata={},
                    actions={"press": reset_filter},
                )
            )
        return tuple(entities)


ADAPTER = Product2P3NAdapter()
