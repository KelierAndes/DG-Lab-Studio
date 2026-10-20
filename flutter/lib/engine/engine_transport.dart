import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'engine_models.dart';

class BridgeException implements Exception {
  const BridgeException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// 外壳与 Python 引擎之间唯一的通道：一问一答 + 服务端主动推送。
abstract class EngineTransport {
  Future<void> connect();
  Future<Object?> request(
    String method, [
    Map<String, Object?>? params,
    Duration? timeout,
  ]);

  /// `snapshot` / `log` / `modules` / `toast` 等服务端推送。
  Stream<BridgeMessage> get pushes;
  Stream<void> get closed;
  Future<void> dispose();
}

/// 起引擎侧车（`python -m bridge`），从它的 stdout 读端口与握手 token，再连本地 WebSocket。
class LoopbackEngineTransport implements EngineTransport {
  LoopbackEngineTransport({this.scriptDir, this.pythonExecutable});

  final String? scriptDir;
  final String? pythonExecutable;

  Process? _proc;
  WebSocket? _ws;
  int _seq = 0;
  final Map<int, Completer<Object?>> _pending = {};
  final StreamController<BridgeMessage> _pushes =
      StreamController<BridgeMessage>.broadcast();
  final StreamController<void> _closed = StreamController<void>.broadcast();

  @override
  Future<void> connect() async {
    // 重连时先收掉上一次的进程与套接字，否则会留下孤儿侧车。
    await teardown();
    final bridge = sidecarExecutable();
    // 冻结成无控制台 exe 后 stdout 不一定还通，握手另开一个文件通道。
    final hello = File(
      '${Directory.systemTemp.path}/dgstudio-bridge-$pid.json',
    );
    if (hello.existsSync()) hello.deleteSync();
    final String exe;
    final String home;
    final List<String> args;
    if (bridge != null) {
      exe = bridge;
      home = File(bridge).parent.path;
      args = [
        '--handshake',
        '--handshake-file',
        hello.path,
        '--parent',
        '$pid',
      ];
    } else {
      exe =
          pythonExecutable ??
          Platform.environment['DGSTUDIO_PYTHON'] ??
          'python';
      home = scriptDir ?? engineHome() ?? '';
      if (home.isEmpty) {
        throw const BridgeException(
          '找不到引擎目录（bridge/server.py），可用 DGSTUDIO_HOME 指定',
        );
      }
      args = [
        '-m',
        'bridge.server',
        '--handshake',
        '--handshake-file',
        hello.path,
        '--parent',
        '$pid',
      ];
    }
    // 侧车靠 --parent 轮询我们的句柄收尾；它绝不能拿 stdin 阻塞读，那会攥住 GIL。
    _proc = await Process.start(
      exe,
      args,
      workingDirectory: home,
      runInShell: false,
    );
    unawaited(_dropHandshake(hello));
    final hand = await _readHandshake(_proc!, hello);
    final ws = await WebSocket.connect('ws://127.0.0.1:${hand['port']}/ws');
    _ws = ws;
    ws.listen(
      _onData,
      onDone: () => _closed.add(null),
      onError: (_) => _closed.add(null),
    );
    // 未鉴权的连接既不推送也不接受方法，所以握手后的第一件事是交令牌。
    final reply = await request('bridge.auth', {'token': hand['token']});
    if (reply != true) throw const BridgeException('桥鉴权失败');
  }

  /// 打包版：与外壳 exe 同目录的冻结侧车。引擎按 `sys.executable` 所在目录找
  /// config.json 与 modules/，所以它必须和 DGStudio.exe 摆在一起。
  static String? sidecarExecutable() {
    final candidate = File(
      '${File(Platform.resolvedExecutable).parent.path}/DGStudio-bridge.exe',
    );
    return candidate.existsSync() ? candidate.path : null;
  }

  /// 握手到手后这个临时文件就没用了，别在临时目录里堆垃圾。
  Future<void> _dropHandshake(File hello) async {
    for (var i = 0; i < 80; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 250));
      if (!hello.existsSync()) return;
      try {
        hello.deleteSync();
        return;
      } on FileSystemException {
        continue;
      }
    }
  }

  /// 开发期从 exe 往上找仓库根；打包后桥与 exe 同目录，同样命中。
  static String? engineHome() {
    final fromEnv = Platform.environment['DGSTUDIO_HOME'];
    if (fromEnv != null && Directory('$fromEnv/bridge').existsSync()) {
      return fromEnv;
    }
    var dir = File(Platform.resolvedExecutable).parent;
    for (var i = 0; i < 7; i++) {
      if (Directory('${dir.path}/bridge').existsSync() &&
          File('${dir.path}/app.py').existsSync()) {
        return dir.path;
      }
      if (!dir.parent.path.endsWith(dir.path) && dir.path != dir.parent.path) {
        dir = dir.parent;
      } else {
        break;
      }
    }
    return null;
  }

  /// 握手是一条独立的 JSON：`{"bridge":1,"port":54321,"token":"..."}`。
  ///
  /// 两条通道谁先到听谁的：stdout 要一直有人读，侧车往管道里打的东西没人接，
  /// 管道写满后它会整条卡死，表现是桥突然不再回任何消息；而无控制台的冻结侧车
  /// 根本没有可用的 stdout，只能靠握手文件。非握手行转接到本进程 stderr 便于排错。
  Future<Map<String, Object?>> _readHandshake(Process proc, File hello) async {
    proc.stderr.transform(utf8.decoder).listen((chunk) => stderr.write(chunk));
    final completer = Completer<Map<String, Object?>>();
    void accept(String line) {
      if (completer.isCompleted) return;
      final t = line.trim();
      if (!t.startsWith('{')) {
        stderr.writeln('[sidecar] $line');
        return;
      }
      try {
        final map = Map<String, Object?>.from(jsonDecode(t) as Map);
        if (map['bridge'] == 1 && map['port'] is int) completer.complete(map);
      } on FormatException {
        // 引擎的普通日志行也可能以 { 开头，忽略继续等。
        stderr.writeln('[sidecar] $line');
      }
    }

    proc.stdout
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen(accept);
    late Timer poll;
    poll = Timer.periodic(const Duration(milliseconds: 120), (_) async {
      if (completer.isCompleted) {
        poll.cancel();
        return;
      }
      try {
        final text = await hello.readAsString();
        if (text.trim().isNotEmpty) accept(text);
      } on FileSystemException {
        // 文件还没被侧车创建。
      }
    });
    return completer.future.timeout(
      const Duration(seconds: 20),
      onTimeout: () {
        poll.cancel();
        throw const BridgeException('引擎侧车未在 20 秒内完成握手');
      },
    );
  }

  void _onData(dynamic raw) {
    final msg = BridgeMessage.decode('$raw');
    if (msg.isReply) {
      final c = _pending.remove(msg.id);
      if (msg.error != null) {
        c?.completeError(BridgeException(msg.error!));
      } else {
        c?.complete(msg.data);
      }
      return;
    }
    _pushes.add(msg);
  }

  @override
  Future<Object?> request(
    String method, [
    Map<String, Object?>? params,
    Duration? timeout,
  ]) async {
    final ws = _ws;
    if (ws == null) throw const BridgeException('引擎未连接');
    final id = ++_seq;
    final completer = Completer<Object?>();
    _pending[id] = completer;
    ws.add(
      jsonEncode({'id': id, 'method': method, 'params': params ?? const {}}),
    );
    return completer.future.timeout(
      timeout ?? const Duration(seconds: 30),
      onTimeout: () {
        _pending.remove(id);
        throw BridgeException('$method 超时');
      },
    );
  }

  @override
  Stream<BridgeMessage> get pushes => _pushes.stream;

  @override
  Stream<void> get closed => _closed.stream;

  Future<void> teardown() async {
    final ws = _ws;
    _ws = null;
    await ws?.close();
    _proc?.kill();
    _proc = null;
    for (final c in _pending.values) {
      if (!c.isCompleted) c.completeError(const BridgeException('连接已重置'));
    }
    _pending.clear();
  }

  @override
  Future<void> dispose() async {
    await teardown();
    await _pushes.close();
    await _closed.close();
  }
}

