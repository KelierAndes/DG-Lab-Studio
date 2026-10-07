
META = {
    "id": "config_init",
    "name": "初始化配置",
    "version": "0.1.0",
    "description": "核心模块：启动时按模块声明自动装载配置文件；"
                   "支持保存到文件、从指定文件载入、导出全部配置。",
    "settings_key": "config_init",
    "actions": [],
    "default_enabled": True,
}

import json
import os
import traceback

from plugins import ModuleBase, _load_json_file

BUNDLE_MARK = "dgstudio-config-bundle"
BUNDLE_VERSION = 1


class ConfigInitModule(ModuleBase):
    id = META["id"]
    name = META["name"]
    version = META["version"]
    description = META["description"]
    settings_key = META["settings_key"]

    def __init__(self):
        self.ctx = None

    def on_load(self, ctx) -> None:
        self.ctx = ctx
        touched = ctx.engine.modules.ensure_all_configs()
        if touched:
            ctx.log(f"启动装载：按模块声明补齐 {len(touched)} 个配置文件")
        else:
            ctx.log("启动装载：全部配置文件就绪")

    def on_unload(self) -> None:
        self.ctx = None


    def save_all(self) -> None:
        manager = self.ctx.engine.modules
        self.ctx.engine.config.save()
        for settings in list(manager._settings_cache.values()):
            settings.save()
        manager._modules_state.save()

    def collect_bundle(self) -> dict:
        files: dict[str, dict] = {
            "config.json": json.loads(json.dumps(dict(self.ctx.engine.config))),
        }
        config_dir = self.ctx.engine.modules.config_dir
        try:
            names = sorted(os.listdir(config_dir))
        except OSError:
            names = []
        for name in names:
            if name.lower().endswith(".json"):
                files[f"config/{name}"] = _load_json_file(os.path.join(config_dir, name))
        return {"__bundle__": BUNDLE_MARK, "version": BUNDLE_VERSION, "files": files}

    def export_to(self, path: str) -> int:
        data = self.collect_bundle()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return len(data["files"])


    def load_from(self, path: str) -> int:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("配置文件内容不是 JSON 对象")
        if data.get("__bundle__") == BUNDLE_MARK:
            files = data.get("files") or {}
        elif isinstance(data.get("files"), dict):
            files = data["files"]
        else:
            files = {"config.json": data}

        manager = self.ctx.engine.modules
        applied = 0
        for name, content in files.items():
            if not isinstance(content, dict):
                continue
            if name == "config.json":
                for key, value in content.items():
                    self.ctx.engine.config[key] = value
                self.ctx.engine.config.save()
            else:
                target = os.path.join(manager.config_dir, os.path.basename(name))
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(content, f, ensure_ascii=False, indent=2)
            applied += 1
        manager._settings_cache.clear()
        manager.ensure_all_configs()
        manager._modules_state = _reload_modules_state(manager)
        return applied

    async def restart_running(self) -> list[str]:
        manager = self.ctx.engine.modules
        restarted: list[str] = []
        for module_id in list(manager._instances):
            if module_id == self.id:
                continue
            try:
                await manager.stop(module_id)
                await manager.start(module_id)
                restarted.append(module_id)
            except Exception:
                self.ctx.log(f"模块 {module_id} 重启失败:\n{traceback.format_exc()}")
        return restarted


def _reload_modules_state(manager) -> None:
    from plugins import JsonDict

    path = os.path.join(manager.config_dir, "modules.json")
    manager._modules_state = JsonDict(path, _load_json_file(path))
