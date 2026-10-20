import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/painting.dart';

import '../app/tokens.dart';
import 'node_metrics.dart';

enum PortDir { input, output }

enum NodeKind {
  model,
  prompt,
  generator,
  preview,

  /// Any Blueprint-style node described by a [NodeBlueprint] in the registry.
  blueprint,

  /// A bare circular relay that a wire bends through.
  reroute,

  /// A translucent grouping box that always paints under the nodes.
  comment,

  /// 事件流卡片：形状与端口都由引擎回传的目录决定，不是写死的一种。
  flow,
}

@immutable
class PortSpec {
  const PortSpec({
    required this.id,
    required this.kind,
    required this.dir,
    required this.label,
    required this.dy,
    this.dx = 0,
    this.valueKey,
  });

  final String id;
  final PortKind kind;
  final PortDir dir;

  /// Data key backing this pin's inline default editor, when it has one.
  final String? valueKey;

  /// Label drawn next to the dot inside the card.
  final String label;

  /// Port centre measured from the node's top-left, in world units.
  final double dy;
  final double dx;

  bool get isExec => kind == PortKind.exec;

  /// Direction must oppose, and the types must match or be one of Blueprint's
  /// implicit conversions (bool -> int -> float, or a wildcard).
  bool accepts(PortSpec other) =>
      dir != other.dir && portTypesCompatible(kind, other.kind);
}

@immutable
class PortRef {
  const PortRef(this.nodeId, this.portId);

  final String nodeId;
  final String portId;

  @override
  bool operator ==(Object other) =>
      other is PortRef && other.nodeId == nodeId && other.portId == portId;

  @override
  int get hashCode => Object.hash(nodeId, portId);

  @override
  String toString() => '$nodeId:$portId';
}

class GraphNode extends ChangeNotifier {
  GraphNode({
    required this.id,
    required this.kind,
    required this.title,
    required this.position,
    required this.size,
    required List<PortSpec> ports,
    Map<String, Object?>? data,
  }) : data = data ?? <String, Object?>{},
       // 自己留一份可写的端口表：目录里还没有这张卡时传进来的是 const []，
       // 引擎回传后要在原地换端口。
       ports = List<PortSpec>.of(ports),
       baseSize = size {
    _resyncWidth();
  }

  final String id;
  final NodeKind kind;
  final String title;
  final List<PortSpec> ports;
  final Map<String, Object?> data;

  Offset position;

  /// The size the kind was designed at. The live [size] may be wider, never
  /// narrower, so a long checkpoint name can never shrink a card below the
  /// reference layout.
  final Size baseSize;
  Size size;

  /// 整页镜像：引脚换边（见 [portWorld]）。由 [GraphController.mirror] 同步下来，
  /// 卡片自己画标签时读同一个值，两边才不会各说各话。
  bool mirror = false;

  PortSpec? port(String portId) =>
      ports.where((p) => p.id == portId).firstOrNull;

  Offset portWorld(PortSpec p) {
    // 正常页：出脚在右、入脚在左。镜像页（事件流输入页）整页反过来——
    // 与旧版同一套：读到的值从右边出、写进去的值从左边收，数据右 → 左流。
    final right = (p.dir == PortDir.output) != mirror;
    return Offset(
      right ? position.dx + size.width + p.dx : position.dx + p.dx,
      position.dy + p.dy,
    );
  }

  Rect get worldRect => position & size;

  void move(Offset delta) {
    if (delta == Offset.zero) return;
    position += delta;
    notifyListeners();
  }

  void set(String key, Object? value) {
    if (data[key] == value) return;
    data[key] = value;
    _resyncWidth();
    notifyListeners();
  }

  /// Widens the card so no field inside it has to truncate. Called before any
  /// notify, so the sockets, the wires and the painted card agree on the same
  /// width in the same frame.
  void _resyncWidth() {
    final needed = nodeWidthFor(kind, data);
    final next = Size(math.max(baseSize.width, needed), baseSize.height);
    if (next != size) size = next;
  }