/// 桥就绪前的替身：给出一套可用的设备/模块/日志数据，让页面在纯前端阶段就能被点验。
class MockEngineTransport implements EngineTransport {
  /// [heartbeat] 每两秒推一次快照，用来演示数据流；widget 测试里关掉它，
  /// 免得留下未完成的定时器。
  MockEngineTransport({this.heartbeat = true});

  final bool heartbeat;

  final _pushes = StreamController<BridgeMessage>.broadcast();
  final _closed = StreamController<void>.broadcast();
  Timer? _beat;
  int _battery = 78;
  final Map<String, int> _strength = {'A': 0, 'B': 0};

  /// 测试用：记下每次调用的方法名与参数，便于断言界面到底发了什么。
  final List<(String, Map<String, Object?>)> calls = [];

  @override
  Future<void> connect() async {
    _emit('snapshot', _snapshot());
    _log('info', '外壳已连接（演示数据）');
    if (!heartbeat) return;
    _beat = Timer.periodic(const Duration(seconds: 2), (_) {
      _battery = (_battery - 1).clamp(1, 100);
      _emit('snapshot', _snapshot());
    });
  }

  @override
  Future<Object?> request(
    String method, [
    Map<String, Object?>? params,
    Duration? timeout,
  ]) async {
    calls.add((method, Map<String, Object?>.of(params ?? const {})));
    switch (method) {
      case 'engine.state':
        return _snapshot();
      case 'engine.add_strength':
        final ch = '${params?['channel'] ?? 'A'}';
        _strength[ch] = ((_strength[ch] ?? 0) + (params?['delta'] as int? ?? 0))
            .clamp(0, 200);
        _emit('snapshot', _snapshot());
      case 'engine.set_strength':
        final ch = '${params?['channel'] ?? 'A'}';
        _strength[ch] = (params?['value'] as int? ?? 0).clamp(0, 200);
        _emit('snapshot', _snapshot());
        _log('info', '强度 $ch → ${_strength[ch]}');
      case 'engine.emergency_stop':
        _strength['A'] = 0;
        _strength['B'] = 0;
        _emit('snapshot', _snapshot());
        _log('warn', '紧急停止已触发');
      case 'engine.set_led_color':
        // 引擎把颜色键写回槽位 props，界面按它选中并画色点；不回传的话下拉永远停在「熄灭」。
        _ovcProps['led'] = '${params?['color'] ?? ''}';
        _emit('snapshot', _snapshot());
        _log('info', 'slot_3 LED 颜色 → ${params?['color']}');
      case 'engine.modules_list':
        return _modules;
      case 'modules.install':
        return _setModuleState('${params?['module_id'] ?? ''}',
            enabled: true, loaded: true, running: true);
      case 'modules.deactivate':
      case 'modules.uninstall':
        return _setModuleState('${params?['module_id'] ?? ''}',
            enabled: false, loaded: false, running: false);
      case 'modules.delete':
        // 卸载 = 文件也没了：列表里那条要真的消失，市场那颗按钮才会翻回「安装」。
        final id = '${params?['module_id'] ?? ''}';
        _modules.removeWhere((m) => m['id'] == id);
        _emit('modules', _modules);
        _emit('profiles', {'module': id});
        return _modules;
      case 'modules.set_enabled':
        final on = params?['enabled'] == true;
        return _setModuleState('${params?['module_id'] ?? ''}',
            enabled: on, loaded: on, running: on);
      case 'engine.config_get':
        return _config();
      case 'engine.config_set':
        // 设置页改了「本地中继」就要立刻回一份新配置：连接页按它选连接方法。
        final path = '${params?['path'] ?? ''}';
        if (path == 'connection.v4_local') {
          _relayLocal['v4'] = params?['value'] == true;
        }
        if (path == 'connection.v3_local') {
          _relayLocal['v3'] = params?['value'] == true;
        }
        _emit('config', _config());
        return _config();

      case 'flow.state':
        return _flowState('${params?['page'] ?? 'input'}');
      case 'flow.vars':
        return _vars();
      case 'flow.live':
        // 演示数据没有节拍，给每张卡一个会走的数，界面按文本显示。
        // 红字与灰显跟着一起回：界面每一拍都按这份收敛，不然注释会停在旧快照上。
        final mark = _flowDiagnose();
        return {
          'values': {
            for (final n in _flowNodes) '${n['id']}': ['${++_flowLiveTick}'],
          },
          'errors': mark.errors,
          'idle': mark.idle.toList(),
          'catalog_rev': catalogRev,
        };
      case 'flow.dataflow':
        // 演示数据没有真画布，给两条引擎同形的线路：一条进核心、一条出核心。
        return {
          'lines': [
            {
              'kind': 'to_core',
              'core': '郊狼通道 A 强度',
              'coreShort': 'CORE',
              'other': 'OSC 强度',
              'otherShort': 'OSC',
              'count': 1,
            },
            {
              'kind': 'from_core',
              'core': '郊狼电量百分比',
              'coreShort': 'CORE',
              'other': 'OSC 电量',
              'otherShort': 'OSC',
              'count': 2,
            },
          ],
        };
      case 'flow.var_add':
        return _varAdd(
          '${params?['name'] ?? ''}'.trim(),
          '${params?['note'] ?? ''}',
        );
      case 'flow.var_remove':
        _userVars.removeWhere((r) => r['name'] == params?['name']);
        return _vars();
      case 'flow.var_rename':
        return _varRename(
          '${params?['name'] ?? ''}',
          '${params?['new'] ?? ''}'.trim(),
        );
      case 'flow.var_dir':
        for (final r in _userVars) {
          if (r['name'] == params?['name']) r['dir'] = params?['dir'];
        }
        return _vars();
      case 'flow.node_add':
        final id = 'n${++_flowSeq}';
        _flowNodes.add({
          'id': id,
          'def': params?['def'],
          'x': params?['x'] ?? 0.0,
          'y': params?['y'] ?? 0.0,
          'params': Map<String, Object?>.from(
            params?['params'] as Map? ?? const {},
          ),
          'alias': '',
          'overrides': <String, Object?>{},
        });
        return _flowResult();
      case 'flow.paste':
        // 与桥那侧同形：界面给「剪贴板里的第几张」，这里换成新 id 再落图。
        final rows = params?['nodes'] as List? ?? const [];
        if (rows.isEmpty) {
          return {..._flowState('${params?['page'] ?? 'input'}'),
            'error': '剪贴板是空的'};
        }
        final index = <int, String>{};
        for (var i = 0; i < rows.length; i++) {
          final raw = Map<String, Object?>.from(rows[i] as Map);
          final id = 'n${++_flowSeq}';
          index[i] = id;
          _flowNodes.add({
            'id': id,
            'def': raw['def'],
            'x': ((raw['x'] as num?) ?? 0.0) + 34.0,
            'y': ((raw['y'] as num?) ?? 0.0) + 34.0,
            'params': Map<String, Object?>.from(
              raw['params'] as Map? ?? const {},
            ),
            'alias': '${raw['alias'] ?? ''}',
            'overrides': Map<String, Object?>.from(
              raw['overrides'] as Map? ?? const {},
            ),
          });
        }
        for (final row in params?['wires'] as List? ?? const []) {
          final w = (row as List).cast<Object?>();
          if (w.length != 4) continue;
          final src = index[(w[0] as num).toInt()];
          final dst = index[(w[2] as num).toInt()];
          if (src == null || dst == null) continue;
          _flowWires.add({
            'id': 'w$src-$dst',
            'src': [src, (w[1] as num).toInt()],
            'dst': [dst, (w[3] as num).toInt()],
            'type': 'float',
          });
        }
        return {..._flowState('${params?['page'] ?? 'input'}'), 'error': ''};
      case 'flow.nodes_move':
        final moves = params?['moves'];
        if (moves is Map) {
          for (final n in _flowNodes) {
            final pos = moves['${n['id']}'];
            if (pos is List && pos.length == 2) {
              n['x'] = (pos[0] as num).toDouble();
              n['y'] = (pos[1] as num).toDouble();
            }
          }
        }
        return _flowResult();
      case 'flow.node_remove':
        _flowNodes.removeWhere((n) => n['id'] == params?['id']);
        _flowWires.removeWhere(
          (w) =>
              (w['src'] as List).first == params?['id'] ||
              (w['dst'] as List).first == params?['id'],
        );
        return _flowResult();
      case 'flow.node_set':
        final p = params ?? const <String, Object?>{};
        for (final n in _flowNodes) {
          if (n['id'] != p['id']) continue;
          if (p['alias'] != null) n['alias'] = p['alias'];
          if (p['params'] is Map) {
            (n['params'] as Map).addAll(
              Map<String, Object?>.from(p['params'] as Map),
            );
          }
          if (p['overrides'] is Map) {
            (n['overrides'] as Map).addAll(
              Map<String, Object?>.from(p['overrides'] as Map),
            );
          }
        }
        return _flowResult();
      case 'flow.wire_add':
        _flowWires.add({
          'id': 'w${++_flowSeq}',
          'src': params?['src'],
          'dst': params?['dst'],
          'type': 'any',
        });
        return _flowResult();
      case 'flow.wire_remove':
        _flowWires.removeWhere((w) => w['id'] == params?['id']);
        return _flowResult();
      case 'flow.clear':
        _flowNodes.clear();
        _flowWires.clear();
        return _flowResult();
      case 'flow.profile_switch':
        final name = '${params?['name'] ?? ''}';
        if (_flowProfiles.contains(name)) _flowActive = name;
        return _flowState('${params?['page'] ?? 'input'}');
      case 'flow.profile_new':
        var name = '${params?['name'] ?? ''}'.trim();
        for (var i = 1; name.isEmpty || _flowProfiles.contains(name); i++) {
          name = '配置$i';
        }
        _flowProfiles.add(name);
        _flowActive = name;
        return {..._flowState('${params?['page'] ?? 'input'}'),
          'created': name,
          'error': ''};
      case 'flow.profile_rename':
        final from = '${params?['old'] ?? ''}';
        final to = '${params?['new'] ?? ''}'.trim();
        final at = _flowProfiles.indexOf(from);
        final why = at < 0
            ? '找不到要改名的配置'
            : to.isEmpty
            ? '名称不能为空'
            : _flowProfiles.contains(to)
            ? '已存在同名配置「$to」'
            : '';
        if (why.isEmpty) {
          _flowProfiles[at] = to;
          if (_flowActive == from) _flowActive = to;
        }
        return {..._flowState('${params?['page'] ?? 'input'}'), 'error': why};
      case 'flow.profile_remove':
        final name = '${params?['name'] ?? ''}';
        final why = !_flowProfiles.contains(name)
            ? '没有配置「$name」'
            : _flowProfiles.length <= 1
            ? '至少要保留一份配置'
            : '';
        if (why.isEmpty) {
          _flowProfiles.remove(name);
          // 与桥那侧同规矩：删的是当前这份就切到剩下第一份，不叫用户先切再删。
          if (_flowActive == name) _flowActive = _flowProfiles.first;
        }
        return {..._flowState('${params?['page'] ?? 'input'}'), 'error': why};
      case 'bindings.get':
        return _bindings();
      case 'bindings.set':
        final bit = '${params?['bit'] ?? ''}';
        final action = '${params?['action'] ?? 'none'}';
        final rows = Map<String, String>.of(
          _bindingProfiles[_bindingActive] ?? const {},
        );
        if (action.isEmpty || action == 'none') {
          rows.remove(bit);
        } else {
          rows[bit] = action;
        }
        _bindingProfiles[_bindingActive] = rows;
        return _bindings();
      case 'bindings.profile_new':
        final name = '${params?['name'] ?? ''}'.trim().isEmpty
            ? '映射 ${_bindingProfiles.length + 1}'
            : '${params?['name']}'.trim();
        _bindingProfiles.putIfAbsent(name, () => {});
        _bindingActive = name;
        return _bindings();
      case 'bindings.profile_rename':
        final from = '${params?['name'] ?? ''}';
        final to = '${params?['new'] ?? ''}'.trim();
        if (_bindingProfiles.containsKey(from) &&
            to.isNotEmpty &&
            !_bindingProfiles.containsKey(to)) {
          _bindingProfiles[to] = _bindingProfiles.remove(from)!;
          if (_bindingActive == from) _bindingActive = to;
        }
        return _bindings();
      case 'bindings.profile_switch':
        final name = '${params?['name'] ?? ''}';
        if (_bindingProfiles.containsKey(name)) _bindingActive = name;
        return _bindings();
      case 'bindings.profile_remove':
        final name = '${params?['name'] ?? ''}';
        if (_bindingProfiles.length > 1) {
          _bindingProfiles.remove(name);
          if (_bindingActive == name) {
            _bindingActive = _bindingProfiles.keys.first;
          }
        }
        return _bindings();
      case 'detectors.state':
        return _detectors();
      case 'detectors.add':
        final used = {for (final r in _detectorRows) '${r['name']}'};
        var n = 1;
        while (used.contains('param$n')) {
          n++;
        }
        _detectorRows.add({
          'name': 'param$n',
          'kind': '${params?['kind'] ?? 'color'}',
          'rect': const [40, 40, 60, 24],
          'values': {'color': 'FF0000', 'tol': 40, 'ratio': 5},
        });
        return _detectors();
      case 'detectors.update':
        final index = (params?['index'] as num?)?.round() ?? -1;
        if (index < 0 || index >= _detectorRows.length) return _detectors();
        final patch = Map<String, Object?>.from(
          params?['patch'] as Map? ?? const {},
        );
        final row = Map<String, Object?>.of(_detectorRows[index]);
        if (patch['name'] != null) row['name'] = '${patch['name']}';
        if (patch['kind'] != null) row['kind'] = '${patch['kind']}';
        if (patch.containsKey('rect')) {
          final raw = patch['rect'];
          if (raw is List) {
            row['rect'] = [for (final v in raw) (v as num).round()];
          } else if (raw is String && raw.trim().isNotEmpty) {
            row['rect'] = [
              for (final p in raw.split(',')) int.tryParse(p.trim()) ?? 0,
            ];
          } else {
            row['rect'] = null;
          }
        }
        final values = Map<String, Object?>.of(
          Map<String, Object?>.from(row['values'] as Map? ?? const {}),
        );
        for (final e in (patch['values'] as Map? ?? const {}).entries) {
          values['${e.key}'] = e.value;
        }
        row['values'] = values;
        _detectorRows[index] = row;
        return _detectors();
      case 'detectors.remove':
        final index = (params?['index'] as num?)?.round() ?? -1;
        if (index >= 0 && index < _detectorRows.length) {
          _detectorRows.removeAt(index);
        }
        return _detectors();
      case 'detectors.shot':
        // 假数据不真截屏：界面按「截图失败」回落到手填 x,y,w,h。
        return {'path': '', 'width': 0, 'height': 0};
      case 'detectors.crop':
        return _detectors();
      case 'engine.channels':
        return [
          for (final r in _channelRows)
            if (!_channelsOff.contains(r['key'])) r,
        ];
      case 'engine.local_ip':
        return {'ip': '192.168.1.20'};
      case 'engine.qr':
        final want = '${params?['channel'] ?? ''}';
        final row = _channelRows.firstWhere(
          (r) => r['key'] == want && '${r['qr_text'] ?? ''}'.isNotEmpty,
          orElse: () => const {},
        );
        // 真桥有文字就一定能出图（qrcode + PIL），假数据也要给一张 PNG，
        // 否则连接页「链接一到就自动显示二维码」这条路径测不到。
        return {
          'channel': row['key'] ?? want,
          'text': '${row['qr_text'] ?? ''}',
          'png': '${row['qr_text'] ?? ''}'.isEmpty ? '' : _fakePng,
        };
      case 'engine.disconnect_channel':
        _channelsOff.add('${params?['channel'] ?? ''}');
        _emit('snapshot', _snapshot());
        return {'ok': true};
      case 'engine.connect_v4':
      case 'engine.connect_v3':
      case 'engine.connect_v4_local':
      case 'engine.connect_v3_local':
        _channelsOff.remove('${params?['channel'] ?? ''}');
        _emit('snapshot', _snapshot());
        return {'ok': true};
      case 'engine.wave_history':
        final tick = ++_waveTick;
        return {
          'window_s': 5.0,
          'samples': [
            for (var i = 0; i < 12; i++)
              [
                (i - 12) * 0.4,
                [((i + tick) % 5) * 18],
                [((i * 2 + tick) % 4) * 22],
              ],
          ],
        };
      case 'engine.intensity_params':
        final ovc = '${params?['slot_id'] ?? ''}' == 'slot_3';
        return {
          'slot_id': params?['slot_id'],
          'step': ovc ? 10 : 1,
          'rows': [
            {
              'key': 'max_strength',
              'label': '强度上限',
              'value': ovc ? 100 : 200,
              'min': ovc ? 10 : 0,
              'max': 200,
              'float': false,
            },
            {
              'key': 'fire_strength_a',
              'label': '触发 A 电平',
              'value': 0,
              'min': 0,
              'max': 200,
              'float': false,
            },
            {
              'key': 'fire_strength_b',
              'label': '触发 B 电平',
              'value': 0,
              'min': 0,
              'max': 200,
              'float': false,
            },
            {
              'key': 'strength_step',
              'label': '调节步进',
              'value': ovc ? 10 : 1,
              'min': ovc ? 10 : 1,
              'max': 50,
              'float': false,
            },
            {
              'key': 'fire_duration_s',
              'label': '触发时长 (秒)',
              'value': 1.0,
              'min': 0.1,
              'max': 60,
              'float': true,
            },
          ],
        };
      case 'modules.apply_defaults':
        return {
          'notes': const ['模块 alice_cradle 的事件流已套用默认配置「默认事件流」'],
          'modules': _modules,
        };
      case 'modules.deps':
        return _extras('${params?['module_id'] ?? ''}');
      case 'modules.deps_install':
        _depsReady.add('${params?['module_id'] ?? ''}');
        return _extras('${params?['module_id'] ?? ''}');
      case 'modules.mods_scan':
        return {
          ..._extras('${params?['module_id'] ?? ''}'),
          'found': const [
            'C:/Games/AliceInCradle',
            r'D:\SteamLibrary\AliceInCradle',
          ],
        };
      case 'modules.mods_install':
        _modsRoot['${params?['module_id'] ?? ''}'] = '${params?['root'] ?? ''}';
        return {..._extras('${params?['module_id'] ?? ''}'), 'count': 3};
      case 'modules.readme':
        // 演示数据里只给画面识别那个模块带一份 README，其余照实报缺。
        final id = '${params?['module_id'] ?? ''}';
        if (id != 'vision_link') {
          return {
            'module_id': id,
            'path': '',
            'text': '',
            'size': 0,
            'missing': true,
            'truncated': false,
          };
        }
        return {
          'module_id': id,
          'path': 'vision_link/README.md',
          'text': '# 画面识别联动\n\n'
              '按识别区域给出画面亮度 / 内容判定，供事件流当输入。\n\n'
              '## 依赖\n\n- opencv-python-headless>=4.10\n',
          'size': 96,
          'missing': false,
          'truncated': false,
        };
      case 'modules.settings_get':
        final id = '${params?['module_id'] ?? ''}';
        return {
          'spec': _settingsSpec,
          'settings': _settingsStore[id] ??= {
            'out_ip': '127.0.0.1',
            'rate_hz': 10,
            'device_prefixes': {
              'COYOTE': 'DGLab',
              'OVC': 'DGLabOvc',
              'BMTR': 'DGLabBmtr',
            },
            // 设备参数那一栏是引擎按当前在连设备现算的，界面只读它。
            // 给到 7 条：正好越过「最多露出 5 行」这条线，用来验证表体滚动。
            'device_params': [
              {
                'key': 'COYOTE.Battery',
                'address': 'avatar/parameters/DGLabBattery',
                'dir': 'out',
                'type': 'Int',
                'label': 'OSC 可写 · 显示电量百分比',
              },
              {
                'key': 'COYOTE.LimitA',
                'address': 'avatar/parameters/DGLabLimitA',
                'dir': 'out',
                'type': 'Int',
                'label': 'OSC 可写 · 输出通道 A 强度上限',
              },
              {
                'key': 'in_strength_a',
                'address': 'avatar/parameters/DGLabStrengthA',
                'dir': 'inout',
                'type': 'Float',
                'label': 'OSC 读写 · 输出通道 A 当前强度',
              },
              {
                'key': 'in_fire_a',
                'address': 'avatar/parameters/DGLabFireA',
                'dir': 'in',
                'type': 'Bool',
                'label': 'OSC 可读 · 输出开火 A',
              },
              {
                'key': 'COYOTE.ChannelOK_A',
                'address': 'avatar/parameters/DGLabChannelOK_A',
                'dir': 'out',
                'type': 'Bool',
                'label': 'OSC 可写 · 在线通道 A 探针',
              },
              {
                'key': 'in_wave_a',
                'address': 'avatar/parameters/DGLabWaveA',
                'dir': 'in',
                'type': 'Int',
                'label': 'OSC 可读 · 输出通道 A 波形选择',
              },
              {
                'key': 'Action',
                'address': 'DGLab/Action',
                'dir': 'out',
                'type': 'Int',
                'label': 'OSC 可写 · App 按键反馈',
              },
            ],
            'custom_params': [
              {
                'address': 'avatar/parameters/Heard',
                'dir': 'in',
                'type': 'Float',
              },
            ],
          },
        };
      case 'modules.settings_set':
        final id = '${params?['module_id'] ?? ''}';
        final store = _settingsStore[id] ??= <String, Object?>{};
        store['${params?['key'] ?? ''}'] = params?['value'];
        return {'ok': true, 'settings': store};
    }
    return {'ok': true};
  }

