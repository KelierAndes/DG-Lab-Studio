import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/widgets.dart';

import '../app/tokens.dart';
import '../chrome/primitives.dart';
import '../flow/flow_card.dart';
import '../flow/flow_editor.dart';
import 'blueprint.dart';
import 'graph_model.dart';
import 'node_chrome.dart';
import 'nodes/blueprint_node.dart';
import 'nodes/generator_node.dart';
import 'nodes/model_node.dart';
import 'nodes/preview_node.dart';
import 'nodes/prompt_node.dart';
import 'painters.dart';

enum _Mode { none, pan, marquee, marqueeDelete }

/// 右键按下后要走哪条路判断用的位移：比 Flutter 的点击容差小一点，
/// 免得「刚拖出一点就既框选又弹菜单」。
const double _marqueeSlop = 6;

class GraphCanvas extends StatefulWidget {
  const GraphCanvas({
    super.key,
    required this.graph,
    required this.popover,
    required this.tokens,
    this.showGrid = true,
    this.flow,
    this.onAddCard,
  });

  final GraphController graph;
  final PopoverService popover;
  final AppTokens tokens;
  final bool showGrid;

  /// 事件流画布需要引擎目录与写回通道；图像工作流那套留空即可。
  final FlowEditor? flow;

  /// 右键空白处建卡：给了它就走带搜索的建卡面板（事件流页），否则用旧的分类弹层。
  final void Function(Offset world, Offset view)? onAddCard;

  @override
  State<GraphCanvas> createState() => _GraphCanvasState();
}

class _GraphCanvasState extends State<GraphCanvas> {
  final GlobalKey _boxKey = GlobalKey();
  _Mode _mode = _Mode.none;
  Offset _last = Offset.zero;
  Offset _marqueeFrom = Offset.zero;
  Offset _downAt = Offset.zero;

  /// 右键按下了但还没决定是「建卡菜单」还是「框选并删除」——移动超过阈值才定。
  bool _rightPending = false;

  /// 这一次右键已经当成框选用掉了，别再弹建卡菜单。
  bool _rightSpent = false;

  GraphController get graph => widget.graph;

  Offset toView(Offset global) {
    final box = _boxKey.currentContext?.findRenderObject() as RenderBox?;
    if (box == null) return global;
    return box.globalToLocal(global);
  }

  // ------------------------------------------------------------- background

  void _onDown(PointerDownEvent e) {
    if (e.buttons == 0) return;
    final view = toView(e.position);
    _downAt = view;
    _last = view;
    // 中键在 Flutter 里叫 kTertiaryButton。
    if (e.buttons & kTertiaryButton != 0) {
      // 中键：移动画布。左键那一划让给框选，与旧界面一致。
      _mode = _Mode.pan;
      return;
    }
    if (e.buttons & kSecondaryButton != 0) {
      _rightPending = true;
      _rightSpent = false;
      _mode = _Mode.none;
      return;
    }
    final edge = graph.edgeAt(view);
    if (edge != null) {
      graph.selectEdge(edge);
      _mode = _Mode.none;
      return;
    }
    graph.clear();
    widget.popover.close();
    // 左键在空白处：框选（卡片是各节点自己接的拖动，不会走到这里）。
    _mode = _Mode.marquee;
    _marqueeFrom = view;
    graph.marquee = Rect.fromLTWH(view.dx, view.dy, 0, 0);
    graph.poke();
  }

  void _onMove(PointerMoveEvent e) {
    final view = toView(e.position);
    if (_rightPending && (view - _downAt).distance > _marqueeSlop) {
      _rightPending = false;
      _rightSpent = true;
      graph.clear();
      _mode = _Mode.marqueeDelete;
      _marqueeFrom = _downAt;
    }
    switch (_mode) {
      case _Mode.pan:
        graph.beginPan(view - _last);
      case _Mode.marquee:
      case _Mode.marqueeDelete:
        graph.marquee = Rect.fromPoints(_marqueeFrom, view);
        graph.poke();
      case _Mode.none:
        final hit = graph.edgeAt(view);
        if (hit != graph.hoveredEdge) {
          graph.hoveredEdge = hit;
          graph.poke();
        }
    }
    _last = view;
  }

  /// 卡片在视口里的矩形（框选判交就用它，与 [_onUp] 老算法同一份）。
  Rect _nodeViewRect(GraphNode n) {
    final top = graph.toView(n.position);
    return Rect.fromPoints(
      top,
      graph.toView(
        Offset(n.position.dx + n.size.width, n.position.dy + n.size.height),
      ),
    );
  }