  T read<T>(String key, T fallback) =>
      data[key] is T ? data[key] as T : fallback;

  /// Public repaint trigger for external mutations (e.g. a TextEditingController
  /// edited from the dock).
  void touch() {
    _resyncWidth();
    notifyListeners();
  }
}

@immutable
class GraphEdge {
  const GraphEdge({required this.id, required this.from, required this.to});

  final String id;
  final PortRef from;
  final PortRef to;

  GraphEdge withTo(PortRef target) => GraphEdge(id: id, from: from, to: target);
  GraphEdge withFrom(PortRef source) => GraphEdge(id: id, from: source, to: to);
}

/// Geometry of a cubic connector, shared by the painter and the hit-tester.
@immutable
class EdgeGeometry {
  const EdgeGeometry(this.start, this.end, {this.mirror = false});

  final Offset start;
  final Offset end;

  /// 镜像页的线从右往左走，控制点要朝反方向甩，否则线会在引脚上打了个结再绕回来。
  final bool mirror;

  double get _bow =>
      (mirror ? -1.0 : 1.0) *
      math.max(38.0, math.min(150.0, (end.dx - start.dx).abs() * 0.55));

  Offset get c1 => start + Offset(_bow, 0);
  Offset get c2 => end - Offset(_bow, 0);

  RRect get bounds {
    final reach = _bow.abs();
    final l = math.min(start.dx, end.dx) - reach;
    final r = math.max(start.dx, end.dx) + reach;
    final t = math.min(start.dy, end.dy) - 12;
    final b = math.max(start.dy, end.dy) + 12;
    return RRect.fromLTRBAndCorners(l, t, r, b);
  }

  /// Samples the curve for proximity tests and midpoint badges.
  List<Offset> sample([int steps = 44]) {
    final pts = <Offset>[];
    for (var i = 0; i <= steps; i++) {
      final t = i / steps;
      final u = 1 - t;
      pts.add(
        start * u * u * u +
            c1 * (3 * u * u * t) +
            c2 * (3 * u * t * t) +
            end * (t * t * t),
      );
    }
    return pts;
  }

  Offset get midpoint => sample(2)[2];
}

@immutable
class PendingLink {
  const PendingLink({
    required this.origin,
    required this.cursor,
    required this.dir,
    this.hover,
    this.reuseEdge,
  });

  final PortRef origin;
  final Offset cursor;
  final PortDir dir;
  final PortRef? hover;
  final String? reuseEdge;

  PendingLink copyWith({
    Offset? cursor,
    PortRef? hover,
    bool clearHover = false,
  }) => PendingLink(
    origin: origin,
    cursor: cursor ?? this.cursor,
    dir: dir,
    hover: clearHover ? null : (hover ?? this.hover),
    reuseEdge: reuseEdge,
  );
}

class LinkResult {
  const LinkResult(this.ok, this.message);
  final bool ok;
  final String message;
}

/// Owns the node graph, the viewport transform and every pointer interaction
/// that mutates them. Views never mutate [GraphNode] fields directly.
class GraphController extends ChangeNotifier {
  GraphController({
    required List<GraphNode> nodes,
    required List<GraphEdge> edges,
    String? title,
    this.autoConvert = false,
  }) : title = title ?? 'Image generation x3',
       // Callers often hand in `const` literals; the graph must stay mutable.
       nodes = List<GraphNode>.of(nodes),
       edges = List<GraphEdge>.of(edges) {
    _index();
  }

  final List<GraphNode> nodes;
  final List<GraphEdge> edges;
  final String title;

  /// 拉线落在空白处时回调：(起点端口, 起点方向, 世界坐标)。
  /// 事件流页用它打开「按类型过滤的建卡搜索」，选中后自动接线；引脚类型由
  /// 引擎目录那侧解释，画布模型只管端口形状。
  void Function(PortRef origin, PortDir dir, Offset world)? onLinkMiss;

