import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/painting.dart';

import '../app/tokens.dart';
import '../engine/engine_state.dart';
import '../graph/graph_model.dart';
import 'flow_models.dart';

/// 事件流编辑器：画布是引擎图的一份镜像，改动都发给引擎，再把回传的结果
/// 原地同步回来——连线规则、参数取值、求值都只有引擎一个权威。
class FlowEditor extends ChangeNotifier {
  FlowEditor({required this.engine, required this.graph, this.onLayout}) {
    graph.addListener(_onGraphChanged);
    graph.onDuplicate = duplicateSelected;
    engine.addListener(_onEnginePush);
  }

  /// 配置档列表只有引擎知道：模块停用把注入的那份收回去时，重拉一次当前页，
  /// 下拉里才不会留着一份已经不存在的配置。
  int _profilesRev = 0;

  void _onEnginePush() {
    if (engine.profilesRev == _profilesRev) return;
    _profilesRev = engine.profilesRev;
    unawaited(load());
  }

  final StudioEngine engine;
  final GraphController graph;

  /// 图变了之后让外壳重新适配视图：卡片是异步拉回来的，
  /// 只在外壳 initState 里 fit 一次会停在空画布上。
  final VoidCallback? onLayout;

  FlowState state = FlowState.empty;
  String page = 'input';
  bool busy = false;
  String? lastError;

  Timer? _saveTimer;
  Timer? _moveTimer;
  bool _applying = false;

  /// 视图只在换页 / 换配置 / 整理之后适配一次。卡片一改就 fit 会把用户正在
  /// 编辑的那张卡挪走，看起来就像「视图被重置」。
  bool _needsFit = true;

  void _fit() {
    if (!_needsFit) return;
    _needsFit = false;
    onLayout?.call();
  }

  /// 换了一张图（页 / 配置 / 整理）才需要重新取景。
  void _invalidateFit() => _needsFit = true;

  static const rowPitch = flowRowHeight;

  FlowDef? defOf(GraphNode node) => state.def('${node.data['defKey']}');

  List<String> get profiles => state.order;
  String get profile => state.active;

  Future<void> load() async {
    busy = true;
    _notify();
    final result = await engine.invoke('flow.state', {'page': page});
    busy = false;
    if (result is Map) {
      state = FlowState.fromMap(Map<String, Object?>.from(result));
      _sync();
    }
    _notify();
  }

  Future<void> setPage(String next) async {
    if (page == next) return;
    page = next;
    _invalidateFit();
    _notify();
    await load();
  }

  Future<void> switchProfile(String name) async {
    _invalidateFit();
    await engine.invoke('flow.profile_switch', {'name': name, 'page': page});
    await load();
  }

  Future<void> newProfile([String name = '']) async {
    _invalidateFit();
    await engine.invoke('flow.profile_new', {'name': name, 'page': page});
    await load();
  }

  /// 改的是当前这份配置的名字：配置档列表、保存的文件名与画布都跟着引擎那份走。
  Future<void> renameProfile(String from, String to) async {
    _invalidateFit();
    final r = await engine.invoke('flow.profile_rename', {
      'old': from,
      'new': to,
      'page': page,
    });
    final why = '${(r is Map ? r['error'] : '') ?? ''}';
    if (why.isNotEmpty) engine.say(why);
    await load();
  }

  Future<void> removeProfile(String name) async {
    _invalidateFit();
    final r = await engine.invoke('flow.profile_remove', {
      'name': name,
      'page': page,
    });
    if (r is Map && '${(r)['error'] ?? ''}'.isNotEmpty) {
      engine.say('${r['error']}');
    }
    await load();
  }

  Future<void> addCard(String defKey, Offset world) async {
    await _mutate('flow.node_add', {
      'def': defKey,
      'x': world.dx,
      'y': world.dy,
    });
  }

  Future<void> removeCard(String id) async {
    await _mutate('flow.node_remove', {'id': id});
  }