  void _onUp(PointerUpEvent e) {
    if (_mode == _Mode.marquee || _mode == _Mode.marqueeDelete) {
      final r = graph.marquee;
      if (r != null) {
        final hitNodes = <String>{};
        final hitEdges = <String>{};
        for (final n in graph.nodes) {
          if (!r.overlaps(_nodeViewRect(n))) continue;
          if (_mode == _Mode.marqueeDelete) {
            hitNodes.add(n.id);
          } else {
            graph.selection.add(n.id);
          }
        }
        if (_mode == _Mode.marqueeDelete) {
          // 线没有「面积」，按中点算：圈住线中间那截就算断它。
          for (final edge in graph.edges) {
            final g = graph.edgeGeometry(edge);
            if (g == null) continue;
            final mid = graph.toView((g.start + g.end) / 2);
            if (r.contains(mid)) hitEdges.add(edge.id);
          }
          graph.deleteNodesAndEdges(hitNodes, hitEdges);
        }
      }
      graph.marquee = null;
      graph.poke();
    }
    _mode = _Mode.none;
    if (_rightPending) _rightPending = false;
  }

  void _onSignal(PointerSignalEvent e) {
    if (e is! PointerScrollEvent) return;
    final view = toView(e.position);
    if (e.scrollDelta.dy == 0) return;
    final factor = math.exp(-e.scrollDelta.dy * 0.0016);
    graph.zoomAt(view, factor);
  }

  static const _imageKinds = [
    NodeKind.model,
    NodeKind.prompt,
    NodeKind.generator,
    NodeKind.preview,
  ];

  /// 事件流的右键：直接打开带快捷搜索的建卡面板（旧界面就是这个，不是两级分类菜单）。
  void _onFlowTap(TapUpDetails d, FlowEditor flow, Offset world) {
    widget.onAddCard?.call(world, toView(d.globalPosition));
  }

  void _onSecondaryTap(TapUpDetails d) {
    if (_rightSpent) {
      // 这一下右键已经用作框选删除了，不该再弹建卡菜单。
      _rightSpent = false;
      return;
    }
    final view = toView(d.globalPosition);
    final world = graph.toWorld(view);
    final edge = graph.edgeAt(view);
    if (edge != null) {
      // 右键点在线上 = 断这条线：以前要先把线点中再按 Delete，
      // 而那条线细得根本点不准。
      graph.selectEdge(edge);
      graph.deleteSelection();
      return;
    }
    if (graph.nodeAtWorld(world) != null) {
      return; // node menus come from DragZone
    }
    final flow = widget.flow;
    if (flow != null) return _onFlowTap(d, flow, world);
    widget.popover.show(
      PopoverSpec(
        title: '添加节点',
        anchor: d.globalPosition,
        width: 196,
        entries: const [
          MenuEntry('图像工作流', hint: '4'),
          MenuEntry('流程控制', hint: '5'),
          MenuEntry('数据与运算', hint: '4'),
          MenuEntry('变量', hint: '2'),
          MenuEntry('调试输出', hint: '1'),
          MenuEntry('注释与中继', hint: '2'),
        ],
        onPick: (i) => _openCategory(i, d.globalPosition, world),
      ),
    );
  }

  /// Category index 0 is the image workflow; the rest map onto [bpCategories].
  void _openCategory(int category, Offset anchor, Offset world) {
    if (category == 0) {
      widget.popover.show(
        PopoverSpec(
          title: '图像工作流',
          anchor: anchor,
          width: 196,
          entries: const [
            MenuEntry('Model'),
            MenuEntry('Prompt'),
            MenuEntry('Image Generator'),
            MenuEntry('Preview Image'),
          ],
          onPick: (i) => _spawn(_imageKinds[i], world),
        ),
      );
      return;
    }
    // Only the annotation category has hand-picked extras; the map is sparse.
    final kinds = [
      ...?_annotationTypes[category - 1],
      ...typesIn(bpCategories[category - 1]),
    ];
    widget.popover.show(
      PopoverSpec(
        title: bpCategories[category - 1].label,
        anchor: anchor,
        width: 196,
        entries: [
          for (final k in kinds)
            MenuEntry(
              k == 'reroute'
                  ? 'Reroute 中继'
                  : k == 'comment'
                  ? 'Comment 注释框'
                  : bpMenuLabel(k),
            ),
        ],
        onPick: (i) => _spawnType(kinds[i], world),
      ),
    );
  }

  static const Map<int, List<String>> _annotationTypes = {
    4: ['reroute', 'comment'],
  };