  /// 整页镜像（事件流输入页）：引脚换边、连线反向甩。见 [setMirror]。
  bool mirror = false;

  /// 异类型的数据脚允许落线（引擎自动补转换卡）：事件流画布开，图像工作流不开。
  bool autoConvert = false;

  /// 换页时统一改一遍：节点上的引脚算式、卡片里的标签位置、连线的控制点
  /// 都读同一个值，漏掉一个就会出现「点在左边、线从右边出来」。
  void setMirror(bool on) {
    if (mirror == on) return;
    mirror = on;
    for (final n in nodes) {
      if (n.mirror == on) continue;
      n.mirror = on;
      n.touch();
    }
    notifyListeners();
  }

  /// Viewport transform: `view = world * scale + pan`.
  Offset pan = const Offset(46, 30);
  double scale = 1.0;

  /// The canvas's own size, published by [GraphCanvas] each build so chrome
  /// outside the canvas (the drawer, the rail buttons, the F key) can fit or
  /// spawn at a point that is actually on screen.
  Size viewport = Size.zero;

  final Set<String> selection = <String>{};
  String? selectedEdge;
  PendingLink? pending;
  Rect? marquee;
  PortRef? hoveredPort;
  String? hoveredEdge;

  int _edgeSeq = 0;
  int _nodeSeq = 0;
  final Map<String, GraphNode> _byId = {};

  void _index() {
    _byId.clear();
    for (final n in nodes) {
      _byId[n.id] = n;
      n.addListener(notifyListeners);
    }
    _nodeSeq = nodes.length;
    _edgeSeq = edges.length;
  }

  GraphNode? node(String id) => _byId[id];

  /// Public escape hatch for view code that mutates a plain field and needs a
  /// repaint (protected `notifyListeners` cannot be called from outside).
  void poke() => notifyListeners();

  String? status;
  DateTime? statusAt;

  /// 事件流卡片的副本必须由引擎登记，画布自己 clone 出来的 id 引擎不认识。
  /// 挂上这个钩子后，右键「复制并另起一张」走编辑器；图像工作流仍用本地 clone。
  void Function()? onDuplicate;

  void say(String message) {
    status = message;
    statusAt = DateTime.now();
    notifyListeners();
  }

  @override
  void dispose() {
    for (final n in nodes) {
      n.removeListener(notifyListeners);
    }
    super.dispose();
  }

  // ---------------------------------------------------------------- geometry

  Offset toWorld(Offset view) => (view - pan) / scale;
  Offset toView(Offset world) => world * scale + pan;

  PortSpec? specOf(PortRef ref) => _byId[ref.nodeId]?.port(ref.portId);

  Offset? portWorld(PortRef ref) {
    final n = _byId[ref.nodeId];
    final p = n?.port(ref.portId);
    if (n == null || p == null) return null;
    return n.portWorld(p);
  }

  Offset? portView(PortRef ref) {
    final w = portWorld(ref);
    return w == null ? null : toView(w);
  }

  EdgeGeometry? edgeGeometry(GraphEdge e) {
    final a = portWorld(e.from);
    final b = portWorld(e.to);
    if (a == null || b == null) return null;
    return EdgeGeometry(a, b, mirror: mirror);
  }

  /// World-space rect of the whole graph, used by [fitView].
  Rect? contentBounds({bool Function(GraphNode n)? where}) {
    final picked = where == null ? nodes : nodes.where(where).toList();
    if (picked.isEmpty) return null;
    var l = double.infinity,
        t = double.infinity,
        r = double.negativeInfinity,
        b = double.negativeInfinity;
    for (final n in picked) {
      l = math.min(l, n.position.dx);
      t = math.min(t, n.position.dy);
      r = math.max(r, n.position.dx + n.size.width);
      b = math.max(b, n.position.dy + n.size.height);
    }
    return Rect.fromLTRB(l, t, r, b);
  }

