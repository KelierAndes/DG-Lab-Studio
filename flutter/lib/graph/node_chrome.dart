import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/widgets.dart';

import '../app/tokens.dart';
import '../chrome/primitives.dart';
import 'graph_model.dart';

/// Shared handle on the viewport: lets any descendant convert a global pointer
/// position into canvas-local ("view") coordinates.
class CanvasScope extends InheritedWidget {
  const CanvasScope({
    super.key,
    required this.graph,
    required this.toView,
    required this.popover,
    required super.child,
  });

  final GraphController graph;
  final Offset Function(Offset global) toView;
  final PopoverService popover;

  static CanvasScope of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<CanvasScope>()!;

  @override
  bool updateShouldNotify(CanvasScope old) => false;
}

/// Exposes the node a widget belongs to so inner parts can start a drag or
/// open the node context menu without threading callbacks through every
/// node-kind file.
class NodeScope extends InheritedWidget {
  const NodeScope({
    super.key,
    required this.node,
    required this.graph,
    required this.popover,
    required super.child,
  });

  final GraphNode node;
  final GraphController graph;
  final PopoverService popover;

  static NodeScope of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<NodeScope>()!;

  @override
  bool updateShouldNotify(NodeScope old) => old.node != node;
}

/// Wraps any visually-draggable part of a node (header, card body). Uses a pan
/// recognizer so a click inside a text field still reaches the field.
class DragZone extends StatefulWidget {
  const DragZone({
    super.key,
    required this.child,
    this.menuBuilder,
    this.opaque = true,
  });

  final Widget child;
  final List<MenuEntry> Function()? menuBuilder;
  final bool opaque;

  @override
  State<DragZone> createState() => _DragZoneState();
}

class _DragZoneState extends State<DragZone> {
  Offset? _lastView;

  void _start(DragDownDetails d) {
    final scope = CanvasScope.of(context);
    final node = NodeScope.of(context).node;
    _lastView = scope.toView(d.globalPosition);
    scope.graph.bringToFront(node.id);
    if (!scope.graph.selection.contains(node.id)) scope.graph.select(node.id);
  }

  void _update(DragUpdateDetails d) {
    final scope = CanvasScope.of(context);
    final view = scope.toView(d.globalPosition);
    final prev = _lastView;
    _lastView = view;
    if (prev == null) return;
    scope.graph.translateSelection((view - prev) / scope.graph.scale);
  }

  void _end(DragEndDetails d) => _lastView = null;

  @override
  Widget build(BuildContext context) {
    final menu = widget.menuBuilder;
    return GestureDetector(
      behavior: widget.opaque
          ? HitTestBehavior.opaque
          : HitTestBehavior.deferToChild,
      onPanDown: _start,
      onPanUpdate: _update,
      onPanEnd: _end,
      onSecondaryTapUp: menu == null
          ? null
          : (d) {
              final scope = CanvasScope.of(context);
              final node = NodeScope.of(context).node;
              scope.graph.select(node.id);
              scope.popover.show(
                PopoverSpec(
                  anchor: d.globalPosition,
                  width: 168,
                  entries: menu(),
                  onPick: (i) => _pick(i),
                ),
              );
            },
      child: widget.child,
    );
  }

  void _pick(int i) {
    final scope = CanvasScope.of(context);
    final node = NodeScope.of(context).node;
    switch (i) {
      case 0:
        scope.graph.select(node.id);
        break;
      case 1:
        final hook = scope.graph.onDuplicate;
        if (hook != null) {
          hook();
          break;
        }
        scope.graph.selection
          ..clear()
          ..add(node.id);
        scope.graph.duplicateSelection();
        break;
      case 2:
        scope.graph.selection
          ..clear()
          ..add(node.id);
        scope.graph.deleteSelection();
        break;
    }
  }
}

/// The raised card every node is drawn on.
class NodeCard extends StatelessWidget {
  const NodeCard({
    super.key,
    required this.width,
    required this.height,
    required this.child,
    this.selected = false,
    this.padding = const EdgeInsets.all(10),
    this.transparent = false,
    this.glow,
  });

  final double width;
  final double height;
  final Widget child;
  final bool selected;
  final EdgeInsetsGeometry padding;
  final bool transparent;
  final Color? glow;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return SizedBox(
      width: width,
      height: height,
      child: transparent
          ? child
          : DecoratedBox(
              decoration: BoxDecoration(
                color: t.surface,
                borderRadius: BorderRadius.circular(AppTokens.radiusNode),
                border: Border.all(
                  color: selected ? t.edgeActive.withAlpha(150) : t.hairline,
                ),
                boxShadow: [
                  if (glow case final g?)
                    BoxShadow(
                      color: g.withAlpha(46),
                      blurRadius: 26,
                      spreadRadius: -4,
                    ),
                  ...t.raised(depth: selected ? 1.5 : 1.1),
                ],
              ),
              child: Padding(padding: padding, child: child),
            ),
    );
  }
}

/// "● Title" node header used by all four kinds.
class NodeHeader extends StatelessWidget {
  const NodeHeader(
    this.title, {
    super.key,
    this.bullet,
    this.titleColor,
    this.trailing,
    this.height = 24,
  });

  final String title;
  final Color? bullet;

  /// 标题跟着节点类型的主题色走；不给就用常规次级文字色。
  final Color? titleColor;
  final Widget? trailing;
  final double height;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return SizedBox(
      height: height,
      child: Row(
        children: [
          Dot(color: bullet ?? t.textSecondary, size: 4.5),
          const SizedBox(width: 7),
          // 标题可能比卡片还宽（别名、未识别的 def key），让它自己截断，
          // 不要把整行顶出 RenderFlex 的边界。
          Expanded(
            child: Text(
              title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 12,
                height: 1,
                color: titleColor ?? t.textSecondary,
                fontWeight: FontWeight.w600,
                letterSpacing: .1,
              ),
            ),
          ),
          ?trailing,
        ],
      ),
    );
  }
}

/// The text half of a port row. The coloured socket itself is drawn by the
/// canvas overlay (so it stays hit-testable and a constant size when zoomed);
/// this label is positioned 8px away from that socket.
class PortRow extends StatelessWidget {
  const PortRow({
    super.key,
    required this.spec,
    required this.nodeId,
    this.width,
  });

  final PortSpec spec;
  final String nodeId;
  final double? width;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final wired = GraphLabel.wiredOf(context, nodeId, spec.id);
    return SizedBox(
      height: 18,
      width: width,
      child: Align(
        alignment: spec.dir == PortDir.output
            ? Alignment.centerRight
            : Alignment.centerLeft,
        child: Text(
          spec.label,
          style: TextStyle(
            fontSize: 11.5,
            height: 1,
            color: wired ? t.textSecondary : t.textTertiary,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }
}

/// Carries the "is this port wired" answer down to [PortRow] without every node
/// kind having to query the controller itself.
class GraphLabel extends InheritedWidget {
  const GraphLabel({super.key, required this.wired, required super.child});

  final bool Function(String nodeId, String portId) wired;

  static bool wiredOf(BuildContext context, String nodeId, String portId) =>
      context.dependOnInheritedWidgetOfExactType<GraphLabel>()?.wired(
        nodeId,
        portId,
      ) ??
      false;

  @override
  bool updateShouldNotify(GraphLabel old) => false;
}