  // ------------------------------------------------------------ 按键映射假数据

  String _bindingActive = '默认';
  final Map<String, Map<String, String>> _bindingProfiles = {
    '默认': {'13': 'fire_a'},
  };

  static const _bindingButtons = [
    {'bit': '0', 'label': 'SEL_1'},
    {'bit': '1', 'label': 'SEL_2'},
    {'bit': '2', 'label': 'HOME'},
    {'bit': '8', 'label': 'Up'},
    {'bit': '9', 'label': 'Down'},
    {'bit': '10', 'label': 'Left'},
    {'bit': '11', 'label': 'Right'},
    {'bit': '12', 'label': 'B'},
    {'bit': '13', 'label': 'A'},
    {'bit': '14', 'label': 'G'},
    {'bit': '15', 'label': 'D'},
  ];

  /// 与桥那侧同一份形状：`argument` 非空表示这个动作还要一个参数
  /// （键盘动作收键名，模块动作收 OSC 路径）。
  static const _bindingActions = [
    {'key': 'none', 'label': '无', 'builtin': true},
    {'key': 'a_strength_up', 'label': 'A 通道强度 +10', 'builtin': true},
    {'key': 'fire_a', 'label': '持续开火 A (按住，仅 A 通道)', 'builtin': true},
    {'key': 'estop', 'label': '急停', 'builtin': true},
    {
      'key': 'key',
      'label': '模拟键盘按键…',
      'builtin': false,
      'argument': '点击此处后按下要绑定的按键',
    },
    {
      'key': 'osc',
      'label': '发送 OSC 参数',
      'builtin': false,
      'argument': '/avatar/parameters/...',
    },
    {'key': 'margin_guard_toggle', 'label': '边控闭环 暂停/恢复', 'builtin': false},
  ];