  /// The four cards the image workflow is built from, as opposed to the
  /// Blueprint band and the annotations parked further out along the canvas.
  static const imageNodeKinds = {
    NodeKind.model,
    NodeKind.prompt,
    NodeKind.generator,
    NodeKind.preview,
  };

  /// [leftInset] keeps the framing clear of chrome that floats over the
  /// canvas — the drawer covers that strip, so centring on the full width
  /// would park the workflow underneath it.
  void fitView(
    Size viewport, {
    double padding = 90,
    bool Function(GraphNode n)? where,
    double leftInset = 0,
  }) {
    final bounds = contentBounds(where: where);
    if (bounds == null || bounds.isEmpty) return;
    final usable = math.max(160.0, viewport.width - leftInset);
    final w = bounds.width + padding * 2;
    final h = bounds.height + padding * 2;
    scale = (1.0 / math.max(0.001, math.max(w / usable, h / viewport.height)))
        .clamp(0.35, 1.6);
    final cx = leftInset + usable / 2 - (bounds.center.dx * scale);
    final cy = viewport.height / 2 - (bounds.center.dy * scale);
    pan = Offset(cx, cy);
    notifyListeners();
  }

  /// Frames the image workflow only. The Blueprint band sits far out to the
  /// right, and fitting the whole graph at boot shrinks everything to the 0.35
  /// floor just to make room for it.
  void fitWorkflow(Size viewport, {double leftInset = 0}) => fitView(
    viewport,
    padding: 26,
    leftInset: leftInset,
    where: (n) => imageNodeKinds.contains(n.kind),
  );

  void resetView() {
    scale = 1.0;
    pan = const Offset(46, 30);
    notifyListeners();
  }

  void zoomAt(Offset focusView, double factor) {
    final next = (scale * factor).clamp(0.35, 2.6);
    if (next == scale) return;
    final world = toWorld(focusView);
    scale = next;
    pan = focusView - world * scale;
    notifyListeners();
  }

  void zoomStep(double factor) {
    scale = (scale * factor).clamp(0.35, 2.6);
    notifyListeners();
  }

  // ------------------------------------------------------------- interaction

  GraphNode? nodeAtWorld(Offset world) {
    for (final n in nodes.reversed) {
      if (n.worldRect.contains(world)) return n;
    }
    return null;
  }

  /// 这根线能不能落到这个脚上。
  ///
  /// 类型接得上当然落；开了自动转换的画布（事件流）上，方向相反的**数据**脚也算
  /// 落点——异类型不再在画布上被拒，引擎会在中间补一张「转整数 / 转布尔 / 转文本」。
  /// 执行流仍然要拒：那不是差一张转换卡的事。
  bool dropOk(PortSpec from, PortSpec to) =>
      from.accepts(to) ||
      (autoConvert && from.dir != to.dir && !from.isExec && !to.isExec);

  /// Nearest compatible port to a viewport-space point, or null.
  PortRef? portAt(Offset view, {double radius = 17, PortSpec? match}) {
    PortRef? best;
    var bestDist = radius * radius;
    for (final n in nodes) {
      for (final p in n.ports) {
        if (match != null && !dropOk(match, p)) continue;
        final pv = toView(n.portWorld(p));
        final d = (pv - view).distanceSquared;
        if (d < bestDist) {
          bestDist = d;
          best = PortRef(n.id, p.id);
        }
      }
    }
    return best;
  }

  /// Closest edge to a viewport-space point (curve sampled for accuracy).
  String? edgeAt(Offset view, {double radius = 9}) {
    String? best;
    var bestDist = radius * radius;
    for (final e in edges) {
      final g = edgeGeometry(e);
      if (g == null) continue;
      final vg = EdgeGeometry(toView(g.start), toView(g.end), mirror: g.mirror);
      for (final p in vg.sample(56)) {
        final d = (p - view).distanceSquared;
        if (d < bestDist) {
          bestDist = d;
          best = e.id;
        }
      }
    }
    return best;
  }

