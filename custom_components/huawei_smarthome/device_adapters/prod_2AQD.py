"""User-contributed protocol for Huawei product 2AQD (达伦自然光吸顶灯 Z3 Pro).

设备类型: 智能照明 (01B)，达伦 Z3 Pro 自然光吸顶灯，WiFi 直连。
Profile: https://smarthome-drcn.dbankcdn.com/device/guide/2AQD/2AQD.json

Profile 共 9 个服务，其中 5 个带写权限。本适配器暴露 6 个实体：

    1. light         主灯       switch.on + brightness.brightness + cct.colorTemperature
    2. switch        自然光     natural.on
    3. switch        夜灯       nightmode.on
    4. select        场景模式   lightMode.mode (10 个预设)
    5. select        配光       lampswitch.switch (全亮/区域1/区域2)
    6. sensor        当前灯光模式 colourMode.mode (只读，枚举文本)

██ 未适配项与理由（按项目"失败关闭"原则，无证据不出实体） ██

1. colourMode = 2 (预置流光) / 3 (自定义流光)
   本 Profile 没有任何 colour / colourset 服务，既没有 RGB 写通道，
   也拿不到流光场景色板。据此无法构造完整、正确的彩色控制实体。
   Profile 原文明确说明流光模式下 brightness 与 colourset 中的亮度语义不同
   （"主界面的亮度只显示brightness中的亮度，不显示colourset中的亮度"），
   在没有 colourset 字段的情况下强行映射亮度会产生错误状态。

2. colourMode.mode 权限为 "GR"，即只读。本 Profile 未声明它的写权限。
   本适配器把 colourMode 作为只读 sensor 暴露，并且——
   见下方"待真机验证项"——对 colourMode 的下发是唯一一处超出 Profile 权限声明的
   写入。如果真机验证否定该做法，删除 _set_colour_mode 及其两个调用点即可回到
   纯只读语义。

3. lightMode 与 colourMode 存在双向耦合（Profile 原文：
   "colourMode和lightMode需要同时上报"）。本适配器要求 colourMode 明确上报为
   4(设备预置模式) 时，场景 select 才回读当前值；其余情况一律返回 None，
   避免 HA 显示一个并不生效的场景名。

4. update 服务：action 权限仅 "P"（只写），version/progress/introduction
   仅 "GR"（只读），升级动作需要按 检查版本 -> 读 version -> 启动升级 的多步
   序列。本项目不对 OTA 做封装，升级走华为智慧生活 App。

5. netInfo 服务（信号强度/IP/SSID/BSSID/RSSI）为纯诊断信息，
   与 2FAT 等现有适配器的处理保持一致——不映射为实体。

6. quickmenu 中的 "{brightness/brightness%1}%" 是 App 侧展示公式，不是线协议。

██ 待真机验证项（未验证前请勿依赖） ██

- 【唯一超出 Profile 权限声明的写入】_set_colour_mode() 会向 colourMode 写入
  mode 值。依据是 Profile 的字段语义说明（colourMode 与 lightMode 需要同时上报，
  且 cct/场景的选择依赖当前 colourMode），但 colourMode 的 method 是 "R"。
  上线前必须在真机上确认该写入被接受；若被拒绝，整个函数连同两个调用点一起删除，
  并接受"场景写入可能不生效、cct 写入可能被忽略"的降级行为。

- lightMode 写入是否会立即触发 switch/brightness/cct 的 deviceDataChanged 上报
  （Profile 声称"需立即上报给APP"），未验证前不依赖该行为做状态推断。

- 场景预设生效后 colourMode 是否稳定上报为 4。若不是，scene select 的当前值
  将始终为 None —— 这是保守但仍可用的降级，不会显示错误场景名。

- 多 sid 写入顺序：light 的 turn_on 采用 switch -> brightness -> colourMode -> cct，
  场景写入采用 colourMode -> lightMode。依据是 Profile 字段语义（先通电再调参、
  先切模式再选场景）。两处顺序均标注为 order: unknown，未取得真机捕获前不做断言。

- brightness 的 int 量化采用与 prod_100z.py 一致的 round-half-away-from-zero，
  因此 HA 50 会映射到设备 63（回读 126）而非 62。属既定换算约定，非缺陷。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .api import EntitySpec
from .context import DeviceContext

_LIGHT_HA_BRIGHTNESS_MAX = 255
_BRIGHTNESS_FIELD = "brightness"
_CCT_FIELD = "colorTemperature"

_COLOUR_MODE_MULTI = 0
_COLOUR_MODE_SINGLE = 1
_COLOUR_MODE_PRESET_FLOW = 2
_COLOUR_MODE_CUSTOM_FLOW = 3
_COLOUR_MODE_DEVICE_PRESET = 4

_COLOUR_MODE_LABELS: dict[int, str] = {
    _COLOUR_MODE_MULTI: "彩色",
    _COLOUR_MODE_SINGLE: "单色",
    _COLOUR_MODE_PRESET_FLOW: "预置流光",
    _COLOUR_MODE_CUSTOM_FLOW: "自定义流光",
    _COLOUR_MODE_DEVICE_PRESET: "设备预置模式",
}

_MODE_SWITCH_ON = 1
_MODE_SWITCH_OFF = 0


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


def _profile_range(field: Mapping[str, Any]) -> tuple[float, float] | None:
    minimum = _number(field.get("min"))
    maximum = _number(field.get("max"))
    if minimum is None or maximum is None or maximum <= minimum:
        return None
    return float(minimum), float(maximum)


def _profile_step(field: Mapping[str, Any]) -> float | None:
    step = _number(field.get("step"))
    if step is None or step <= 0:
        return None
    return float(step)


def _enum_options(field: Mapping[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    """Return ``(label, raw_value)`` pairs in Profile order."""

    options: list[tuple[str, Any]] = []
    if field is None:
        return ()
    for option in field.get("enumList", ()):
        if not isinstance(option, Mapping):
            continue
        raw = option.get("enumVal")
        label = option.get("descCh") or option.get("descEn") or raw
        options.append((str(label), raw))
    return tuple(options)


def _coerce_profile_value(value: Any, field: Mapping[str, Any] | None) -> Any:
    """Encode a Profile value using its declared characteristic type."""

    data_type = str((field or {}).get("characteristicType") or "").casefold()
    if data_type in {"int", "integer", "enum"}:
        number = _number(value)
        if number is not None:
            return int(number) if float(number).is_integer() else number
    if data_type in {"float", "double", "number"}:
        number = _number(value)
        if number is not None:
            return float(number)
    if not data_type:
        number = _number(value)
        if number is not None:
            return number
    return value


def _enum_payload(value: Any, field: Mapping[str, Any] | None) -> Any:
    return _coerce_profile_value(value, field)


def _device_brightness_to_ha(value: Any, field: Mapping[str, Any]) -> int | None:
    """Convert 2AQD brightness (1..100) to HA's 0..255 scale."""

    number = _number(value)
    value_range = _profile_range(field)
    if number is None or value_range is None:
        return None
    minimum, maximum = value_range
    number = min(max(float(number), minimum), maximum)
    return round((number - minimum) * _LIGHT_HA_BRIGHTNESS_MAX / (maximum - minimum))


