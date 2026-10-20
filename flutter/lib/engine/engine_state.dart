import 'dart:async';

import 'package:flutter/foundation.dart';

import 'engine_models.dart';
import 'engine_transport.dart';

/// 外壳唯一的数据入口：把桥上的推送摊平成可监听的状态，页面不再各自连接。
class StudioEngine extends ChangeNotifier {
  StudioEngine(this._transport) {
    _subs.addAll([
      _transport.pushes.listen(_onPush),
      _transport.closed.listen((_) => _setOnline(false, '与引擎侧车的连接已断开')),
    ]);
  }

  final EngineTransport _transport;
  final List<StreamSubscription<dynamic>> _subs = [];

  EngineSnapshot snapshot = const EngineSnapshot();
  List<LogEntry> logs = const [];
  List<ModuleInfo> modules = const [];
  List<Map<String, Object?>> market = const [];
  Map<String, Object?> config = const {};

  bool online = false;
  String? status;
  DateTime? statusAt;
  Timer? _retry;

  /// 模块启用注入 / 停用收回默认配置后自增：配置档列表跟着重拉一次。
  int profilesRev = 0;

  /// 日志缓冲上限沿用旧 UI 的 800 行。
  static const logCap = 800;

  Future<void> start() async {
    try {
      await _transport.connect();
      _setOnline(true, null);
      await refresh();
    } on Object catch (e) {
      _setOnline(false, '$e');
    }
  }

  /// 侧车被重启或崩掉时外壳不该变成一具空壳：三秒后自己再连一次。
  void _scheduleRetry() {
    _retry?.cancel();
    _retry = Timer(const Duration(seconds: 3), () async {
      if (online || _disposed) return;
      try {
        await _transport.connect();
        _setOnline(true, null);
        await refresh();
      } on Object catch (e) {
        _setOnline(false, '$e');
      }
    });
  }

  /// 拉一次快照；模块与配置在「模块」「设置」页各自按需再取。
  Future<void> refresh() async {
    await _guard(() async {
      snapshot = EngineSnapshot.fromMap(
        Map<String, Object?>.from(
          await _transport.request('engine.state') as Map,
        ),
      );
      modules = [
        for (final m
            in await _transport.request('engine.modules_list') as List? ??
                const [])
          ModuleInfo.fromMap(Map<String, Object?>.from(m as Map)),
      ];
      config = Map<String, Object?>.from(
        await _transport.request('engine.config_get') as Map,
      );
      _notify();
    });
  }

  /// 页面调用引擎动作的统一出口：失败不抛出，只回落到提示。
  Future<Object?> invoke(
    String method, [
    Map<String, Object?>? params,
    Duration? timeout,
  ]) async {
    try {
      return await _transport.request(method, params, timeout);
    } on Object catch (e) {
      say('$method 失败：$e');
      return null;
    }
  }

  void say(String message) {
    status = message;
    statusAt = DateTime.now();
    _notify();
  }

  void clearLogs() => logs = const [];

  /// 负鼠按键位 → 最近一次按下的时刻。松开事件丢了也要自己熄，别常亮。
  final Map<int, DateTime> _pressed = {};
  static const pressHold = Duration(milliseconds: 900);

  bool pressed(int bit) {
    final at = _pressed[bit];
    if (at == null) return false;
    if (DateTime.now().difference(at) > pressHold) {
      _pressed.remove(bit);
      return false;
    }
    return true;
  }

  bool marketLoading = false;
  String? marketError;

  /// 蓝牙扫描要 6 秒左右，结果只服务连接页，但放在这里才不会因为切页丢掉。
  bool bleScanning = false;
  List<Map<String, Object?>> bleResults = const [];

  Future<void> scanBle({double timeout = 6.0}) async {
    if (bleScanning) return;
    bleScanning = true;
    _notify();
    try {
      final rows = await _transport.request('engine.ble_scan', {
        'timeout': timeout,
      }, Duration(seconds: (timeout + 12).round()));
      bleResults = [
        for (final d in rows as List? ?? const [])
          Map<String, Object?>.from(d as Map),
      ];
    } on Object catch (e) {
      say('扫描失败：$e');
    } finally {
      bleScanning = false;
      _notify();
    }
  }