  Map<String, Object?> _bindings() => {
    'active': _bindingActive,
    'order': _bindingProfiles.keys.toList(),
    'bindings': Map<String, String>.of(
      _bindingProfiles[_bindingActive] ?? const {},
    ),
    'buttons': _bindingButtons,
    'actions': _bindingActions,
    'keys': const ['A', 'B', 'Space', 'Enter', 'F5'],
    'missing': const <String, String>{},
    'connected': true,
  };

  // ---------------------------------------------------------- 识别参数假数据

  final List<Map<String, Object?>> _detectorRows = [
    {
      'name': 'boss_hp',
      'kind': 'bar',
      'rect': [1180, 44, 260, 18],
      'values': {
        'min': 1180,
        'max': 1440,
        'anchor': 'boss_hp_anchor.png',
        'thresh': 70,
      },
    },
    {
      'name': 'ready',
      'kind': 'color',
      'rect': [64, 300, 40, 40],
      'values': {'color': '3ADF6E', 'tol': 40, 'ratio': 6},
    },
  ];
  int _detectorTick = 0;
  int _waveTick = 0;

  static const _detectorKinds = [
    {'key': 'color', 'label': '检测颜色'},
    {'key': 'image', 'label': '检测图片'},
    {'key': 'number', 'label': '检测数值'},
    {'key': 'bar', 'label': '检测数值条'},
  ];