def _ha_brightness_to_device(value: Any, field: Mapping[str, Any]) -> int | float:
    """Convert HA's 0..255 brightness to the 2AQD Profile range."""

    number = _number(value)
    value_range = _profile_range(field)
    if number is None or value_range is None:
        raise ValueError("2AQD brightness range is missing from the Profile")
    minimum, maximum = value_range
    number = min(max(float(number), 0.0), float(_LIGHT_HA_BRIGHTNESS_MAX))
    device_value = minimum + number * (maximum - minimum) / _LIGHT_HA_BRIGHTNESS_MAX
    step = _profile_step(field)
    if step is not None:
        device_value = minimum + round((device_value - minimum) / step) * step
    if (field.get("characteristicType") or "").casefold() in {"int", "integer"}:
        return int(round(device_value))
    return device_value


def _colour_mode_value(context: DeviceContext) -> int | None:
    value = _number(context.value("colourMode", "mode"))
    return int(value) if value is not None else None


def _light_mode_value(context: DeviceContext) -> int | None:
    value = _number(context.value("lightMode", "mode"))
    return int(value) if value is not None else None


def _cct_kelvin(context: DeviceContext) -> int | None:
    """2AQD reports cct.colorTemperature directly in kelvin (2800..6000)."""

    value = _number(context.value("cct", _CCT_FIELD))
    field = _field(context.profile or {}, "cct", _CCT_FIELD)
    if value is None or field is None:
        return None
    value_range = _profile_range(field)
    if value_range is not None:
        value = min(max(float(value), value_range[0]), value_range[1])
    return int(round(float(value)))


async def _set_colour_mode(context: DeviceContext, value: int) -> None:
    """Select the 2AQD colour context before writing cct or a scene.

    Required by the Profile contract: "colourMode和lightMode需要同时上报" — the
    device needs colourMode driven to the matching projection, otherwise a cct
    write can be accepted while the lamp keeps rendering 流光 / 场景 output.

    Unverified against a real device: this writer exists so the sequence is
    explicit and reviewable.  If runtime capture shows the cloud contract does
    not need it, delete this function and its two call sites.
    """

    if not context.has_service("colourMode"):
        return
    field = _field(context.profile or {}, "colourMode", "mode")
    await context.async_send_service(
        "colourMode",
        {"mode": _enum_payload(value, field)},
    )