  /// 市场清单要跑网络，十几秒是常态，所以取清单的生命周期归引擎而不是某个页面。
  Future<void> fetchMarket({bool force = true}) async {
    if (marketLoading) return;
    marketLoading = true;
    marketError = null;
    _notify();
    try {
      final rows = await _transport.request('market.fetch', {
        'force': force,
      }, const Duration(seconds: 90));
      if (rows is List) {
        market = [for (final m in rows) Map<String, Object?>.from(m as Map)];
      } else {
        marketError = '市场清单取回失败';
      }
    } on Object catch (e) {
      marketError = '$e';
    } finally {
      marketLoading = false;
      _notify();
    }
  }

  /// 正在跑长任务（下载 / 装依赖 / 更新）的模块 id；同一模块并发点击直接忽略。
  final Set<String> busyModules = {};

  /// 模块设置面板：spec + 当前值，按模块缓存，切模块时重取。
  String? settingsModuleId;
  Map<String, Object?> moduleSpec = const {};
  Map<String, Object?> moduleSettings = const {};

  bool isBusy(String moduleId) => busyModules.contains(moduleId);

  /// 下载与 pip 可能跑几分钟，超时放宽，并且结束一定要清 busy。
  Future<void> runModuleTask(
    String moduleId,
    String method, [
    Map<String, Object?>? params,
  ]) async {
    if (busyModules.contains(moduleId)) return;
    busyModules.add(moduleId);
    _notify();
    try {
      await _transport.request(method, {
        'module_id': moduleId,
        ...?params,
      }, const Duration(minutes: 6));
    } on Object catch (e) {
      say('$moduleId：$e');
    } finally {
      busyModules.remove(moduleId);
      await refresh();
      // 重新加载会让模块自己重排设置（OSC 的设备参数就是按当前设备现算的）：
      // 用户正盯着这一栏，得把引擎那边的新表取回来，不然看到的还是旧行。
      if (settingsModuleId == moduleId) await reloadSettings(moduleId);
    }
  }

  /// 卸载 = 停止 + 卸下 + 删掉模块文件。
  ///
  /// 只调 `modules.uninstall` 的话文件还在盘上，列表里那条还是这一家，市场那颗
  /// 按钮也还是「更新」——用户看到的就是「点了卸载没反应」。安装的反义词是
  /// 把文件也带走，所以这里走 `modules.delete`。
  Future<void> deleteModule(String moduleId) async {
    if (busyModules.contains(moduleId)) return;
    busyModules.add(moduleId);
    _notify();
    try {
      await _transport.request('modules.delete', {
        'module_id': moduleId,
      }, const Duration(minutes: 6));
    } on Object catch (e) {
      say('$moduleId：$e');
    } finally {
      busyModules.remove(moduleId);
      // 这两份缓存是按模块 id 认的：不丢回去，删掉的模块还会占着设置面板的读数。
      if (settingsModuleId == moduleId) settingsModuleId = null;
      if (extrasModuleId == moduleId) extrasModuleId = null;
      await refresh();
    }
  }

  /// 模块自带的 README：点详情页那颗「README」按钮才取，弹窗里预览。
  Future<Map<String, Object?>?> readReadme(String moduleId) async {
    if (moduleId.isEmpty) return null;
    final r = await invoke('modules.readme', {'module_id': moduleId});
    return r is Map ? Map<String, Object?>.from(r) : null;
  }

  /// 模块详情页的附加行：第三方依赖与游戏模组，切模块时重取。
  String? extrasModuleId;
  Map<String, Object?> moduleDeps = const {};
  Map<String, Object?> moduleMods = const {};
  List<String> moduleModsFound = const [];

  Future<void> loadExtras(String moduleId) async {
    if (moduleId.isEmpty || extrasModuleId == moduleId) return;
    extrasModuleId = moduleId;
    final r = await invoke('modules.deps', {
      'module_id': moduleId,
    }, const Duration(seconds: 40));
    if (extrasModuleId != moduleId) return;
    _applyExtras(r);
  }