  void _spawn(NodeKind kind, Offset world) {
    final spec = nodeSpecFor(kind, world);
    graph.addNode(
      kind: spec.kind,
      at: world,
      title: spec.title,
      size: spec.size,
      ports: spec.ports,
      data: Map<String, Object?>.of(spec.data),
    );
  }

  void _spawnType(String type, Offset world) {
    if (type == 'reroute') return _spawn(NodeKind.reroute, world);
    if (type == 'comment') return _spawn(NodeKind.comment, world);
    final spec = buildBlueprint(type, {});
    graph.addNode(
      kind: NodeKind.blueprint,
      at: world,
      title: spec.title,
      size: spec.size,
      ports: spec.ports,
      data: Map<String, Object?>.of(spec.data),
    );
  }

  // -------------------------------------------------------------- rendering

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: graph,
      builder: (context, _) {
        return CanvasScope(
          graph: graph,
          toView: toView,
          popover: widget.popover,
          child: ClipRect(
            child: DecoratedBox(
              key: _boxKey,
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [widget.tokens.canvasTop, widget.tokens.canvasBottom],
                ),
              ),
              child: LayoutBuilder(
                builder: (context, box) => Stack(
                  clipBehavior: Clip.none,
                  children: [
                    Positioned.fill(
                      child: Listener(
                        onPointerDown: _onDown,
                        onPointerMove: _onMove,
                        onPointerUp: _onUp,
                        onPointerCancel: (_) => _mode = _Mode.none,
                        onPointerSignal: _onSignal,
                        child: GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onSecondaryTapUp: _onSecondaryTap,
                          onDoubleTap: () => graph.fitView(box.biggest),
                          child: CustomPaint(
                            painter: GridPainter(
                              pan: graph.pan,
                              scale: graph.scale,
                              tokens: widget.tokens,
                              enabled: widget.showGrid,
                            ),
                            child: const SizedBox.expand(),
                          ),
                        ),
                      ),
                    ),
                    Positioned.fill(
                      child: IgnorePointer(
                        child: CustomPaint(
                          painter: EdgePainter(
                            graph: graph,
                            tokens: widget.tokens,
                          ),
                        ),
                      ),
                    ),
                    Positioned.fill(
                      child: Stack(
                        clipBehavior: Clip.none,
                        children: [
                          // Nodes are placed in view space and scaled one by
                          // one. One transformed Stack would be viewport-sized,
                          // and RenderBox gates hit testing on `size.contains`,
                          // so every node whose *world* position falls outside
                          // 0..viewport would paint fine but swallow nothing —
                          // panning over to it left the whole card dead.
                          // Comment bubbles are a backdrop: they must never
                          // cover or swallow the nodes they group.
                          for (final n in [
                            ...graph.nodes.where(
                              (x) => x.kind == NodeKind.comment,
                            ),
                            ...graph.nodes.where(
                              (x) => x.kind != NodeKind.comment,
                            ),
                          ])
                            Positioned(
                              // The key has to be on the Positioned: a
                              // drag calls bringToFront, and without it the
                              // reorder is matched by index, which throws
                              // away the node's element (and with it the
                              // in-flight drag state).
                              key: ValueKey('pos-${n.id}'),
                              left: graph.toView(n.position).dx,
                              top: graph.toView(n.position).dy,
                              child: Transform.scale(
                                scale: graph.scale,
                                alignment: Alignment.topLeft,
                                child: FlowScopeWrap(
                                  editor: widget.flow,
                                  child: NodeView(
                                    key: ValueKey(n.id),
                                    node: n,
                                    graph: graph,
                                    popover: widget.popover,
                                    selected: graph.selection.contains(n.id),
                                  ),
                                ),
                              ),
                            ),
                        ],
                      ),
                    ),
                    Positioned.fill(
                      child: IgnorePointer(
                        child: CustomPaint(
                          painter: CanvasOverlayPainter(
                            marquee: graph.marquee,
                            tokens: widget.tokens,
                          ),
                        ),
                      ),
                    ),
                    Positioned.fill(
                      child: _PortLayer(graph: graph, tokens: widget.tokens),
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

/// Interactive port sockets, painted above the node layer so they stay
/// clickable and keep a constant size at any zoom.
class _PortLayer extends StatelessWidget {
  const _PortLayer({required this.graph, required this.tokens});

  final GraphController graph;
  final AppTokens tokens;

  @override
  Widget build(BuildContext context) {
    return Stack(
      clipBehavior: Clip.none,
      children: [
        for (final n in graph.nodes)
          for (final p in n.ports)
            Builder(
              builder: (context) {
                final world = n.portWorld(p);
                final v = graph.toView(world);
                return Positioned(
                  left: v.dx - 11,
                  top: v.dy - 11,
                  child: PortSocket(
                    node: n,
                    spec: p,
                    graph: graph,
                    tokens: tokens,
                  ),
                );
              },
            ),
      ],
    );
  }
}

class PortSocket extends StatefulWidget {
  const PortSocket({
    super.key,
    required this.node,
    required this.spec,
    required this.graph,
    required this.tokens,
  });

  final GraphNode node;
  final PortSpec spec;
  final GraphController graph;
  final AppTokens tokens;

  @override
  State<PortSocket> createState() => _PortSocketState();
}

class _PortSocketState extends State<PortSocket> {
  bool _hot = false;

  bool get _isSource =>
      widget.graph.pending?.origin == PortRef(widget.node.id, widget.spec.id);
  bool get _isHoverTarget =>
      widget.graph.pending?.hover == PortRef(widget.node.id, widget.spec.id);
  bool get _wired =>
      widget.graph.edges.any(
        (e) =>
            e.from.nodeId == widget.node.id && e.from.portId == widget.spec.id,
      ) ||
      widget.graph.edges.any(
        (e) => e.to.nodeId == widget.node.id && e.to.portId == widget.spec.id,
      );

  @override
  Widget build(BuildContext context) {
    final t = widget.tokens;
    final color = t.portColor(widget.spec.kind);
    final active = _isSource || _isHoverTarget;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) {
        setState(() => _hot = true);
        widget.graph.hoveredPort = PortRef(widget.node.id, widget.spec.id);
      },
      onExit: (_) {
        setState(() => _hot = false);
        widget.graph.hoveredPort = null;
      },
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onPanDown: (d) {
          final view = CanvasScope.of(context).toView(d.globalPosition);
          widget.graph.beginLink(
            PortRef(widget.node.id, widget.spec.id),
            widget.graph.toWorld(view),
          );
        },
        onPanUpdate: (d) => widget.graph.updateLink(
          CanvasScope.of(context).toView(d.globalPosition),
        ),
        onPanEnd: (d) => widget.graph.commitLink(
          CanvasScope.of(context).toView(d.globalPosition),
        ),
        onPanCancel: () => widget.graph.cancelLink(),
        child: SizedBox(
          width: 22,
          height: 22,
          child: widget.spec.isExec
              ? Center(
                  child: CustomPaint(
                    size: Size(
                      active ? 13 : (_hot ? 11 : 9),
                      active ? 13 : (_hot ? 11 : 9),
                    ),
                    painter: ExecPinPainter(
                      // 三角一律朝着这一页的执行流向（与卡片里的引脚箭头同一判据）：
                      // 输出页 ▶、输入页镜像 ◀。以前按「进 / 出」分向画，
                      // 镜像页上出脚那颗会反过来指着卡片内部。
                      pointsRight: !widget.node.mirror,
                      color: color,
                      ring: _isHoverTarget
                          ? t.accent
                          : (active ? t.edgeActive : null),
                      glow: _wired || _hot
                          ? color.withAlpha(active ? 190 : 90)
                          : null,
                      blur: active ? 12 : 7,
                    ),
                  ),
                )
              : Center(
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 90),
                    width: active ? 13 : (_hot ? 11 : 9),
                    height: active ? 13 : (_hot ? 11 : 9),
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: t.wellDeep,
                      border: Border.all(
                        color: _isHoverTarget
                            ? t.accent
                            : (active ? t.edgeActive : Colors.transparent),
                        width: 1.6,
                      ),
                      boxShadow: _wired || _hot
                          ? [
                              BoxShadow(
                                color: color.withAlpha(active ? 190 : 90),
                                blurRadius: active ? 12 : 7,
                              ),
                            ]
                          : null,
                    ),
                    child: Center(
                      child: Container(
                        width: active ? 6 : 4.6,
                        height: active ? 6 : 4.6,
                        decoration: BoxDecoration(
                          color: color,
                          shape: BoxShape.circle,
                        ),
                      ),
                    ),
                  ),
                ),
        ),
      ),
    );
  }
}