def _light_projection_mode(context: DeviceContext) -> str | None:
    """Return the active colour projection, never guessing stale cache state.

    2AQD has no RGB write channel in its Profile, so only "color_temp" can be
    projected. Mode 4 (设备预置模式) means a lightMode scene is driving the
    output, and modes 2/3 (流光) are unsupported — in all three cases the
    projector is not the CCT channel.
    """

    colour_mode = _colour_mode_value(context)
    if colour_mode == _COLOUR_MODE_SINGLE:
        return "color_temp"
    # An unknown colourMode gives no evidence about the active projection, so
    # report none rather than displaying a possibly-stale CCT value.
    return None


def _light_state(context: DeviceContext) -> Mapping[str, Any]:
    """Read-only state snapshot.

    Home Assistant ignores read-only keys in a `light` entity state (writes go
    through the EntitySpec actions), so the supported_* / min_* / max_* keys
    below are kept here as the canonical declaration that the test harness and
    any future HA version can rely on.
    """

    brightness_field = _field(context.profile or {}, "brightness", _BRIGHTNESS_FIELD) or {}
    cct_field = _field(context.profile or {}, "cct", _CCT_FIELD) or {}
    projection_mode = _light_projection_mode(context)
    brightness = _device_brightness_to_ha(
        context.value("brightness", _BRIGHTNESS_FIELD),
        brightness_field,
    )
    return {
        "is_on": _bool(context.value("switch", "on")),
        "brightness": brightness,
        # Floor at 1 so a non-zero HA brightness never writes the Profile min
        # when the caller actually meant "on, dimmest".
        "ha_brightness_min": 0 if not brightness else 1,
        "color_temp_kelvin": (
            _cct_kelvin(context) if projection_mode == "color_temp" else None
        ),
        "color_mode": projection_mode,
        "supported_color_modes": {"color_temp"},
        "min_color_temp_kelvin": cct_field.get("min"),
        "max_color_temp_kelvin": cct_field.get("max"),
    }


async def _light_turn_on(context: DeviceContext, data: Mapping[str, Any]) -> None:
    # Order is switch -> brightness -> cct / colourMode, derived from field
    # semantics (power before parameters).  Not yet confirmed against a runtime
    # capture, so it stays explicit here rather than being implied.
    await context.async_send_service("switch", {"on": _MODE_SWITCH_ON})
    if data.get("brightness") is not None:
        brightness_field = _field(
            context.profile or {},
            "brightness",
            _BRIGHTNESS_FIELD,
        )
        if brightness_field is None:
            raise ValueError("2AQD brightness field is missing from the Profile")
        await context.async_send_service(
            "brightness",
            {
                _BRIGHTNESS_FIELD: _ha_brightness_to_device(
                    data["brightness"],
                    brightness_field,
                )
            },
        )
    if data.get("color_temp_kelvin") is not None:
        await _set_colour_mode(context, _COLOUR_MODE_SINGLE)
        await context.async_send_service(
            "cct",
            {_CCT_FIELD: int(round(float(data["color_temp_kelvin"])))},
        )


async def _light_turn_off(context: DeviceContext, _data: Mapping[str, Any]) -> None:
    await context.async_send_service("switch", {"on": _MODE_SWITCH_OFF})


def _flag_state(sid: str) -> Any:
    def reader(context: DeviceContext) -> Mapping[str, Any]:
        return {"is_on": _bool(context.value(sid, "on")) is True}

    return reader


def _flag_action(sid: str, on_value: int) -> Any:
    async def action(context: DeviceContext, _data: Mapping[str, Any]) -> None:
        await context.async_send_service(sid, {"on": on_value})

    return action


def _light_mode_options(context: DeviceContext) -> tuple[tuple[str, Any], ...]:
    field = _field(context.profile or {}, "lightMode", "mode")
    return _enum_options(field)


def _light_mode_state(context: DeviceContext) -> Mapping[str, Any]:
    """Return the active scene, but only when a scene is actually driving output.

    Requires a positively reported ``colourMode == 4``.  A cached ``lightMode``
    value is never trusted on its own: the Profile warns that the device keeps
    reporting both fields together, so an absent colourMode means the projection
    is unknown, not that a scene is active.
    """

    if _colour_mode_value(context) != _COLOUR_MODE_DEVICE_PRESET:
        # 彩色 / 单色 are cct projections, 预置流光 / 自定义流光 have their own
        # colour source, and an unknown colourMode is no evidence at all.
        return {"current_option": None}
    value = _light_mode_value(context)
    if value is None:
        return {"current_option": None}
    options = _light_mode_options(context)
    label = next(
        (text for text, raw in options if str(raw) == str(value)),
        None,
    )
    return {"current_option": label}