  Future<void> setParam(String id, String fieldId, Object? value) async {
    await _mutate('flow.node_set', {
      'id': id,
      'params': {fieldId: value},
    });
  }

  Future<void> setAlias(String id, String alias) async {
    await _mutate('flow.node_set', {'id': id, 'alias': alias});
  }

  Future<void> setOverride(String id, int pinIndex, Object? value) async {
    await _mutate('flow.node_set', {
      'id': id,
      'overrides': {'$pinIndex': value},
    });
  }

  Future<void> connect(PortRef from, PortRef to) async {
    await _mutate('flow.wire_add', {
      'src': [from.nodeId, _outIndex(from)],
      'dst': [to.nodeId, _inIndex(to)],
    });
  }

  Future<void> disconnect(String wireId) async {
    await _mutate('flow.wire_remove', {'id': wireId});
  }

  /// 删掉画布上选中的卡片：本地先摘，再把差异发给引擎。
  Future<void> deleteSelected() async {
    if (graph.selection.isEmpty && graph.selectedEdge == null) {
      engine.say('先点选一张卡片');
      return;
    }
    graph.deleteSelection();
    await _flushLocal();
  }

  /// 复制一张卡片：副本由引擎登记，参数照抄选中的那张。
  void duplicateSelected() {
    final id = graph.selection.firstOrNull;
    if (id == null) {
      engine.say('先点选一张卡片');
      return;
    }
    final src = state.nodes.where((n) => n.id == id).firstOrNull;
    if (src == null) return;
    unawaited(
      _mutate('flow.node_add', {
        'def': src.defKey,
        'x': src.x + 34,
        'y': src.y + 34,
        'params': src.params,
      }),
    );
  }

  /// 剪贴板存的是**引擎那份**节点快照，不是画布对象：Ctrl+X 之后原卡就没了，
  /// 再贴的时候不能回头去找。下标即「剪贴板里的第几张」，连线按它引用。
  List<Map<String, Object?>> _clipNodes = const [];
  List<List<int>> _clipWires = const [];

  /// Ctrl+C：把选中的卡片与它们之间的连线记进剪贴板。
  bool copySelection() {
    final picked = graph.selection.toList();
    if (picked.isEmpty) {
      engine.say('先点选要复制的卡片');
      return false;
    }
    final index = {for (var i = 0; i < picked.length; i++) picked[i]: i};
    final rows = <Map<String, Object?>>[];
    for (final id in picked) {
      final src = state.nodes.where((n) => n.id == id).firstOrNull;
      if (src == null) continue;
      rows.add({
        'def': src.defKey,
        'x': src.x,
        'y': src.y,
        'params': src.params,
        'alias': src.alias,
        'overrides': {for (final e in src.overrides.entries) '${e.key}': e.value},
      });
    }
    if (rows.length != picked.length) {
      engine.say('选中的卡片里有引擎不认的，先刷新一次');
      return false;
    }
    _clipWires = [
      for (final w in state.wires)
        if (index.containsKey(w.srcNode) && index.containsKey(w.dstNode))
          [index[w.srcNode]!, w.srcIndex, index[w.dstNode]!, w.dstIndex],
    ];
    _clipNodes = rows;
    engine.say('已复制 ${rows.length} 张卡片');
    return true;
  }

  /// Ctrl+V：副本由引擎照抄（参数默认值、新 id、连线合法性都归它判）。
  Future<void> paste() async {
    if (_clipNodes.isEmpty) {
      engine.say('剪贴板是空的');
      return;
    }
    await _mutate('flow.paste', {
      'page': page,
      'nodes': _clipNodes,
      'wires': _clipWires,
    });
  }

  /// Ctrl+X：先记进剪贴板，再把选中的这些从画布上摘掉。
  Future<void> cutSelection() async {
    if (!copySelection()) return;
    graph.deleteSelection();
    await _flushLocal();
  }