  // 与 bridge/detectors.py 同一张表：字段类型决定控件，界面不写死行为。
  static const _detectorFields = <String, Object?>{
    'color': [
      {'key': 'color', 'label': '颜色 #RRGGBB', 'type': 'text', 'width': 96},
      {
        'key': 'tol',
        'label': '容差',
        'type': 'int',
        'min': 0,
        'max': 255,
        'default': 40,
      },
      {
        'key': 'ratio',
        'label': '占比',
        'type': 'percent',
        'min': 1,
        'max': 100,
        'default': 5,
      },
    ],
    'image': [
      {'key': 'file', 'label': '例图', 'type': 'file'},
      {
        'key': 'thresh',
        'label': '匹配阈值',
        'type': 'percent',
        'min': 5,
        'max': 100,
        'default': 80,
      },
    ],
    'number': [
      {
        'key': 'fmt',
        'label': '输出类型',
        'type': 'choice',
        'options': [
          ['int', '整数'],
          ['float', '小数'],
          ['text', '文字'],
        ],
      },
      {
        'key': 'text',
        'label': '期望文本',
        'type': 'text',
        'width': 110,
        'when': ['fmt', 'text'],
      },
      {
        'key': 'thresh',
        'label': '匹配阈值',
        'type': 'percent',
        'min': 5,
        'max': 100,
        'default': 60,
      },
    ],
    'bar': [
      {
        'key': 'min',
        'label': '0% 位置(x/y)',
        'type': 'int',
        'min': -99999,
        'max': 99999,
        'default': 0,
      },
      {
        'key': 'max',
        'label': '100% 位置(x/y)',
        'type': 'int',
        'min': -99999,
        'max': 99999,
        'default': 100,
      },
      {'key': 'anchor', 'label': '锚点例图', 'type': 'file'},
      {
        'key': 'thresh',
        'label': '匹配阈值',
        'type': 'percent',
        'min': 5,
        'max': 100,
        'default': 70,
      },
    ],
  };

  Map<String, Object?> _detectors() {
    _detectorTick++;
    return {
      'module_id': 'vision_link',
      'supported': true,
      'kinds': _detectorKinds,
      'fields': _detectorFields,
      'rows': [
        for (var i = 0; i < _detectorRows.length; i++)
          {
            ..._detectorRows[i],
            'index': i,
            'error': '',
            'live': _detectorLive('${_detectorRows[i]['kind']}', i),
          },
      ],
    };
  }

  String _detectorLive(String kind, int index) {
    if (kind == 'bar') return (0.42 + 0.01 * index).toStringAsFixed(2);
    if (kind == 'number') return '${120 + _detectorTick}';
    return _detectorTick.isEven ? '真' : '假';
  }

  // ------------------------------------------------------ 依赖与模组假数据

  static const _depsTable = <String, Object?>{
    'vision_link': {
      'dir': 'modules/vision_link/_deps',
      'abi_ok': true,
      'source': 'requirements.txt',
      'rows': [
        {'req': 'numpy', 'optional': false, 'ok': false},
        {'req': 'pillow', 'optional': false, 'ok': true},
        {'req': 'rapidocr-onnxruntime', 'optional': true, 'ok': false},
      ],
    },
  };

  static const _modsTable = <String, Object?>{
    'alice_cradle': {
      'declared': true,
      'dir': r'M:\games\Alice\mods',
      'marker': 'BepInEx',
      'dest': r'BepInEx\plugins',
      'root': r'C:\Games\AliceInCradle',
    },
  };

  /// 装过依赖的模块：假数据里装完就把必选依赖全标成已满足。
  final Map<String, Map<String, Object?>> _settingsStore = {};