async def _select_light_mode(context: DeviceContext, data: Mapping[str, Any]) -> None:
    option = str(data.get("option"))
    field = _field(context.profile or {}, "lightMode", "mode")
    for label, raw in _enum_options(field):
        if label == option:
            await _set_colour_mode(context, _COLOUR_MODE_DEVICE_PRESET)
            await context.async_send_service(
                "lightMode",
                {"mode": _enum_payload(raw, field)},
            )
            return
    raise ValueError(f"unknown light mode: {option}")


def _lampswitch_state(context: DeviceContext) -> Mapping[str, Any]:
    value = _number(context.value("lampswitch", "switch"))
    if value is None:
        return {"current_option": None}
    query = str(int(value)) if float(value).is_integer() else str(value)
    field = _field(context.profile or {}, "lampswitch", "switch")
    for label, raw in _enum_options(field):
        if str(raw) == query:
            return {"current_option": label}
    return {"current_option": None}


async def _select_lampswitch(context: DeviceContext, data: Mapping[str, Any]) -> None:
    option = str(data.get("option"))
    field = _field(context.profile or {}, "lampswitch", "switch")
    for label, raw in _enum_options(field):
        if label == option:
            await context.async_send_service(
                "lampswitch",
                {"switch": _enum_payload(raw, field)},
            )
            return
    raise ValueError(f"unknown lamp switch option: {option}")


def _colour_mode_state(context: DeviceContext) -> Mapping[str, Any]:
    value = _colour_mode_value(context)
    if value is None:
        return {"state": "未知"}
    return {"state": _COLOUR_MODE_LABELS.get(value, str(value))}


class Product2AQDAdapter:
    """Keep all 2AQD entity and command choices in this file."""

    prod_id = "2AQD"

    def entities(self, context: DeviceContext) -> tuple[EntitySpec, ...]:
        profile = context.profile
        if profile is None or not all(
            context.has_service(sid)
            for sid in ("switch", "brightness", "cct")
        ):
            return ()

        cct_field = _field(profile, "cct", _CCT_FIELD) or {}

        entities: list[EntitySpec] = [
            EntitySpec(
                platform="light",
                key="light",
                name=None,
                state=_light_state,
                metadata={
                    "supported_color_modes": {"color_temp"},
                    "min_color_temp_kelvin": cct_field.get("min"),
                    "max_color_temp_kelvin": cct_field.get("max"),
                },
                actions={
                    "turn_on": _light_turn_on,
                    "turn_off": _light_turn_off,
                },
            )
        ]

        if context.has_service("natural"):
            entities.append(
                EntitySpec(
                    platform="switch",
                    key="natural_light",
                    name="自然光",
                    state=_flag_state("natural"),
                    actions={
                        "turn_on": _flag_action("natural", _MODE_SWITCH_ON),
                        "turn_off": _flag_action("natural", _MODE_SWITCH_OFF),
                    },
                )
            )

        if context.has_service("nightmode"):
            entities.append(
                EntitySpec(
                    platform="switch",
                    key="night_mode",
                    name="夜灯",
                    state=_flag_state("nightmode"),
                    actions={
                        "turn_on": _flag_action("nightmode", _MODE_SWITCH_ON),
                        "turn_off": _flag_action("nightmode", _MODE_SWITCH_OFF),
                    },
                )
            )

        if context.has_service("lightMode"):
            options = _light_mode_options(context)
            entities.append(
                EntitySpec(
                    platform="select",
                    key="scene_mode",
                    name="场景模式",
                    state=_light_mode_state,
                    metadata={"options": tuple(label for label, _ in options)},
                    actions={"select_option": _select_light_mode},
                )
            )

        if context.has_service("lampswitch"):
            options = _enum_options(_field(profile, "lampswitch", "switch"))
            entities.append(
                EntitySpec(
                    platform="select",
                    key="lamp_switch",
                    name="配光",
                    state=_lampswitch_state,
                    metadata={"options": tuple(label for label, _ in options)},
                    actions={"select_option": _select_lampswitch},
                )
            )

        if context.has_service("colourMode"):
            entities.append(
                EntitySpec(
                    platform="sensor",
                    key="colour_mode",
                    name="当前灯光模式",
                    state=_colour_mode_state,
                )
            )

        del cct_field
        return tuple(entities)


ADAPTER = Product2AQDAdapter()
