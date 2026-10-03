"""默认头像参数命名（OSC 风格「设备前缀 + 信号名」）。

原属 osc_bridge 模块的纯函数，因联动页（核心 UI）在新增映射行时也需要
生成默认参数名，上移为核心共享；OSC 模块与联动页共用同一套命名规则。
"""

from __future__ import annotations

from dglab.params import core_inputs, output_spec

# 输入参数的默认头像参数名模板：家族 → (参数后缀模板, 全局前缀名)
INPUT_NAME_TEMPLATES = {
    "COYOTE": ("{prefix}Strength{ch}", "{prefix}Wave{ch}",
               "{prefix}WaveStep{ch}", "{prefix}Zap{ch}", "{prefix}Fire"),
    "OVC": ("{prefix}InStrength{ch}", "{prefix}InWave{ch}",
            "{prefix}InWaveStep{ch}", "{prefix}InZap{ch}", "{prefix}InFire"),
}


def default_input_name(config: dict, param_key: str) -> str:
    """核心输入参数 id → 默认头像参数名（设备前缀 + 信号模板）。"""
    global_prefix = str(config.get("prefix") or "DGLab")
    prefixes = dict(config.get("device_prefixes") or {})
    for spec in core_inputs():
        if spec["key"] != str(param_key):
            continue
        if spec["action"] == "emergency":
            return f"{global_prefix}Emergency"
        templates = INPUT_NAME_TEMPLATES.get(str(spec["family"]), ())
        index = {"strength": 0, "wave": 1, "wave_step": 2, "zap": 3,
                 "fire": 4}.get(str(spec["action"]))
        if index is None or index >= len(templates):
            return str(spec["key"])
        prefix = str(prefixes.get(str(spec["family"]))
                     or f"DGLab{spec['family'].capitalize()}")
        return templates[index].format(prefix=prefix, ch=spec["channel"])
    return str(param_key)


def default_output_name(config: dict, param_key: str) -> str:
    """核心输出参数 id → 默认头像参数名（设备前缀 + 信号名）。"""
    if str(param_key) == "Action":
        return f"{config.get('prefix') or 'DGLab'}Action"
    spec = output_spec(param_key)
    if spec is None:
        return str(param_key).split(".")[-1]
    family = str(spec.get("family") or "")
    index = int(spec.get("index") or 1)
    base = str((config.get("device_prefixes") or {}).get(family)
               or f"DGLab{family.capitalize()}")
    if index > 1:
        base = f"{base}{index}"
    return f"{base}{spec['signal']}"


def device_osc_names(state, prefixes: dict) -> dict[str, dict[str, str]]:
    """接入设备 → {family, index, name}：按家族前缀生成默认参数名前缀，
    同家族第 2 台起追加序号（DGLabCoyote / DGLabCoyote2 …）。"""
    from dglab.state import family_of

    families: dict[str, list[str]] = {}
    for slot_id in sorted(state.slots):
        slot = state.slots[slot_id]
        families.setdefault(family_of(slot.type), []).append(slot_id)
    names: dict[str, dict[str, str]] = {}
    for family, slot_ids in families.items():
        base = (prefixes or {}).get(family, f"DGLab{family.capitalize()}")
        for index, slot_id in enumerate(slot_ids, start=1):
            names[slot_id] = {
                "family": family,
                "index": index,
                "name": base if index == 1 else f"{base}{index}",
            }
    return names
