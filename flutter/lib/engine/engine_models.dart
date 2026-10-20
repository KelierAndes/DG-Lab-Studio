import 'dart:convert';

/// 一条设备槽位。字段与 Python 侧 `dglab/state.py: Slot` 一一对应，
/// 桥上沿用 snake_case，Dart 侧只在解析处翻译。
class DeviceSlot {
  const DeviceSlot({
    required this.slotId,
    required this.name,
    required this.type,
    required this.strength,
    required this.strengthLimit,
    required this.channelStatus,
    this.battery,
    this.pressure,
    this.edgeState,
    this.props = const {},
    this.slotState = const {},
  });

  final String slotId;
  final String name;
  final String type;
  final Map<String, int> strength;
  final Map<String, int> strengthLimit;
  final Map<String, int> channelStatus;
  final int? battery;
  final double? pressure;
  final int? edgeState;
  final Map<String, Object?> props;
  final Map<String, Object?> slotState;

  bool get isOutputDevice => !type.toUpperCase().startsWith('BMTR');

  String get label =>
      name.isNotEmpty ? name : (type.isNotEmpty ? type : slotId);

  int of(String channel, Map<String, int> map) => map[channel] ?? 0;

  String summary() {
    final bat = battery == null ? '--' : '$battery%';
    if (!isOutputDevice) {
      final kpa = pressure == null ? '--' : pressure!.toStringAsFixed(2);
      return '$label  气压 $kpa kPa  电量 $bat';
    }
    return '$label  A=${of('A', strength)}/${of('A', strengthLimit)} '
        'B=${of('B', strength)}/${of('B', strengthLimit)}  电量 $bat';
  }

  static DeviceSlot fromMap(Map<String, Object?> m) => DeviceSlot(
    slotId: '${m['slot_id'] ?? ''}',
    name: '${m['name'] ?? ''}',
    type: '${m['type'] ?? ''}',
    strength: _intPair(m['strength']),
    strengthLimit: _intPair(m['strength_limit']),
    channelStatus: _intPair(m['channel_status']),
    battery: m['battery'] is int ? m['battery'] as int : null,
    pressure: m['pressure'] == null ? null : (m['pressure'] as num).toDouble(),
    edgeState: m['edge_state'] is int ? m['edge_state'] as int : null,
    props: Map<String, Object?>.from(_asMap(m['props'])),
    slotState: Map<String, Object?>.from(_asMap(m['slot_state'])),
  );
}

/// 一条接入通道的状态。多通道可以并存，连接页按通道各说各话。
class ChannelState {
  const ChannelState({
    required this.key,
    required this.label,
    this.connected = false,
    this.paired = false,
    this.primary = false,
    this.present = false,
    this.statusText = '未连接',
    this.clientId = '',
    this.targetId = '',
    this.qrText = '',
    this.slots = const [],
    this.localRelay = false,
    this.relayPort,
  });

  final String key;
  final String label;
  final bool connected;
  final bool paired;

  /// 最后一次连上的通道：顶栏的状态胶囊跟着它走。
  final bool primary;

  /// 这条通道当前有没有客户端（连过又断开就没了）。
  final bool present;
  final String statusText;
  final String clientId;
  final String targetId;
  final String qrText;
  final List<String> slots;
  final bool localRelay;
  final int? relayPort;

  bool get isRelay => key != 'ble';

  String get summary {
    if (localRelay && relayPort != null) return '本地中继 ws://局域网:$relayPort';
    return isRelay ? '远程中继' : '蓝牙直连设备';
  }

  static ChannelState fromMap(Map<String, Object?> m) => ChannelState(
    key: '${m['key'] ?? ''}',
    label: '${m['label'] ?? ''}',
    connected: m['connected'] == true,
    paired: m['paired'] == true,
    primary: m['primary'] == true,
    present: m['present'] == true,
    statusText: '${m['status_text'] ?? ''}',
    clientId: '${m['client_id'] ?? ''}',
    targetId: '${m['target_id'] ?? ''}',
    qrText: '${m['qr_text'] ?? ''}',
    slots: [for (final s in (m['slots'] as List? ?? const [])) '$s'],
    localRelay: m['local_relay'] == true,
    relayPort: m['relay_port'] is num ? (m['relay_port'] as num).round() : null,
  );
}

/// 引擎状态快照，对应 `EngineState`（dataclass 的 vars 展开）。
class EngineSnapshot {
  const EngineSnapshot({
    this.backend = 'none',
    this.connected = false,
    this.paired = false,
    this.statusText = '未连接',
    this.clientId = '',
    this.targetId = '',
    this.qrText = '',
    this.slots = const [],
    this.activeSlot = '',
    this.lastAction,
    this.channels = const [],
    this.slotChannels = const {},
  });

  final String backend;
  final bool connected;
  final bool paired;
  final String statusText;
  final String clientId;
  final String targetId;
  final String qrText;
  final List<DeviceSlot> slots;
  final String activeSlot;
  final int? lastAction;
  final List<ChannelState> channels;

  /// 槽位 → 通道键（v4 / v3 / ble）：控制页卡头写「连接通道」。
  final Map<String, String> slotChannels;

  static const channelLabels = {
    'v4': 'Socket V4',
    'v3': 'Socket V3',
    'ble': '蓝牙直连',
  };

  String channelLabel(String slotId) =>
      channelLabels[slotChannels[slotId]] ?? '未连接';

  ChannelState channel(String key) => channels.firstWhere(
    (c) => c.key == key,
    orElse: () => ChannelState(key: key, label: key),
  );

  int get connectedCount => channels.where((c) => c.connected).length;

  DeviceSlot? get active {
    for (final s in slots) {
      if (s.slotId == activeSlot) return s;
    }
    return slots.isEmpty ? null : slots.first;
  }