/// Routes a node id to its card implementation.
class NodeView extends StatelessWidget {
  const NodeView({
    super.key,
    required this.node,
    required this.graph,
    required this.popover,
    required this.selected,
  });

  final GraphNode node;
  final GraphController graph;
  final PopoverService popover;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    // Deliberately not `const`: Flutter skips `Element.update` when the new
    // and old widget are the identical const instance, which would freeze the
    // card against every later graph change (wiring, selection, captions).
    final body = switch (node.kind) {
      NodeKind.model => ModelNodeView(),
      NodeKind.prompt => PromptNodeView(),
      NodeKind.generator => GeneratorNodeView(),
      NodeKind.preview => PreviewNodeView(),
      NodeKind.blueprint => BlueprintNodeView(),
      NodeKind.reroute => RerouteNodeView(),
      NodeKind.comment => CommentNodeView(),
      // 同样不能 const：identical 的实例会让 Element.update 跳过重建，
      // 引擎回传的参数与别名就永远画不出来。
      NodeKind.flow => FlowNodeView(),
    };
    return NodeScope(
      node: node,
      graph: graph,
      popover: popover,
      child: GraphLabel(
        wired: (nodeId, portId) => graph.edges.any(
          (e) =>
              (e.from.nodeId == nodeId && e.from.portId == portId) ||
              (e.to.nodeId == nodeId && e.to.portId == portId),
        ),
        child: body,
      ),
    );
  }
}