  void select(String? id, {bool additive = false}) {
    if (id == null) {
      if (!additive) {
        selection.clear();
        selectedEdge = null;
      }
      notifyListeners();
      return;
    }
    if (additive) {
      selection.contains(id) ? selection.remove(id) : selection.add(id);
    } else {
      selection
        ..clear()
        ..add(id);
    }
    selectedEdge = null;
    notifyListeners();
  }

  void selectEdge(String? id) {
    selectedEdge = id;
    if (id != null) selection.clear();
    notifyListeners();
  }

  void clear() {
    selection.clear();
    selectedEdge = null;
    marquee = null;
    notifyListeners();
  }

  /// Moves a node to the top of the paint/hit order.
  void bringToFront(String id) {
    final i = nodes.indexWhere((n) => n.id == id);
    if (i < 0 || i == nodes.length - 1) return;
    final n = nodes.removeAt(i);
    nodes.add(n);
    notifyListeners();
  }

  void beginPan(Offset viewDelta) {
    pan += viewDelta;
    notifyListeners();
  }

  void translateSelection(Offset worldDelta) {
    if (worldDelta == Offset.zero) return;
    for (final id in selection) {
      _byId[id]?.move(worldDelta);
    }
  }

  void beginLink(PortRef ref, Offset worldCursor) {
    final spec = specOf(ref);
    if (spec == null) return;
    // Pulling from a wired input detaches and re-routes the existing edge.
    String? reuse;
    if (spec.dir == PortDir.input) {
      final existing = edges.where((e) => e.to == ref).firstOrNull;
      if (existing != null) {
        reuse = existing.id;
        _detached[existing.id] = existing;
        edges.remove(existing);
      }
    }
    pending = PendingLink(
      origin: ref,
      cursor: worldCursor,
      dir: spec.dir,
      reuseEdge: reuse,
    );
    selection.clear();
    selectedEdge = null;
    notifyListeners();
  }

  void updateLink(Offset viewCursor) {
    final p = pending;
    if (p == null) return;
    final originSpec = specOf(p.origin);
    final target = portAt(viewCursor, match: originSpec);
    pending = p.copyWith(
      cursor: toWorld(viewCursor),
      hover: target,
      clearHover: target == null,
    );
    notifyListeners();
  }

  LinkResult? commitLink(Offset viewCursor) {
    final p = pending;
    if (p == null) return null;
    final originSpec = specOf(p.origin);
    final target = portAt(viewCursor, match: originSpec);
    pending = null;
    if (target == null) {
      if (p.reuseEdge != null) _restore(p.reuseEdge!);
      notifyListeners();
      // 落在空白处不是「取消」：旧界面在这里打开建卡搜索，选中即自动接线。
      onLinkMiss?.call(p.origin, p.dir, toWorld(viewCursor));
      return const LinkResult(false, '连线取消');
    }
    final result = connect(p.origin, target);
    if (!result.ok && p.reuseEdge != null) _restore(p.reuseEdge!);
    _detached.remove(p.reuseEdge);
    say(result.message);
    notifyListeners();
    return result;
  }

  void cancelLink() {
    final p = pending;
    if (p?.reuseEdge != null) _restore(p!.reuseEdge!);
    pending = null;
    notifyListeners();
  }

  void _restore(String edgeId) {
    final e = _detached.remove(edgeId);
    if (e != null && !edges.any((x) => x.id == e.id)) edges.add(e);
  }

  final Map<String, GraphEdge> _detached = {};