  /// 与 osc_bridge 的 META["config"] 同形状：列表型设置项带 columns。
  static const Map<String, Object?> _settingsSpec = {
    'out_ip': {'label': 'VRChat 地址', 'type': 'str', 'desc': 'OSC 输出目标 IP'},
    'rate_hz': {
      'label': '发送频率',
      'type': 'int',
      'min': 1,
      'max': 30,
      'desc': '',
    },
    'device_prefixes': {
      'label': '设备参数前缀',
      'type': 'map',
      'desc': '设备头像参数的默认名字前缀',
    },
    'device_params': {
      'label': '设备 OSC 参数',
      'type': 'list',
      'fixed': true,
      'desc': '设备连上后自动登记，只可改名',
      'columns': [
        {'id': 'address', 'label': '变量名', 'type': 'str'},
        {
          'id': 'dir',
          'label': '读写',
          'type': 'choice',
          'readonly': true,
          'choices': ['in', 'out', 'inout'],
          'labels': ['只收', '只发', '收发'],
        },
        {
          'id': 'type',
          'label': '类型',
          'type': 'choice',
          'readonly': true,
          'choices': ['Float', 'Int', 'Bool'],
          'labels': ['浮点', '整数', '布尔'],
        },
      ],
    },
    'custom_params': {
      'label': '自定义 OSC 参数',
      'type': 'list',
      'desc': '填完整 OSC 地址即可建立参数',
      'columns': [
        {
          'id': 'address',
          'label': 'OSC 地址',
          'type': 'str',
          'hint': 'avatar/parameters/MyParam',
        },
        {
          'id': 'dir',
          'label': '方向',
          'type': 'choice',
          'choices': ['in', 'out', 'inout'],
          'labels': ['只收', '只发', '收发'],
        },
        {
          'id': 'type',
          'label': '类型',
          'type': 'choice',
          'choices': ['Float', 'Int', 'Bool'],
          'labels': ['浮点', '整数', '布尔'],
        },
      ],
    },
  };

  final Set<String> _depsReady = {};
  final Map<String, String> _modsRoot = {};

  Map<String, Object?> _extras(String moduleId) {
    final deps = Map<String, Object?>.from(
      _depsTable[moduleId] as Map? ?? const {},
    );
    final rows = [
      for (final r in (deps['rows'] as List? ?? const []))
        if (r is Map)
          {
            ...Map<String, Object?>.from(r),
            if (_depsReady.contains(moduleId) && r['optional'] != true)
              'ok': true,
          },
    ];
    final mods = Map<String, Object?>.from(
      _modsTable[moduleId] as Map? ?? const {'declared': false},
    );
    final root = _modsRoot[moduleId];
    return {
      'module_id': moduleId,
      'deps': {
        ...deps,
        'rows': rows,
        'missing': [
          for (final r in rows)
            if (r['ok'] != true && r['optional'] != true) r['req'],
        ],
      },
      'mods': {...mods, 'root': ?root},
    };
  }

  // ---------------------------------------------------------- 事件流假数据

  final List<Map<String, Object?>> _flowNodes = [];
  final List<Map<String, Object?>> _flowWires = [];
  final List<Map<String, Object?>> _userVars = [
    {
      'name': 'combo',
      'label': '连击',
      'dir': 'inout',
      'type': '',
      'mid': '',
      'source': '临时变量',
      'renamable': true,
      'value': 3.0,
      'card': 'var.read.combo',
    },
  ];
  final List<Map<String, Object?>> _systemVars = [
    {
      'name': 'Coyote.StrengthA',
      'label': '强度 A',
      'dir': 'out',
      'type': 'float',
      'mid': '',
      'source': '核心',
      'renamable': false,
      'value': 12.0,
      'card': 'var.read.Coyote.StrengthA',
    },
    // 模块登记的 OSC 地址：落在系统变量组，名字改得，类型与读写改不得。
    {
      'name': 'avatar/parameters/DGLabBattery',
      'label': 'OSC 可写 · 设备电量',
      'dir': 'out',
      'type': 'int',
      'mid': 'osc_bridge',
      'source': 'VRChat OSC 联动（osc_bridge）',
      'renamable': true,
      'value': 72.0,
      'card': 'mod.read.osc_bridge.avatar/parameters/DGLabBattery',
    },
  ];
  int _flowSeq = 0;
  int _flowLiveTick = 0;

  static const _flowCatalog = <Map<String, Object?>>[
    {
      'key': 'var.const_float',
      'title': '数值',
      'cat': '常数',
      'tip': '一个常数',
      'op': 'const',
      'outputs': [
        {'name': '值', 'type': 'float'},
      ],
      'fields': [
        {'id': 'v', 'label': '值', 'type': 'float', 'default': 0},
      ],
    },
    {
      'key': 'math.add',
      'title': '相加',
      'en': 'Add',
      'cat': '数学',
      'tip': '两个数相加',
      'op': 'add',
      'inputs': [
        {'name': '甲', 'type': 'float'},
        {'name': '乙', 'type': 'float'},
      ],
      'outputs': [
        {'name': '和', 'type': 'float'},
      ],
    },
    {
      'key': 'driver.period',
      'title': '周期更新',
      'en': 'Period',
      'cat': '模块事件',
      'tip': '按周期触发一次',
      'op': 'driver_period',
      'entry': true,
      'outputs': [
        {'name': '触发', 'type': 'exec'},
      ],
      'fields': [
        {'id': 'seconds', 'label': '周期(秒)', 'type': 'float', 'default': 1},
      ],
    },
    {
      'key': 'core.strength',
      'title': '强度写入',
      'en': 'Set Strength',
      'cat': '核心写入',
      'op': 'core_write',
      'tip': '把值写到当前设备的某个通道',
      'exec_in': true,
      'sink': true,
      'inputs': [
        {'name': '强度', 'type': 'float'},
      ],
      'fields': [
        {
          'id': 'channel',
          'label': '通道',
          'type': 'choice',
          'choices': ['A', 'B'],
          'labels': ['A', 'B'],
        },
      ],
    },
  ];

  /// 目录里的变量卡跟着变量走，和引擎的 `_var_defs` 一样：登记一个变量就多出
  /// 一对读数 / 写入卡。
  List<Map<String, Object?>> _catalog() => [
    ..._flowCatalog,
    for (final r in _userVars) ...[
      {
        'key': 'var.read.${r['name']}',
        'title': '读数 · ${r['name']}',
        'cat': '临时变量',
        'palette': false,
        'outputs': [
          {'name': '值', 'type': 'float'},
        ],
        'fields': [
          {
            'id': 'name',
            'label': '变量',
            'type': 'text',
            'default': r['name'],
            'pool': 'var',
          },
        ],
      },
      {
        'key': 'var.write.${r['name']}',
        'title': '写入 · ${r['name']}',
        'cat': '临时变量',
        'palette': false,
        'exec_in': true,
        'sink': true,
        'inputs': [
          {'name': '值', 'type': 'float'},
        ],
        'fields': [
          {
            'id': 'name',
            'label': '变量',
            'type': 'text',
            'default': r['name'],
            'pool': 'var',
          },
        ],
      },
    ],
  ];

  /// 事件流配置档：新建 / 改名 / 删除都得在测试里看得见，所以是可变的一份。
  final List<String> _flowProfiles = ['默认'];
  String _flowActive = '默认';

  Map<String, Object?> _flowState(String page) {
    final mark = _flowDiagnose();
    return {
      'profiles': {
        'active': _flowActive,
        'order': List<String>.of(_flowProfiles),
      },
      'page': page,
      'graph': {'page': page, 'nodes': _flowNodes, 'wires': _flowWires},
      'catalog': _catalog(),
      'types': {'labels': <String, String>{}, 'colors': <String, String>{}},
      'vars': _vars(),
      'errors': mark.errors,
      'idle': mark.idle.toList(),
      'catalog_rev': catalogRev,
      'missing_modules': <String>[],
    };
  }

  Map<String, Object?> _flowResult() {
    final mark = _flowDiagnose();
    return {
      'error': '',
      'graph': {'page': 'input', 'nodes': _flowNodes, 'wires': _flowWires},
      'errors': mark.errors,
      'idle': mark.idle.toList(),
    };
  }