  Future<void> clearPage() async {
    await _mutate('flow.clear', const {});
  }

  /// 整理布局：按数据流方向分五列（事件 → 源 → 运算 → 分支 → 汇）。
  ///
  /// 列宽取该列最宽的一张卡 + 28，行距取卡片自身高度 + 14，列内保持原有的上下
  /// 顺序。放在界面这边是因为只有画布知道自己把卡片撑到了多大。
  ///
  /// 输入页整页镜像（与旧版同一套）：那页卡片出脚在左、入脚在右，数据从右往左流，
  /// 所以列序反着铺——事件在最右、写入在最左，线才不会横穿卡片。
  static const _arrangeColumns = <String>['事件', '源', '运算', '分支', '汇'];
  static const _arrangeOps = {
    '事件': {'driver_period', 'driver_change'},
    '源': {'const', 'mod_read', 'core_read', 'temp_read'},
    '分支': {'branch', 'gate', 'sequence'},
    '汇': {'core_write', 'mod_write', 'temp_write'},
  };

  /// 每一列在这张画布上具体是什么卡，两页说法不同，报给用户的提示要按页讲。
  static const _arrangeTitles = {
    'input': '事件 · 模块变量传出 · 运算 · 分支 · 核心数据接收',
    'output': '事件 · 核心数值输出 · 运算 · 分支 · 模块变量接收',
  };

  Future<void> arrange() async {
    if (state.nodes.isEmpty) {
      engine.say('这张画布还是空的，先拖几张卡片进来');
      return;
    }
    final columnOf = <String, int>{};
    for (final entry in _arrangeOps.entries) {
      final index = _arrangeColumns.indexOf(entry.key);
      for (final op in entry.value) {
        columnOf[op] = index;
      }
    }
    final columns = List.generate(
      _arrangeColumns.length,
      (_) => <FlowNodeData>[],
    );
    for (final node in state.nodes) {
      final d = state.def(node.defKey);
      // 认不出的算子落在「运算」列，与旧界面同一兜底。
      columns[(d == null ? 2 : columnOf[d.op] ?? 2)].add(node);
    }
    // 卡片实际撑到多大只有画布知道（字段数量、别名行都算高度）。
    final measured = {for (final n in graph.nodes) n.id: n.size};
    final moves = <String, List<double>>{};
    var x = 40.0;
    for (final nodes in page == 'input' ? columns.reversed : columns) {
      final sorted = [...nodes]
        ..sort((a, b) => a.y != b.y ? a.y.compareTo(b.y) : a.x.compareTo(b.x));
      if (sorted.isEmpty) continue;
      var y = 40.0;
      var widest = 0.0;
      for (final node in sorted) {
        final size = measured[node.id];
        moves[node.id] = [x, y];
        y += (size?.height ?? 120) + 14.0;
        widest = math.max(widest, size?.width ?? 220);
      }
      x += widest + 28.0;
    }
    _invalidateFit();
    await _mutate('flow.nodes_move', {'moves': moves});
    engine.say('已按数据流方向整理布局：${_arrangeTitles[page]}');
  }

  /// 某个端口在引擎目录里的类型（exec / int / float / bool / str / any）。
  String pinType(PortRef ref, {required bool output}) {
    final node = state.nodes.where((n) => n.id == ref.nodeId).firstOrNull;
    final d = node == null ? null : state.def(node.defKey);
    if (d == null) return 'any';
    if (!output && ref.portId == 'exec') return 'exec';
    final list = output ? d.outputs : d.inputs;
    final i = int.tryParse(ref.portId.split(':').last) ?? -1;
    return i >= 0 && i < list.length ? list[i].type : 'any';
  }

  /// 引脚类型能不能互接：同类、any、或同为数值——与引擎 `compatible` 同规则。
  static bool pinCompatible(String src, String dst) =>
      src == dst ||
      src == 'any' ||
      dst == 'any' ||
      (src == 'float' || src == 'int') && (dst == 'float' || dst == 'int');