  /// 装依赖 / 扫盘 / 装模组都是分钟级：借模块卡的 busy 转圈，回包即新一帧附加行。
  Future<void> runExtrasTask(
    String moduleId,
    String method, [
    Map<String, Object?>? params,
  ]) async {
    if (busyModules.contains(moduleId)) return;
    busyModules.add(moduleId);
    _notify();
    try {
      _applyExtras(
        await _transport.request(method, {
          'module_id': moduleId,
          ...?params,
        }, const Duration(minutes: 6)),
      );
    } on Object catch (e) {
      say('$moduleId：$e');
    } finally {
      busyModules.remove(moduleId);
      _notify();
    }
  }

  void _applyExtras(Object? r) {
    if (r is! Map) return;
    final id = '${r['module_id'] ?? ''}';
    if (id.isEmpty) return;
    extrasModuleId = id;
    if (r['deps'] is Map) {
      moduleDeps = Map<String, Object?>.from(r['deps'] as Map);
    }
    if (r['mods'] is Map) {
      moduleMods = Map<String, Object?>.from(r['mods'] as Map);
    }
    moduleModsFound = [for (final p in (r['found'] as List? ?? const [])) '$p'];
    _notify();
  }

  Future<void> loadSettings(String moduleId) async {
    if (moduleId.isEmpty || settingsModuleId == moduleId) return;
    settingsModuleId = moduleId;
    try {
      final r = Map<String, Object?>.from(
        await _transport.request('modules.settings_get', {
          'module_id': moduleId,
        }) as Map,
      );
      if (settingsModuleId != moduleId) return;
      moduleSpec = {
        for (final e in (r['spec'] as Map? ?? const {}).entries)
          if (e.value is Map)
            '${e.key}': Map<String, Object?>.from(e.value as Map),
      };
      moduleSettings = Map<String, Object?>.from(
        r['settings'] as Map? ?? const {},
      );
    } on Object catch (e) {
      say('读取模块设置失败：$e');
    }
    _notify();
  }

  Future<void> setSetting(String moduleId, String key, Object? value) async {
    moduleSettings = {...moduleSettings, key: value};
    _notify();
    await invoke('modules.settings_set', {
      'module_id': moduleId,
      'key': key,
      'value': value,
    });
  }

  /// 丢掉缓存重取一份：设备连上、改完名之后，表要按引擎的现算结果重来。
  Future<void> reloadSettings(String moduleId) async {
    settingsModuleId = null;
    await loadSettings(moduleId);
  }

  /// 在插件配置页里改一个登记参数的名字。
  ///
  /// 走的是事件流那条改名：名字合法性、被占用、画布上引用它的卡片改接，都只有
  /// 引擎那一份判断；这里不再自己校验一遍，免得两边规则走岔。
  Future<void> renameSettingVar(String moduleId, String from, String to) async {
    final next = to.trim();
    if (next.isEmpty || next == from) return;
    final r = await invoke('flow.var_rename', {'name': from, 'new': next});
    final why = '${(r is Map ? r['error'] : '') ?? ''}';
    if (why.isNotEmpty) say(why);
    // 改名成没成都以引擎那份表为准：失败时把地址收回去，界面不会留下半个新名。
    await reloadSettings(moduleId);
  }

  /// 每台设备的「本卡参数」：同一个 key 在不同槽位上取值不同。
  final Map<String, List<Map<String, Object?>>> deviceParams = {};

  /// 加减键实际一步多大（引擎按设备类型算，OVC 会并到 10 的倍数）。
  final Map<String, int> deviceSteps = {};

  Future<void> loadParams(String slotId) async {
    if (slotId.isEmpty) return;
    final r = await invoke('engine.intensity_params', {'slot_id': slotId});
    if (r is! Map) return;
    deviceParams[slotId] = [
      for (final row in (r['rows'] as List? ?? const []))
        if (row is Map) Map<String, Object?>.from(row),
    ];
    if (r['step'] is num) deviceSteps[slotId] = (r['step'] as num).round();
    _notify();
  }

  /// 灵猫气压轨迹：快照每来一帧采一次，画最近 60 秒。
  final Map<String, List<({double at, double kpa})>> pressureTrack = {};
  static const pressureWindow = 60.0;

  void _trackPressure(EngineSnapshot s) {
    final now = DateTime.now().millisecondsSinceEpoch / 1000.0;
    for (final slot in s.slots) {
      if (slot.isOutputDevice) continue;
      final kpa = slot.pressure;
      if (kpa == null) continue;
      final list = pressureTrack.putIfAbsent(slot.slotId, () => []);
      if (list.isNotEmpty && (now - list.last.at) < 0.08) continue;
      list.add((at: now, kpa: kpa));
      while (list.length > 900 ||
          (list.isNotEmpty && now - list.first.at > pressureWindow)) {
        list.removeAt(0);
      }
    }
  }