  LinkResult connect(PortRef a, PortRef b) {
    final sa = specOf(a);
    final sb = specOf(b);
    if (sa == null || sb == null) return const LinkResult(false, '端口不存在');
    PortRef source = a, target = b;
    if (sa.dir == PortDir.input && sb.dir == PortDir.output) {
      source = b;
      target = a;
    }
    if (source.nodeId == target.nodeId) {
      return const LinkResult(false, '不能连接到自身');
    }
    final ts = specOf(target)!;
    final ss = specOf(source)!;
    if (!dropOk(ss, ts)) {
      return LinkResult(
        false,
        '类型不匹配：${portTypeLabel(ss.kind)} → ${portTypeLabel(ts.kind)}',
      );
    }
    // An input holds exactly one edge; a new link replaces the old one.
    edges.removeWhere((e) => e.to == target);
    edges.add(GraphEdge(id: 'e${++_edgeSeq}', from: source, to: target));
    return const LinkResult(true, '已连接');
  }

  void removeEdge(String id) {
    edges.removeWhere((e) => e.id == id);
    if (selectedEdge == id) selectedEdge = null;
    notifyListeners();
  }

  /// 一次删掉给定的卡片与连线：右键框选要「圈到哪条断哪条」，
  /// 而 [deleteSelection] 只会按当前选区删，混合不了「卡片 + 独立的线」。
  void deleteNodesAndEdges(Set<String> nodeIds, Set<String> edgeIds) {
    if (nodeIds.isEmpty && edgeIds.isEmpty) return;
    edges.removeWhere(
      (e) =>
          edgeIds.contains(e.id) ||
          nodeIds.contains(e.from.nodeId) ||
          nodeIds.contains(e.to.nodeId),
    );
    for (final id in nodeIds) {
      nodes.removeWhere((n) {
        if (n.id != id) return false;
        n.removeListener(notifyListeners);
        return true;
      });
    }
    _byId.removeWhere((k, _) => nodeIds.contains(k));
    selection.removeWhere(nodeIds.contains);
    if (selectedEdge != null && !edges.any((e) => e.id == selectedEdge)) {
      selectedEdge = null;
    }
    notifyListeners();
  }

  void deleteSelection() {
    if (selectedEdge != null) {
      removeEdge(selectedEdge!);
      return;
    }
    if (selection.isEmpty) return;
    final doomed = {...selection};
    edges.removeWhere(
      (e) => doomed.contains(e.from.nodeId) || doomed.contains(e.to.nodeId),
    );
    nodes.removeWhere((n) {
      if (!doomed.contains(n.id)) return false;
      n.removeListener(notifyListeners);
      return true;
    });
    _byId.removeWhere((k, _) => doomed.contains(k));
    selection.clear();
    notifyListeners();
  }

  /// 摘掉一张卡片及其连线（引擎回传的图里已经没有它了）。
  void dropNode(String id) {
    final doomed = {id};
    edges.removeWhere(
      (e) => doomed.contains(e.from.nodeId) || doomed.contains(e.to.nodeId),
    );
    nodes.removeWhere((n) {
      if (n.id != id) return false;
      n.removeListener(notifyListeners);
      return true;
    });
    _byId.removeWhere((k, _) => doomed.contains(k));
    selection.removeAll(doomed);
  }

  void duplicateSelection() {
    final copies = <GraphNode>[];
    for (final id in selection) {
      final src = _byId[id];
      if (src == null) continue;
      copies.add(
        addNode(
          kind: src.kind,
          at: src.position + const Offset(34, 34),
          title: src.title,
          size: src.size,
          ports: src.ports,
          data: Map<Object?, Object?>.from(src.data).cast<String, Object?>(),
        ),
      );
    }
    selection
      ..clear()
      ..addAll(copies.map((n) => n.id));
    notifyListeners();
  }

  GraphNode addNode({
    required NodeKind kind,
    required Offset at,
    required String title,
    required Size size,
    required List<PortSpec> ports,
    Map<String, Object?>? data,
    String? id,
  }) {
    final n = GraphNode(
      id: id ?? 'n${++_nodeSeq}',
      kind: kind,
      title: title,
      position: at,
      size: size,
      ports: ports,
      data: data,
    )..mirror = mirror;
    nodes.add(n);
    _byId[n.id] = n;
    n.addListener(notifyListeners);
    notifyListeners();
    return n;
  }