  bool get isBmtrActive => active != null && !active!.isOutputDevice;

  static EngineSnapshot fromMap(Map<String, Object?> m) {
    final raw = _asMap(m['slots']);
    return EngineSnapshot(
      backend: '${m['backend'] ?? 'none'}',
      connected: m['connected'] == true,
      paired: m['paired'] == true,
      statusText: '${m['status_text'] ?? ''}',
      clientId: '${m['client_id'] ?? ''}',
      targetId: '${m['target_id'] ?? ''}',
      qrText: '${m['qr_text'] ?? ''}',
      slots: [
        for (final v in raw.values)
          DeviceSlot.fromMap(Map<String, Object?>.from(_asMap(v))),
      ],
      activeSlot: '${m['active_slot'] ?? ''}',
      lastAction: m['last_action'] is int ? m['last_action'] as int : null,
      channels: [
        for (final c in (m['channels'] as List? ?? const []))
          if (c is Map) ChannelState.fromMap(Map<String, Object?>.from(c)),
      ],
      slotChannels: {
        for (final e in _asMap(m['slot_channels']).entries)
          e.key.toString(): '${e.value}',
      },
    );
  }
}

class LogEntry {
  const LogEntry({required this.ts, required this.level, required this.text});

  final double ts;
  final String level;
  final String text;

  static const levels = <String>['info', 'warn', 'error', 'debug'];

  /// 与旧 UI 的 LEVEL_LABELS 同一套读法。
  String get levelLabel => switch (level) {
    'warn' => '警告',
    'error' => '错误',
    'debug' => '调试',
    _ => '信息',
  };

  String get clock {
    final t = DateTime.fromMillisecondsSinceEpoch((ts * 1000).round());
    String two(int v) => v.toString().padLeft(2, '0');
    return '${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  }

  static LogEntry fromList(List<Object?> l) =>
      LogEntry(ts: (l[0] as num).toDouble(), level: '${l[1]}', text: '${l[2]}');
}

/// 「模块」页的一张卡片，取自 plugins.py 的 META 摘要。
class ModuleInfo {
  const ModuleInfo({
    required this.id,
    required this.name,
    required this.version,
    required this.description,
    this.short = '',
    this.enabled = false,
    this.loaded = false,
    this.running = false,
    this.realtimeManager = false,
    this.actions = const [],
    this.defaults = const {},
  });

  final String id;
  final String name;

  /// 模块自己声明的简写（AIC / OSC…）：概览页的数据流线路靠它标归属。
  final String short;
  final String version;
  final String description;
  final bool enabled;
  final bool loaded;
  final bool running;
  // 画面识别类模块：模块页要给它挂「识别参数」表。
  final bool realtimeManager;
  final List<String> actions;

  /// 模块自带的默认配置名：{'flow': [...], 'bindings': [...]}。
  /// 模块卡用它决定要不要给「一键套用默认配置」这颗按钮。
  final Map<String, List<String>> defaults;

  /// 「事件流「X」· 按键映射「Y」」这一行的内容；没带默认配置就是 null。
  List<String>? get defaultsValue {
    final parts = <String>[
      if (defaults['flow']?.isNotEmpty ?? false)
        '事件流「${defaults['flow']!.join('、')}」',
      if (defaults['bindings']?.isNotEmpty ?? false)
        '按键映射「${defaults['bindings']!.join('、')}」',
    ];
    return parts.isEmpty ? null : parts;
  }

  static ModuleInfo fromMap(Map<String, Object?> m) => ModuleInfo(
    id: '${m['id'] ?? ''}',
    name: '${m['name'] ?? ''}',
    short: '${m['short'] ?? ''}',
    version: '${m['version'] ?? ''}',
    description: '${m['description'] ?? ''}',
    enabled: m['enabled'] == true,
    loaded: m['loaded'] == true,
    running: m['running'] == true,
    realtimeManager: m['realtime_manager'] == true,
    actions: [for (final a in (m['actions'] as List? ?? const [])) '$a'],
    defaults: {
      'flow': [
        for (final n in (_asMap(m['defaults'])['flow'] as List? ?? const []))
          '$n',
      ],
      'bindings': [
        for (final n
            in (_asMap(m['defaults'])['bindings'] as List? ?? const []))
          '$n',
      ],
    },
  );
}

Map<String, Object?> _asMap(Object? v) =>
    v is Map ? Map<String, Object?>.from(v) : const {};

Map<String, int> _intPair(Object? v) {
  if (v is! Map) return const {'A': 0, 'B': 0};
  return {
    for (final e in v.entries)
      '${e.key}': (e.value is num) ? (e.value as num).round() : 0,
  };
}

/// 桥上一帧原始消息：`{"id":..}` 是请求的回执，`{"event":..}` 是服务端推送。
class BridgeMessage {
  const BridgeMessage._({this.id, this.event, this.data, this.error});

  final int? id;
  final String? event;
  final Object? data;
  final String? error;

  bool get isReply => id != null;

  /// 服务端主动推送的一帧（由 Mock 传输与解析器共用）。
  const BridgeMessage.event(this.event, this.data) : id = null, error = null;

  factory BridgeMessage.decode(String raw) {
    final m = jsonDecode(raw);
    if (m is! Map<String, Object?>) return const BridgeMessage._();
    if (m.containsKey('event')) {
      return BridgeMessage._(id: null, event: '${m['event']}', data: m['data']);
    }
    return BridgeMessage._(
      id: m['id'] is int ? m['id'] as int : int.tryParse('${m['id']}'),
      data: m['result'],
      error: m['error'] == null ? null : '${m['error']}',
    );
  }
}