  /// 与引擎同一判据：执行流从驱动卡出发走不到的卡片要灰显；目标已经被走得到的
  /// 卡片占着时，这张不再补写。演示数据没有节拍，接线一变就当场算一次。
  ({Map<String, String> errors, Set<String> idle}) _flowDiagnose() {
    final defs = {for (final d in _catalog()) '${d['key']}': d};
    final nodes = {for (final n in _flowNodes) '${n['id']}': n};
    Map<String, Object?> defOf(Map<String, Object?> n) =>
        defs['${n['def']}'] ?? const {};
    bool isSink(Map<String, Object?> n) =>
        defOf(n)['sink'] == true && defOf(n)['exec_in'] == true;
    String target(Map<String, Object?> n) {
      final params = Map<String, Object?>.from(n['params'] as Map? ?? const {});
      final key = '${n['def']}';
      if (key.startsWith('var.write.')) {
        return '变量 ${params['name'] ?? key.substring('var.write.'.length)}';
      }
      if (defOf(n)['op'] == 'core_write') {
        final channel = '${params['channel'] ?? ''}';
        return channel.isEmpty ? '' : '核心参数 $channel';
      }
      return '';
    }

    final reached = <String>{};
    final queue = <String>[];
    for (final n in _flowNodes) {
      if (defOf(n)['entry'] == true) {
        reached.add('${n['id']}');
        queue.add('${n['id']}');
      }
    }
    for (var i = 0; i < queue.length; i++) {
      final from = queue[i];
      final outs =
          (defOf(nodes[from]!)['outputs'] as List? ?? const []).cast<Object?>();
      for (final w in _flowWires) {
        final src = (w['src'] as List).cast<Object?>();
        if ('${src[0]}' != from) continue;
        final index = (src[1] as num).toInt();
        if (index < 0 || index >= outs.length) continue;
        if (Map<String, Object?>.from(outs[index] as Map)['type'] != 'exec') {
          continue;
        }
        final to = '${(w['dst'] as List).cast<Object?>()[0]}';
        if (nodes.containsKey(to) && reached.add(to)) queue.add(to);
      }
    }
    final driven = <String>{
      for (final n in _flowNodes)
        if (isSink(n) && reached.contains('${n['id']}')) target(n),
    }..remove('');
    final errors = <String, String>{};
    final idle = <String>{};
    for (final n in _flowNodes) {
      final id = '${n['id']}';
      if (defOf(n)['exec_in'] != true || reached.contains(id)) continue;
      idle.add(id);
      if (!isSink(n)) {
        errors[id] = '无连线：这张卡没有执行流走到，不会触发';
        continue;
      }
      final own = target(n);
      // 目标没人管：这张虽然接不到执行流，仍是它每拍顶着值，不算失效。
      if (own.isEmpty || !driven.contains(own)) {
        idle.remove(id);
        continue;
      }
      errors[id] = '无连线：这张卡没有执行流走到，「$own」由执行流上的卡片写入';
    }
    // 同一条执行流上两张卡写同一个目标、值不一样：冲突的那几张一起标出来。
    final byTarget = <String, List<Map<String, Object?>>>{};
    for (final n in _flowNodes) {
      final own = target(n);
      if (own.isEmpty || !isSink(n) || !reached.contains('${n['id']}')) continue;
      byTarget.putIfAbsent(own, () => []).add(n);
    }
    for (final row in byTarget.values) {
      final values = row
          .map((n) =>
              Map<String, Object?>.from(n['overrides'] as Map? ?? const {})['0'])
          .where((v) => v != null)
          .toSet();
      if (row.length < 2 || values.length < 2) continue;
      for (final n in row) {
        final id = '${n['id']}';
        errors[id] = '写入冲突：同一执行流里有卡片用不同的值写「${target(n)}」，'
            '这几张卡本拍都不写入';
        idle.add(id);
      }
    }
    return (errors: errors, idle: idle);
  }

  Map<String, Object?> _vars() => {
    'declared': _systemVars,
    'renamable': _userVars,
  };

  Map<String, Object?> _varAdd(String name, String note) {
    if (name.isEmpty) return {'error': '变量名不能为空'};
    if (_userVars.any((r) => r['name'] == name)) return {'error': '已有同名变量'};
    _userVars.add({
      'name': name,
      'label': note,
      'dir': 'inout',
      'type': '',
      'mid': '',
      'source': '临时变量',
      'renamable': true,
      'value': 0.0,
      'card': 'var.read.$name',
    });
    return _vars()..['error'] = '';
  }

  Map<String, Object?> _varRename(String name, String next) {
    final all = [..._userVars, ..._systemVars];
    final row = all.where((r) => r['name'] == name).firstOrNull;
    if (row == null) return _vars()..['error'] = '找不到这个变量';
    if (row['renamable'] != true) {
      return _vars()..['error'] = '这个变量由模块登记，不能改名';
    }
    if (next.isEmpty) return _vars()..['error'] = '变量名需字母开头';
    if (all.any((r) => r['name'] == next)) return _vars()..['error'] = '已有同名变量';
    row['name'] = next;
    // 模块登记的卡片 key 里带着变量名（mod.read.<模块>.<名>），改名要一起换。
    final mid = '${row['mid'] ?? ''}';
    row['card'] = mid.isEmpty ? 'var.read.$next' : 'mod.read.$mid.$next';
    return _vars()..['error'] = '';
  }

  Map<String, Object?> _config() => {
    'relay': {
      'v3_port': 9999,
      'v4_port': 9998,
      // 设置页那两颗「本地中继」开关的真值；连接页的连接按钮按它选方法。
      'v4_local': _relayLocal['v4'] == true,
      'v3_local': _relayLocal['v3'] == true,
    },
    'v4_url': 'wss://trex.dungeon-lab.cn/v4',
    'v3_url': 'wss://ws.dungeon-lab.cn',
    'auto_reconnect': true,
    'waveforms': {
      'COYOTE': [
        {'name': '__SILENT__', 'label': '静默'},
        {'name': 'SHORT', 'label': '短促'},
        {'name': 'LONG', 'label': '绵长'},
        {'name': 'WAVE', 'label': '起伏'},
        {'name': 'CONTINUOUS', 'label': '持续'},
        {'name': 'PULSE_STREAM', 'label': '外部脉冲流'},
      ],
      'OVC': [
        {'name': '__SILENT__', 'label': '静默'},
        {'name': 'SOUL', 'label': '灵犀'},
        {'name': 'KISS', 'label': '轻吻'},
        {'name': 'CONTINUOUS', 'label': '持续'},
        {'name': 'PULSE_STREAM', 'label': '外部脉冲流'},
      ],
    },
    'wave_selection': {'A': '__SILENT__', 'B': '__SILENT__'},
    'led_options': [
      {'key': 'off', 'label': '熄灭', 'color': '#9AA0A6'},
      {'key': 'yellow', 'label': '黄色', 'color': '#F2C94C'},
      {'key': 'magenta', 'label': '红色', 'color': '#EB5757'},
      {'key': 'purple', 'label': '紫色', 'color': '#9B51E0'},
      {'key': 'blue', 'label': '蓝色', 'color': '#2F80ED'},
      {'key': 'cyan', 'label': '青色', 'color': '#27C4D3'},
      {'key': 'green', 'label': '绿色', 'color': '#27AE60'},
    ],
    'saved_devices': [
      {'address': 'AA:BB:CC:DD:EE:01', 'kind': 'coyote_v3', 'name': '郊狼 · 备'},
      {'address': 'slot_3', 'kind': 'ovc', 'name': '负鼠振动'},
    ],
    'theme': 'dark',
    'settings': {
      'appearance.dark': true,
      'appearance.grid': true,
      'appearance.drawer': true,
      'connection.v4_port': 9998,
    },
  };