  /// 这张卡接不接得上刚拉出的那根线。`receive` = 新卡当接收方（从输出拉出）。
  bool accepts(FlowDef d, {required bool receive, required String type}) {
    if (receive) {
      if (type == 'exec') return d.execIn;
      return d.inputs.any((p) => pinCompatible(type, p.type));
    }
    if (type == 'exec') return d.entry;
    return d.outputs.any((p) => pinCompatible(p.type, type));
  }

  /// 拉线落到空白：建一张卡并把线接到它第一个兼容且空着的引脚。
  Future<void> spawnAndLink(
    FlowDef d,
    PortRef origin,
    PortDir dir,
    String type,
    Offset world,
  ) async {
    final before = {for (final n in state.nodes) n.id};
    await _mutate('flow.node_add', {
      'def': d.key,
      'x': world.dx,
      'y': world.dy,
    });
    final created = [
      for (final n in state.nodes)
        if (!before.contains(n.id)) n,
    ];
    if (created.isEmpty) return;
    final dst = created.last;
    final receive = dir == PortDir.output;
    final index = receive
        ? _freeInput(d, type, [
            for (final w in state.wires)
              if (w.dstNode == dst.id) w.dstIndex,
          ])
        : _freeOutput(d, type);
    if (index == null) {
      engine.say('${d.title}：没有能接上的引脚，卡片已放在画布上');
      return;
    }
    await _mutate(
      'flow.wire_add',
      receive
          ? {
              'src': [origin.nodeId, _outIndex(origin)],
              'dst': [dst.id, index],
            }
          : {
              'src': [dst.id, index],
              'dst': [origin.nodeId, _inIndex(origin)],
            },
    );
    engine.say(
      '已自动连接：${receive ? d.title : origin.nodeId} → ${receive ? dst.defKey : d.title}',
    );
  }

  /// 新卡上第一个兼容且没被占用的输入；exec 用 -1（与引擎同一约定）。
  int? _freeInput(FlowDef d, String type, Iterable<int> taken) {
    if (type == 'exec') return d.execIn ? -1 : null;
    for (var i = 0; i < d.inputs.length; i++) {
      if (taken.contains(i)) continue;
      if (pinCompatible(type, d.inputs[i].type)) return i;
    }
    return null;
  }

  int? _freeOutput(FlowDef d, String type) {
    if (type == 'exec') return d.entry ? 0 : null;
    for (var i = 0; i < d.outputs.length; i++) {
      if (pinCompatible(d.outputs[i].type, type)) return i;
    }
    return null;
  }

  int _outIndex(PortRef ref) => int.tryParse(ref.portId.split(':').last) ?? 0;

  int _inIndex(PortRef ref) => ref.portId == 'exec'
      ? -1
      : (int.tryParse(ref.portId.split(':').last) ?? 0);

  Future<void> _mutate(String method, Map<String, Object?> params) async {
    final result = await engine.invoke(method, {'page': page, ...params});
    if (result is! Map) return;
    final payload = Map<String, Object?>.from(result);
    final error = '${payload['error'] ?? ''}';
    lastError = error.isEmpty ? null : error;
    if (error.isNotEmpty) engine.say(error);
    if (payload['graph'] != null) {
      state = FlowState(
        active: state.active,
        order: state.order,
        page: page,
        nodes: [
          for (final n
              in (Map<String, Object?>.from(payload['graph'] as Map)['nodes']
                      as List? ??
                  const []))
            FlowNodeData.fromMap(Map<String, Object?>.from(n as Map)),
        ],
        wires: [
          for (final w
              in (Map<String, Object?>.from(payload['graph'] as Map)['wires']
                      as List? ??
                  const []))
            FlowWireData.fromMap(Map<String, Object?>.from(w as Map)),
        ],
        catalog: state.catalog,
        errors: {
          for (final e in (payload['errors'] as Map? ?? const {}).entries)
            '${e.key}': '${e.value}',
        },
        idle: {for (final s in (payload['idle'] as List? ?? const [])) '$s'},
        catalogRev: (payload['catalog_rev'] as num?)?.toInt() ??
            state.catalogRev,
        missingModules: state.missingModules,
        vars: state.vars,
      );
      _sync();
      if (state.nodes.any((n) => state.def(n.defKey) == null)) {
        // 变量的读数 / 写入卡是登记变量那一刻才进目录的：本地这份可能还没有，
        // 卡片会退化成一条只有 key 的空壳。补取一次目录再同步。
        _scheduleSave();
        await load();
        return;
      }
    }
    _scheduleSave();
    _notify();
  }