  /// 用一整张外部画布替换当前内容。事件流里引擎才是权威，
  /// 每次结构变更后都拿回完整的节点与连线，界面不自己拼一份状态。
  void replaceAll({
    required List<GraphNode> newNodes,
    required List<GraphEdge> newEdges,
  }) {
    for (final n in nodes) {
      n.removeListener(notifyListeners);
    }
    nodes
      ..clear()
      ..addAll(newNodes);
    edges
      ..clear()
      ..addAll(newEdges);
    selection.retainAll(newNodes.map((n) => n.id).toSet());
    if (selectedEdge != null && !newEdges.any((e) => e.id == selectedEdge)) {
      selectedEdge = null;
    }
    _index();
    notifyListeners();
  }

  List<GraphEdge> edgesFrom(PortRef ref) =>
      edges.where((e) => e.from == ref).toList();

  // ------------------------------------------------------------- execution
  //
  // A Blueprint-style traversal: start at every Custom Event, follow the
  // white execution pins, and resolve data pins lazily (wired source first,
  // otherwise the pin's inline default).

  final Map<String, Object?> variables = {'Seed': 0};
  final List<String> log = [];
  final Map<String, int> _loopIndex = {};
  final Set<String> _resolving = {};
  int _steps = 0;
  double _seconds = 0;
  int _lastStepCount = 0;

  void clearLog() {
    log.clear();
    _lastStepCount = 0;
    notifyListeners();
  }

  /// Runs the graph and appends a transcript to [log].
  void run() {
    log.clear();
    _steps = 0;
    _seconds = 0;
    _loopIndex.clear();
    touched.clear();
    final events = nodes
        .where((n) => n.read<String>('bp', '') == 'event')
        .toList();
    if (events.isEmpty) {
      log.add('没有 Custom Event：右键画布添加一个，或按 F5 前至少放一个事件节点。');
    }
    for (final ev in events) {
      log.add('▶ ${ev.read<String>('eventName', 'Event')}  触发');
      _follow(PortRef(ev.id, 'then'), 0);
    }
    _lastStepCount = _steps;
    log.add('■ 执行结束：$_steps 步，延迟合计 ${(1000 * _seconds).round()} ms');
    notifyListeners();
  }

  void _follow(PortRef from, int depth) {
    for (final e in edges.where((x) => x.from == from).toList()) {
      final n = node(e.to.nodeId);
      if (n == null) continue;
      _step(n, depth + 1);
    }
  }

  void _step(GraphNode n, int depth) {
    final pad = '  ' * depth;
    if (depth > 48 || _steps > 400) {
      log.add('$pad! 超出步骤/深度上限，已中止');
      _steps = 401;
      return;
    }
    _steps++;
    touched.add(n.id);
    void out(String id) => _follow(PortRef(n.id, id), depth);
    switch (n.read<String>('bp', '')) {
      case 'branch':
        final cond = truthy(inputValue(n, 'condition'));
        log.add('${pad}if ${cond ? 'True' : 'False'}');
        out(cond ? 'true' : 'false');
      case 'sequence':
        out('then0');
        out('then1');
        out('then2');
      case 'delay':
        final d = _num(inputValue(n, 'duration'), 0.2);
        _seconds += d;
        log.add('${pad}delay $d s');
        out('then');
      case 'print':
        log.add('${pad}print "${display(inputValue(n, 'in'))}"');
        out('then');
      case 'setVar':
        final name = n.read<String>('varName', 'Var');
        final v = inputValue(n, 'value');
        variables[name] = v;
        log.add('$pad$name = ${display(v)}');
        out('then');
      case 'forEach':
        final count = _num(n.data['count'], 0).toInt().clamp(0, 64);
        for (var i = 0; i < count; i++) {
          if (_steps > 400) break;
          _loopIndex[n.id] = i;
          log.add('${pad}loop index $i');
          out('body');
        }
        _loopIndex.remove(n.id);
        out('completed');
      case 'event':
        out('then');
      default:
        out('then');
    }
  }