  Map<String, Object?> _snapshot() => {
    'backend': 'v4',
    'connected': true,
    'paired': true,
    'status_text': '已连接 · 中继 trex.dungeon-lab.cn',
    'client_id': 'studio-mock',
    'target_id': 'slot-demo',
    'qr_text': '',
    'active_slot': 'slot_1',
    'slots': {
      'slot_1': {
        'slot_id': 'slot_1',
        'name': '项圈 · 主',
        'type': 'COYOTE-V4',
        'strength': Map<String, int>.of(_strength),
        'strength_limit': {'A': 200, 'B': 200},
        'channel_status': {'A': 0, 'B': 0},
        'battery': _battery,
        'props': {},
        'slot_state': {},
      },
      'slot_2': {
        'slot_id': 'slot_2',
        'name': '压力传感器',
        'type': 'BMTR-01',
        'strength': {'A': 0, 'B': 0},
        'strength_limit': {'A': 0, 'B': 0},
        'channel_status': {'A': 0, 'B': 0},
        'battery': 64,
        'pressure': 12.4 + (_battery % 5) / 10,
        'props': {},
        'slot_state': {},
      },
      // 负鼠：控制页只有见到 OVC 才挂按键映射卡。
      'slot_3': {
        'slot_id': 'slot_3',
        'name': '负鼠振动',
        'type': 'OVC-3',
        'strength': {'A': 0, 'B': 0},
        'strength_limit': {'A': 100, 'B': 100},
        'channel_status': {'A': 0, 'B': 0},
        'battery': 55,
        'props': Map<String, Object?>.of(_ovcProps),
        'slot_state': {},
      },
    },
    // 多通道并存：郊狼与灵猫走 v4，负鼠走蓝牙，各自一份通道状态。
    'slot_channels': slotChannels,
    'channels': [
      for (final row in _channelRows)
        if (!_channelsOff.contains(row['key'])) row,
    ],
  };

  /// 断开过的通道：假数据也按通道各断各的，连接页才测得出「只断一条」。
  final Set<String> _channelsOff = {};

  /// 槽位挂在哪条通道上。测试里把灵猫改成蓝牙直连，
  /// 才能核对只有蓝牙才有的那几行布局。
  final Map<String, String> slotChannels = {
    'slot_1': 'v4',
    'slot_2': 'v4',
    'slot_3': 'ble',
  };

  /// 卡片目录的版本：测试里加一，验证事件流页会因为目录换代而整份重取。
  int catalogRev = 1;

  /// 设置页「本地中继」开关的假状态。
  final Map<String, bool> _relayLocal = {};

  /// 负鼠槽位的 props：LED 颜色切完要能回显，假数据也得存住这一笔。
  final Map<String, Object?> _ovcProps = {};

  /// 一张 1×1 的 PNG：假数据只用来证明「拿到图就画出来」这条链路通了。
  static const _fakePng =
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

  static const _channelRows = <Map<String, Object?>>[
    {
      'key': 'v4',
      'label': 'Socket V4',
      'backend': 'v4',
      'present': true,
      'primary': true,
      'connected': true,
      'paired': true,
      'status_text': '已连接 · 中继 trex.dungeon-lab.cn',
      'client_id': 'studio-mock',
      'target_id': 'slot-demo',
      'qr_text': 'https://dungeon-lab.cn/s/?v=1&action=socket&url=ws://192.168.1.20:9999',
      'slots': ['slot_1', 'slot_2'],
      'local_relay': false,
    },
    {
      'key': 'v3',
      'label': 'Socket V3',
      'backend': 'v3',
      'present': false,
      'primary': false,
      'connected': false,
      'status_text': '未启用',
      'slots': [],
      'local_relay': false,
    },
    {
      'key': 'ble',
      'label': '蓝牙直连',
      'backend': 'ble',
      'present': true,
      'primary': false,
      'connected': true,
      'status_text': '已连接 1 台',
      'slots': ['slot_3'],
      'local_relay': false,
    },
  ];

  /// 假模块表：卸载要真的少一条、启用要改状态，测试才看得出按钮有没有生效。
  final List<Map<String, Object?>> _modules = [
    for (final m in _seedModules) Map<String, Object?>.of(m),
  ];

  static const List<Map<String, Object?>> _seedModules = [
    {
      'id': 'osc_bridge',
      'name': 'OSC 桥',
      'short': 'OSC',
      'version': '1.2.0',
      'description': '与 Resonate/OSC 程序互通强度与温度',
      'enabled': true,
      'loaded': true,
      'running': true,
      'actions': ['入站 9001'],
    },
    {
      'id': 'sound_link',
      'name': '声音联动',
      'version': '0.9.3',
      'description': '按音量的低频包络驱动强度',
      'enabled': true,
      'loaded': true,
      'running': false,
      'actions': [],
    },
    {
      'id': 'vision_link',
      'name': '视觉联动',
      'version': '0.4.1',
      'description': '从屏幕取色与姿态事件',
      'enabled': true,
      'loaded': true,
      'running': true,
      'realtime_manager': true,
      'actions': [],
    },
    // 列表要能溢出卡片高度，滚动归属的测试才测得出内层与页面的分工。
    {
      'id': 'alice_cradle', 'name': 'Alice in Cradle 联动', 'version': '0.7.2',
      'description': '游戏侧上报 HP/MP 等数值', 'enabled': false, 'loaded': true,
      'running': false, 'actions': [],
      // 模块自带 flow/ 与 bindings/ 时的形状：模块卡要列出来并给「一键套用」。
      'defaults': {
        'flow': ['默认事件流'],
        'bindings': ['默认按键映射'],
      },
    },
    {
      'id': 'margin_control',
      'name': '灵猫边控联动',
      'version': '0.13.2',
      'description': '气压设备的边控参数',
      'enabled': false,
      'loaded': true,
      'running': false,
      'actions': [],
    },
    {
      'id': 'strength_logger',
      'name': '强度日志示例',
      'version': '0.1.0',
      'description': '把强度变化写成日志',
      'enabled': false,
      'loaded': true,
      'running': false,
      'actions': [],
    },
    {
      'id': 'xinput_oscillate',
      'name': '手柄震动联动',
      'version': '0.3.1',
      'description': 'XInput 震动强度回写',
      'enabled': false,
      'loaded': true,
      'running': false,
      'actions': [],
    },
  ];

  /// 改一条模块行的开关状态并回整张表（与桥那侧 `modules_payload` 同形）。
  List<Map<String, Object?>> _setModuleState(
    String id, {
    required bool enabled,
    required bool loaded,
    required bool running,
  }) {
    for (final row in _modules) {
      if (row['id'] != id) continue;
      row['enabled'] = enabled;
      row['loaded'] = loaded;
      row['running'] = running;
    }
    _emit('modules', _modules);
    return _modules;
  }

  void _log(String level, String text) => _emit('log', [
    DateTime.now().millisecondsSinceEpoch / 1000.0,
    level,
    text,
  ]);

  void _emit(String event, Object? data) {
    if (!_pushes.isClosed) _pushes.add(BridgeMessage.event(event, data));
  }

  @override
  Stream<BridgeMessage> get pushes => _pushes.stream;

  @override
  Stream<void> get closed => _closed.stream;

  @override
  Future<void> dispose() async {
    _beat?.cancel();
    await _pushes.close();
    await _closed.close();
  }
}