  Future<void> saveNow() async {
    _saveTimer?.cancel();
    await engine.invoke('flow.save', const {});
    engine.say('事件流已保存');
  }

  void _scheduleSave() {
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 700), () {
      unawaited(engine.invoke('flow.save', const {}));
    });
  }

  // ------------------------------------------------------------ 实时值回显
  //
  // 引擎每拍算一次 live，界面不跟着跑就看不到「这条写入卡现在传出去的是多少」。
  // 只有事件流页在眼前时才拉，离开这页立刻停。
  Timer? _liveTimer;

  void startLive() {
    if (_liveTimer != null) return;
    _pullLive();
    _liveTimer = Timer.periodic(
      const Duration(milliseconds: 600),
      (_) => _pullLive(),
    );
  }

  void stopLive() {
    _liveTimer?.cancel();
    _liveTimer = null;
  }

  Future<void> _pullLive() async {
    final result = await engine.invoke('flow.live', {'page': page});
    if (result is! Map) return;
    final payload = Map<String, Object?>.from(result);
    // 目录换了版本就整份重取：设备连上以后模块才登记出参数，画布上那几张读数卡
    // 以前一直是只有 key 的空壳（变量表却已经出现了行，看着像状态没刷新）。
    // 版本只增不减，拿大于比较就能忽略迟到的旧响应；画布空着也要查，
    // 卡片面板同样按目录出。
    final rev = (payload['catalog_rev'] as num?)?.toInt() ?? 0;
    if (rev > state.catalogRev) {
      if (busy) return;
      await load();
      return;
    }
    if (graph.nodes.isEmpty) return;
    final values = Map<String, Object?>.from(
      payload['values'] as Map? ?? const {},
    );
    var changed = false;
    for (final node in graph.nodes) {
      final next = values[node.id];
      final current = node.data['live'];
      final same =
          next is List &&
          current is List &&
          next.length == current.length &&
          List.generate(
            next.length,
            (i) => '${next[i]}',
          ).every((v) => current.contains(v));
      if (same) continue;
      node.data['live'] = next;
      changed = true;
    }
    // 红字与灰显跟着这一拍一起换：判断是引擎每算一拍才定的，只按进页时那份
    // flow.state 画注释的话，用户把线接上以后卡片还挂着「这张没执行流」。
    final errors = <String, String>{
      for (final e in (payload['errors'] as Map? ?? const {}).entries)
        '${e.key}': '${e.value}',
    };
    final idle = <String>{
      for (final s in (payload['idle'] as List? ?? const [])) '$s',
    };
    if (!_sameText(errors, state.errors) ||
        !_sameIds(idle, state.idle)) {
      state = state.copyWith(errors: errors, idle: idle);
      changed = true;
    }
    if (changed) graph.poke();
  }

  static bool _sameText(Map<String, String> a, Map<String, String> b) =>
      a.length == b.length && a.entries.every((e) => b[e.key] == e.value);

  static bool _sameIds(Set<String> a, Set<String> b) =>
      a.length == b.length && a.every(b.contains);

  // -------------------------------------------------------------- 变量表

  /// 左栏：模块登记、不可改名的参数；右栏：可改名的登记行 + 临时变量。
  List<Map<String, Object?>> get systemVars =>
      state.vars['declared'] ?? const [];
  List<Map<String, Object?>> get freeVars =>
      state.vars['renamable'] ?? const [];

  /// 变量表的实时值只有引擎知道，面板打开时按秒取一次轻量的两栏清单。
  Future<void> refreshVars() async {
    _applyVars(await engine.invoke('flow.vars', {'page': page}));
  }

  Future<void> addVar(String name, {String note = ''}) async {
    _applyVars(
      await engine.invoke('flow.var_add', {
        'page': page,
        'name': name,
        'note': note,
        'dir': 'inout',
      }),
    );
  }

  Future<void> removeVar(String name) async {
    _applyVars(
      await engine.invoke('flow.var_remove', {'page': page, 'name': name}),
    );
  }

  Future<void> renameVar(String name, String next) async {
    _applyVars(
      await engine.invoke('flow.var_rename', {
        'page': page,
        'name': name,
        'new': next,
      }),
    );
  }

  /// 方向循环和旧界面一致：in → out → inout。
  Future<void> cycleVarDir(Map<String, Object?> row) async {
    final next = switch ('${row['dir'] ?? 'in'}') {
      'in' => 'out',
      'out' => 'inout',
      _ => 'in',
    };
    _applyVars(
      await engine.invoke('flow.var_dir', {
        'page': page,
        'name': row['name'],
        'dir': next,
      }),
    );
  }

  /// 变量类方法要么回两栏清单、要么只回一个 error；只回错误时不能把表清空。
  void _applyVars(Object? result) {
    if (result is! Map) return;
    final payload = Map<String, Object?>.from(result);
    final error = '${payload['error'] ?? ''}';
    if (error.isNotEmpty) engine.say(error);
    if (payload['declared'] == null && payload['renamable'] == null) return;
    state = state.copyWith(
      vars: {
        'declared': [
          for (final v in (payload['declared'] as List? ?? const []))
            Map<String, Object?>.from(v as Map),
        ],
        'renamable': [
          for (final v in (payload['renamable'] as List? ?? const []))
            Map<String, Object?>.from(v as Map),
        ],
      },
    );
    _notify();
  }

  /// 画布上的本地改动（拖动、删除、连线）在停下后一次性回传给引擎。
  void _onGraphChanged() {
    if (_applying) return;
    _moveTimer?.cancel();
    _moveTimer = Timer(const Duration(milliseconds: 260), _flushLocal);
  }

  Future<void> _flushLocal() async {
    var changed = false;
    final liveNodes = graph.nodes.map((n) => n.id).toSet();
    final liveEdges = {for (final e in graph.edges) e.id};

    for (final data in state.nodes) {
      if (liveNodes.contains(data.id)) continue;
      await engine.invoke('flow.node_remove', {'page': page, 'id': data.id});
      changed = true;
    }
    for (final w in state.wires) {
      if (liveEdges.contains(w.id)) continue;
      await engine.invoke('flow.wire_remove', {'page': page, 'id': w.id});
      changed = true;
    }
    for (final e in graph.edges) {
      if (state.wires.any((w) => w.id == e.id)) continue;
      await connect(e.from, e.to);
      changed = true;
    }
    for (final node in graph.nodes) {
      final src = state.nodes.where((n) => n.id == node.id).firstOrNull;
      if (src == null) continue;
      if ((src.x - node.position.dx).abs() <= .5 &&
          (src.y - node.position.dy).abs() <= .5) {
        continue;
      }
      await engine.invoke('flow.node_move', {
        'page': page,
        'id': node.id,
        'x': node.position.dx,
        'y': node.position.dy,
      });
      changed = true;
    }
    if (changed) {
      await load();
      _scheduleSave();
    }
  }

  /// 把引擎回传的图原地同步进画布：已存在的卡片只改字段，
  /// 不重建 GraphNode——重建会打断正在输入的文本框并丢掉焦点。
  void _sync() {
    _applying = true;
    // 输入页整页镜像（与旧版一致）：出脚在左、入脚在右，数据右 → 左流。
    graph.setMirror(page == 'input');
    final keep = <String>{};
    for (final data in state.nodes) {
      keep.add(data.id);
      final def = state.def(data.defKey);
      final size = Size(flowWidthOf(def, data), flowHeightOf(def));
      final ports = portsFor(def);
      final existing = graph.node(data.id);
      if (existing == null) {
        graph.addNode(
          id: data.id,
          kind: NodeKind.flow,
          at: Offset(data.x, data.y),
          title: data.alias.isNotEmpty
              ? data.alias
              : (def?.title ?? data.defKey),
          size: size,
          ports: ports,
          data: {
            'defKey': data.defKey,
            'params': data.params,
            'overrides': data.overrides,
          },
        );
        continue;
      }
      existing.data['defKey'] = data.defKey;
      // 参数字段读的是画布节点上的这一份；不同步的话改完值一刷新就回到默认。
      existing.data['params'] = data.params;
      existing.data['overrides'] = data.overrides;
      if (existing.title !=
          (data.alias.isNotEmpty ? data.alias : (def?.title ?? data.defKey))) {
        existing.data['title'] = data.alias.isNotEmpty
            ? data.alias
            : (def?.title ?? data.defKey);
      }
      existing.position = Offset(data.x, data.y);
      if (existing.size != size) existing.size = size;
      if (ports.length != existing.ports.length ||
          existing.ports.any((p) => ports.every((q) => q.id != p.id))) {
        existing.ports
          ..clear()
          ..addAll(ports);
      }
      existing.touch();
    }
    for (final id in graph.nodes.map((n) => n.id).toList()) {
      if (!keep.contains(id)) graph.dropNode(id);
    }
    final wanted = <GraphEdge>[
      for (final w in state.wires)
        GraphEdge(
          id: w.id,
          from: PortRef(w.srcNode, outputPortId(w.srcIndex)),
          to: PortRef(w.dstNode, inputPortId(w.dstIndex)),
        ),
    ];
    graph.edges
      ..removeWhere((e) => !wanted.any((w) => w.id == e.id))
      ..clear()
      ..addAll(wanted);
    graph.poke();
    _applying = false;
    _fit();
  }

  @override
  void dispose() {
    _saveTimer?.cancel();
    _moveTimer?.cancel();
    stopLive();
    graph.removeListener(_onGraphChanged);
    graph.onDuplicate = null;
    engine.removeListener(_onEnginePush);
    super.dispose();
  }

  void _notify() {
    if (hasListeners) notifyListeners();
  }
}

/// 一张卡片的端口：执行入口占第一行，数据引脚依次往下。
List<PortSpec> portsFor(FlowDef? def) {
  if (def == null) return const [];
  final out = <PortSpec>[];
  var rows = 0;
  if (def.execIn) {
    out.add(
      PortSpec(
        id: 'exec',
        kind: PortKind.exec,
        dir: PortDir.input,
        label: '执行',
        dy: portDy(rows),
      ),
    );
    rows++;
  }
  for (var i = 0; i < def.inputs.length; i++, rows++) {
    final pin = def.inputs[i];
    out.add(
      PortSpec(
        id: inputPortId(i),
        kind: portKindOf(pin.type),
        dir: PortDir.input,
        label: pin.name,
        dy: portDy(rows),
      ),
    );
  }
  for (var i = 0; i < def.outputs.length; i++) {
    final pin = def.outputs[i];
    out.add(
      PortSpec(
        id: outputPortId(i),
        kind: portKindOf(pin.type),
        dir: PortDir.output,
        label: pin.name,
        dy: portDy(i),
      ),
    );
  }
  return out;
}

double portDy(int row) =>
    flowHeaderHeight + 10 + row * flowRowHeight + flowRowHeight / 2;
