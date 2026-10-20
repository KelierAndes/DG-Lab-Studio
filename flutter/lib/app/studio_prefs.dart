import 'package:flutter/foundation.dart';

/// 外壳自身的外观偏好（不属于引擎状态）：明暗、左栏展开、画布网格。
///
/// 这三项会写回 config.json 的 `ui.*`：设置页的开关与主题不该只活到本次运行，
/// 但界面状态本身仍以这里为准——引擎只在第一次回传时把它们灌进来。
class StudioPrefs extends ChangeNotifier {
  bool lightTheme = false;
  bool railOn = true;

  /// 左栏的抽屉面板：概览给设备、模块给清单、事件流给卡片面板与变量表。
  bool drawerOpen = true;
  bool gridOn = true;

  /// 由外壳接上：把某个界面键写回引擎（`settings.set`）。
  void Function(String key, Object? value)? persist;

  String? status;
  DateTime? statusAt;

  bool _loaded = false;

  /// 配置每次写操作都会回推一份，只有第一份能覆盖用户当下拨的开关。
  void loadFromEngine(Map<String, Object?> settings) {
    if (_loaded) return;
    _loaded = true;
    lightTheme = settings['appearance.dark'] != true;
    gridOn = settings['appearance.grid'] != false;
    drawerOpen = settings['appearance.drawer'] != false;
    notifyListeners();
  }

  void toggleTheme() {
    lightTheme = !lightTheme;
    _persist('appearance.dark', !lightTheme);
  }

  void toggleDrawer() {
    drawerOpen = !drawerOpen;
    _persist('appearance.drawer', drawerOpen);
  }

  void toggleGrid() {
    gridOn = !gridOn;
    _persist('appearance.grid', gridOn);
  }

  void toggleRail() {
    railOn = !railOn;
    notifyListeners();
  }

  void _persist(String key, Object? value) {
    persist?.call(key, value);
    notifyListeners();
  }

  void say(String message) {
    status = message;
    statusAt = DateTime.now();
    notifyListeners();
  }
}