  /// Value arriving at an input pin: the wired source if any, else the pin's
  /// inline default stored under its `valueKey`.
  Object? inputValue(GraphNode n, String portId) {
    final spec = n.port(portId);
    final wired = edges
        .where((e) => e.to.nodeId == n.id && e.to.portId == portId)
        .firstOrNull;
    if (wired != null) return evaluate(wired.from);
    final key = spec?.valueKey;
    return key == null ? null : n.data[key];
  }

  /// Evaluates a data output. Pure nodes are computed on demand, so a cycle
  /// resolves to null instead of recursing forever.
  bool _sawCycle = false;

  /// Evaluates a data output. Pure nodes compute on demand, so a cycle has to
  /// be detected rather than recursed out of: once a cycle is seen anywhere
  /// below the top call, the whole expression resolves to null (Unreal shows
  /// the dependent pins as unset rather than as a half-computed value).
  Object? evaluate(PortRef ref) {
    final key = '${ref.nodeId}.${ref.portId}';
    final isTop = _resolving.isEmpty;
    if (isTop) _sawCycle = false;
    if (!_resolving.add(key)) {
      _sawCycle = true;
      return null;
    }
    final result = _compute(ref);
    _resolving.remove(key);
    return isTop && _sawCycle ? null : result;
  }

  Object? _compute(PortRef ref) {
    final n = node(ref.nodeId);
    if (n == null) return null;
    if (n.kind == NodeKind.reroute) {
      // A relay has no logic of its own: its output is whatever arrives at it.
      return ref.portId == 'out' ? inputValue(n, 'in') : null;
    }
    switch (n.read<String>('bp', '')) {
      case 'getVar':
        return variables[n.read<String>('varName', 'Var')];
      case 'addFloat':
        return _num(inputValue(n, 'a'), 0) + _num(inputValue(n, 'b'), 0);
      case 'compare':
        return _num(inputValue(n, 'a'), 0) == _num(inputValue(n, 'b'), 0);
      case 'toString':
        return display(inputValue(n, 'in'));
      case 'makeColor':
        return '(${_num(inputValue(n, 'r'), 0).toStringAsFixed(2)}, '
            '${_num(inputValue(n, 'g'), 0).toStringAsFixed(2)}, '
            '${_num(inputValue(n, 'b'), 0).toStringAsFixed(2)}, '
            '${_num(inputValue(n, 'a'), 0).toStringAsFixed(2)})';
      case 'forEach':
        final i = _loopIndex[n.id] ?? -1;
        return ref.portId == 'index' ? i : 'Item $i';
    }
    final valueKey = n.port(ref.portId)?.valueKey;
    return valueKey == null ? null : n.data[valueKey];
  }

  static double _num(Object? v, double fallback) => v is num
      ? v.toDouble()
      : (v is String ? double.tryParse(v) ?? fallback : fallback);

  static bool truthy(Object? v) => switch (v) {
    null => false,
    bool b => b,
    num n => n != 0,
    String s => s == 'true' || s == '1',
    _ => true,
  };

  static String display(Object? v) => switch (v) {
    null => '<未连接>',
    double d => d.toStringAsFixed(d == d.roundToDouble() ? 0 : 2),
    _ => '$v',
  };

  /// Nodes the last run actually executed through; they get a lit border.
  final Set<String> touched = {};

  int get lastStepCount => _lastStepCount;

  GraphEdge? edgeInto(PortRef ref) =>
      edges.where((e) => e.to == ref).firstOrNull;

  int get zoomPercent => (scale * 100).round();
}