/// Shape of a freshly spawned node.
class NodeShape {
  const NodeShape(this.kind, this.title, this.size, this.ports, this.data);

  final NodeKind kind;
  final String title;
  final Size size;
  final List<PortSpec> ports;
  final Map<String, Object?> data;
}

NodeShape nodeSpecFor(NodeKind kind, Offset at) {
  switch (kind) {
    case NodeKind.flow:
      // 事件流卡片由引擎目录描述，形状在建立节点时已经算好。
      return const NodeShape(NodeKind.flow, '事件流卡片', Size(200, 74), [], {});
    case NodeKind.model:
      return const NodeShape(NodeKind.model, 'Model', modelSize, modelPorts, {
        'checkpoint': 'DreamShaper 6 (SD1.5)',
      });
    case NodeKind.prompt:
      return const NodeShape(
        NodeKind.prompt,
        'Prompt',
        promptSize,
        promptPorts,
        {},
      );
    case NodeKind.generator:
      return const NodeShape(
        NodeKind.generator,
        'Image Generator',
        generatorSize,
        generatorPorts,
        {
          'seed': '12345',
          'control': 'Fixed',
          'steps': 30,
          'strength': '6.5',
          'sampler': 'dpm++ 2M',
        },
      );
    case NodeKind.preview:
      return const NodeShape(
        NodeKind.preview,
        'Preview Image',
        previewSize,
        previewPorts,
        {'zoom': '2x', 'format': 'PNG'},
      );
    case NodeKind.reroute:
      return const NodeShape(
        NodeKind.reroute,
        'Reroute',
        Size.square(RerouteNodeView.size),
        [
          PortSpec(
            id: 'in',
            kind: PortKind.wildcard,
            dir: PortDir.input,
            label: '',
            dy: RerouteNodeView.size / 2,
          ),
          PortSpec(
            id: 'out',
            kind: PortKind.wildcard,
            dir: PortDir.output,
            label: '',
            dy: RerouteNodeView.size / 2,
          ),
        ],
        {},
      );
    case NodeKind.comment:
      return const NodeShape(NodeKind.comment, '注释', Size(320, 190), [], {
        'body': '把相关节点圈起来',
        'tone': 'gray',
      });
    case NodeKind.blueprint:
      return const NodeShape(
        NodeKind.blueprint,
        'Custom Event',
        Size(190, 73),
        [],
        {'bp': 'event', 'eventName': 'Event'},
      );
  }
}

/// Unreal draws execution pins as triangles pointing along the flow.
class ExecPinPainter extends CustomPainter {
  const ExecPinPainter({
    required this.pointsRight,
    required this.color,
    required this.ring,
    required this.glow,
    required this.blur,
  });

  final bool pointsRight;
  final Color color;
  final Color? ring;
  final Color? glow;
  final double blur;

  @override
  void paint(Canvas canvas, Size size) {
    final path = Path();
    if (pointsRight) {
      path
        ..moveTo(0, 0)
        ..lineTo(size.width, size.height / 2)
        ..lineTo(0, size.height)
        ..close();
    } else {
      path
        ..moveTo(size.width, 0)
        ..lineTo(0, size.height / 2)
        ..lineTo(size.width, size.height)
        ..close();
    }
    if (glow != null) {
      canvas.drawShadow(path, glow!, 1.5, false);
    }
    canvas.drawPath(path, Paint()..color = color);
    if (ring != null) {
      canvas.drawPath(
        path,
        Paint()
          ..color = ring!
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.8,
      );
    }
  }

  @override
  bool shouldRepaint(ExecPinPainter old) =>
      old.pointsRight != pointsRight ||
      old.color != color ||
      old.ring != ring ||
      old.blur != blur;
}
