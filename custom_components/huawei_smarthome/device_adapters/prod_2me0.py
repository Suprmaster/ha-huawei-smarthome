"""奥科 PLC 版开合帘电机时尚款（产品 2ME0）适配器。

设备类型/型号: 开合帘 / AM130-1.2/100-EM-P
核心服务:
  opener.current/target: int 0~100%，0=全关，100=全开
  action.action: enum，0=关、1=开、2=暂停
  switchSpeed.action: enum，0=低速、1=标准、2=快速
  changeDirection.action: enum，0=改变电机转向
  placeMemory.code/action: 限位状态、设置限位(0)、删除限位(1)
  commonFaultDetection.status/code: 故障标志与故障码
本适配器暴露: 窗帘、开合速度、设备转向、限位操作、限位状态和故障状态。

Profile 与厂商 H5 均直接使用上述 sid、字段和值；H5 仅显示 action 的
0/1/2，故不发送复合动作 3/4/5。OTA 和未在 H5 中展示的网络信息不暴露。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .api import EntitySpec
from .context import DeviceContext


def _field(
    profile: Mapping[str, Any], sid: str, name: str
) -> Mapping[str, Any] | None:
    for service in profile.get("services", ()):
        if not isinstance(service, Mapping) or service.get("serviceId") != sid:
            continue
        for characteristic in service.get("characteristics", ()):
            if (
                isinstance(characteristic, Mapping)
                and characteristic.get("characteristicName") == name
            ):
                return characteristic
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(round(number))


def _enum_labels(field: Mapping[str, Any]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for option in field.get("enumList", ()) or ():
        if not isinstance(option, Mapping):
            continue
        raw_value = _integer(option.get("enumVal"))
        if raw_value is None:
            continue
        labels[raw_value] = str(option.get("descCh") or raw_value)
    return labels


def _enum_state(field: Mapping[str, Any], value: Any) -> str | None:
    raw_value = _integer(value)
    return None if raw_value is None else _enum_labels(field).get(raw_value)


def _empty_state(_context: DeviceContext) -> Mapping[str, Any]:
    return {}


class Product2ME0Adapter:
    """2ME0 奥科开合帘电机适配器。"""

    prod_id = "2ME0"

    def entities(self, context: DeviceContext) -> tuple[EntitySpec, ...]:
        profile = context.profile
        if profile is None:
            return ()

        entities: list[EntitySpec] = []

        if (
            context.has_service("opener")
            and context.has_service("action")
            and _field(profile, "opener", "target") is not None
        ):

            def cover_state(device: DeviceContext) -> Mapping[str, Any]:
                current = _integer(device.value("opener", "current"))
                position = (
                    None if current is None else min(max(current, 0), 100)
                )
                return {
                    "current_position": position,
                    "is_closed": None if position is None else position == 0,
                }

            async def open_cover(
                device: DeviceContext, _data: Mapping[str, Any]
            ) -> None:
                await device.async_send_service("action", {"action": 1})

            async def close_cover(
                device: DeviceContext, _data: Mapping[str, Any]
            ) -> None:
                await device.async_send_service("action", {"action": 0})

            async def stop_cover(
                device: DeviceContext, _data: Mapping[str, Any]
            ) -> None:
                await device.async_send_service("action", {"action": 2})

            async def set_position(
                device: DeviceContext, data: Mapping[str, Any]
            ) -> None:
                position = _integer(data.get("position"))
                if position is None:
                    raise ValueError(
                        f"2ME0 invalid curtain position: {data.get('position')!r}"
                    )
                await device.async_send_service(
                    "opener", {"target": min(max(position, 0), 100)}
                )

            entities.append(
                EntitySpec(
                    platform="cover",
                    key="curtain",
                    name=None,
                    state=cover_state,
                    actions={
                        "open": open_cover,
                        "close": close_cover,
                        "stop": stop_cover,
                        "set_position": set_position,
                    },
                )
            )

        speed_field = _field(profile, "switchSpeed", "action")
        if context.has_service("switchSpeed") and speed_field is not None:
            speed_labels = _enum_labels(speed_field)
            speed_values = {label: raw for raw, label in speed_labels.items()}

            def speed_state(device: DeviceContext) -> Mapping[str, Any]:
                return {
                    "current_option": _enum_state(
                        speed_field, device.value("switchSpeed", "action")
                    )
                }

            async def set_speed(
                device: DeviceContext, data: Mapping[str, Any]
            ) -> None:
                option = data.get("option")
                if option not in speed_values:
                    raise ValueError(f"2ME0 unknown speed option: {option!r}")
                await device.async_send_service(
                    "switchSpeed", {"action": speed_values[option]}
                )

            entities.append(
                EntitySpec(
                    platform="select",
                    key="speed",
                    name="开合速度",
                    state=speed_state,
                    metadata={"options": tuple(speed_values)},
                    actions={"select_option": set_speed},
                )
            )

        for sid, value, key, name in (
            ("changeDirection", 0, "change_direction", "设备转向"),
            ("placeMemory", 0, "set_limit", "设置限位"),
            ("placeMemory", 1, "delete_limit", "删除限位"),
        ):
            if not context.has_service(sid) or _field(profile, sid, "action") is None:
                continue

            async def press(
                device: DeviceContext,
                _data: Mapping[str, Any],
                target_sid: str = sid,
                target_value: int = value,
            ) -> None:
                await device.async_send_service(
                    target_sid, {"action": target_value}
                )

            entities.append(
                EntitySpec(
                    platform="button",
                    key=key,
                    name=name,
                    state=_empty_state,
                    actions={"press": press},
                )
            )

        fault_code_field = _field(profile, "commonFaultDetection", "code")
        if (
            context.has_service("commonFaultDetection")
            and fault_code_field is not None
        ):
            entities.append(
                EntitySpec(
                    platform="sensor",
                    key="fault_state",
                    name="故障状态",
                    state=lambda device: {
                        "native_value": _enum_state(
                            fault_code_field,
                            device.value("commonFaultDetection", "code"),
                        )
                    },
                )
            )

        if context.has_service("commonFaultDetection") and _field(
            profile, "commonFaultDetection", "status"
        ) is not None:

            def fault_status(device: DeviceContext) -> Mapping[str, Any]:
                status = _integer(
                    device.value("commonFaultDetection", "status")
                )
                return {
                    "is_on": status == 1 if status in (0, 1) else None
                }

            entities.append(
                EntitySpec(
                    platform="binary_sensor",
                    key="fault_problem",
                    name="故障告警",
                    state=fault_status,
                    metadata={"device_class": "problem"},
                )
            )

        limit_field = _field(profile, "placeMemory", "code")
        if context.has_service("placeMemory") and limit_field is not None:
            entities.append(
                EntitySpec(
                    platform="sensor",
                    key="limit_state",
                    name="限位状态",
                    state=lambda device: {
                        "native_value": _enum_state(
                            limit_field, device.value("placeMemory", "code")
                        )
                    },
                )
            )

        return tuple(entities)


ADAPTER = Product2ME0Adapter()
