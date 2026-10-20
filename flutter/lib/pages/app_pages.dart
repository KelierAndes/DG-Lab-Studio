import 'package:flutter/material.dart';

/// 外壳的七个目的地。事件流独占节点画布，其余页面共用 page kit 的 bento 栅格。
enum AppPage {
  dashboard('概览', Icons.dashboard_rounded),
  connect('连接', Icons.link_rounded),
  control('控制', Icons.sports_esports_rounded),
  flow('事件流', Icons.account_tree_rounded),
  modules('模块', Icons.extension_rounded),
  log('日志', Icons.list_alt_rounded),
  settings('设置', Icons.settings_rounded);

  const AppPage(this.label, this.icon);
  final String label;
  final IconData icon;

  bool get isCanvas => this == AppPage.flow;
}

/// 跨页共享的 UI 状态。引擎数据不住在这里，页面各自读 [EngineState]；
/// 这里只放「用户看到哪儿了」。
class AppNav extends ChangeNotifier {
  AppPage page = AppPage.dashboard;
  String query = '';
  String? selectedSlot;
  String? selectedModule;
  int logLevel = 0;

  /// 日志级别顺序与 Python 侧 classify_log 的 key 对齐。
  static const logLevels = <String>['全部', 'info', 'warn', 'error', 'debug'];

  void go(AppPage p) {
    if (page == p) return;
    page = p;
    notifyListeners();
  }

  void search(String q) {
    if (query == q) return;
    query = q;
    notifyListeners();
  }

  void selectSlot(String? id) {
    if (selectedSlot == id) return;
    selectedSlot = id;
    notifyListeners();
  }

  void selectModule(String? id) {
    if (selectedModule == id) return;
    selectedModule = id;
    notifyListeners();
  }

  void setLogLevel(int i) {
    if (logLevel == i) return;
    logLevel = i;
    notifyListeners();
  }

  bool matches(String text) {
    final q = query.trim().toLowerCase();
    return q.isEmpty || text.toLowerCase().contains(q);
  }
}