  /// 相对现在的采样点，图表按这个画横轴。
  List<({double at, double kpa})> pressureSamples(String slotId) {
    final list = pressureTrack[slotId] ?? const [];
    final now = DateTime.now().millisecondsSinceEpoch / 1000.0;
    return [for (final p in list) (at: p.at - now, kpa: p.kpa)];
  }

  /// 波形柱状图：设备侧的采样窗口，由控制页按 250ms 轮询。
  Future<List<({double at, List<int> a, List<int> b})>> waveSamples(
    String slotId,
  ) async {
    final r = await invoke('engine.wave_history', {'slot_id': slotId});
    if (r is! Map) return const [];
    return [
      for (final s in (r['samples'] as List? ?? const []))
        if (s is List && s.length >= 3)
          (
            at: (s[0] as num).toDouble(),
            a: [
              for (final v in (s[1] as List? ?? const [])) (v as num).round(),
            ],
            b: [
              for (final v in (s[2] as List? ?? const [])) (v as num).round(),
            ],
          ),
    ];
  }

  /// 概览页的「数据流探测」：模块 ↔ 核心之间已经连好的线路。
  /// 哪条算线路、中间过了几张转换卡、公式里第一个变量是谁，全由引擎判，界面只排。
  Future<List<Map<String, Object?>>> flowLines() async {
    final r = await invoke('flow.dataflow', const {});
    if (r is! Map) return const [];
    return [
      for (final row in (r['lines'] as List? ?? const []))
        Map<String, Object?>.from(row as Map),
    ];
  }

  void _onPush(BridgeMessage msg) {
    switch (msg.event) {
      case 'snapshot':
        snapshot = EngineSnapshot.fromMap(
          Map<String, Object?>.from(msg.data as Map),
        );
        _trackPressure(snapshot);
      case 'log':
        final entry = LogEntry.fromList(msg.data as List<Object?>);
        logs = logs.length >= logCap
            ? [...logs.sublist(logs.length - logCap + 1), entry]
            : [...logs, entry];
      case 'logs':
        logs = [
          for (final row in msg.data as List? ?? const [])
            LogEntry.fromList(row as List<Object?>),
        ].take(logCap).toList();
      case 'modules':
        modules = [
          for (final m in msg.data as List? ?? const [])
            ModuleInfo.fromMap(Map<String, Object?>.from(m as Map)),
        ];
        // 设备连上 / 断开会重算模块的派生设置表（OSC 的「设备 OSC 参数」那一栏）：
        // 用户正看着这张卡就得跟着换，不能等他切走再切回来。
        final open = settingsModuleId;
        if (open != null) unawaited(reloadSettings(open));
      case 'config':
        config = Map<String, Object?>.from(msg.data as Map);
      case 'market':
        market = [
          for (final m in msg.data as List? ?? const [])
            Map<String, Object?>.from(m as Map),
        ];
      case 'profiles':
        profilesRev++;
      case 'toast':
        status = '${msg.data}';
        statusAt = DateTime.now();
      case 'button':
        // 负鼠按键按下 / 松开：可视化绑定面板亮对应那颗键。
        final row = msg.data;
        if (row is List && row.length >= 3) {
          final bit = (row[1] as num).round();
          if (row[2] == 1) {
            _pressed[bit] = DateTime.now();
          } else {
            _pressed.remove(bit);
          }
        }
      default:
        return;
    }
    _notify();
  }

  void _setOnline(bool value, String? error) {
    online = value;
    if (error != null) say(error);
    if (!value) _scheduleRetry();
    _notify();
  }

  void _notify() {
    if (hasListeners) notifyListeners();
  }

  Future<void> _guard(Future<void> Function() body) async {
    try {
      await body();
    } on Object catch (e) {
      say('引擎调用失败：$e');
    }
  }

  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    _retry?.cancel();
    for (final s in _subs) {
      s.cancel();
    }
    unawaited(_transport.dispose());
    super.dispose();
  }
}
