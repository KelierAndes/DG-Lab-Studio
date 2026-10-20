import 'dart:math' as math;

import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/widgets.dart';

import '../../app/field_metrics.dart';
import '../../app/tokens.dart';
import '../../chrome/primitives.dart';
import '../blueprint.dart';
import '../graph_model.dart';
import '../node_chrome.dart';

/// Renders any registry entry. Blueprint nodes are uniform enough that one
/// layout pass driven by [NodeBlueprintDef] beats eight bespoke widgets, and it
/// keeps the pin rows and the socket overlay on the same arithmetic.
class BlueprintNodeView extends StatelessWidget {
  const BlueprintNodeView({super.key});

  @override
  Widget build(BuildContext context) {
    final scope = NodeScope.of(context);
    final node = scope.node;
    final t = AppScope.of(context);
    final def = blueprintRegistry[node.read<String>('bp', 'event')]!;
    final selected = scope.graph.selection.contains(node.id);
    final touched = scope.graph.touched.contains(node.id);

    // The registry width is the floor; a long label or value widens the card
    // and, with it, the socket line on the right edge.
    final w = node.size.width;
    return NodeCard(
      width: w,
      height: def.height,
      selected: selected,
      padding: EdgeInsets.zero,
      glow: touched ? t.accent : def.category.tint,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppTokens.radiusNode),
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            // The card family speaks through light, not through saturated
            // fills: one soft leak in the category colour, top-right, exactly
            // where the Image Generator carries its pink one.
            Positioned.fill(
              child: IgnorePointer(
                child: CustomPaint(
                  painter: CategoryLeakPainter(
                    tint: def.category.tint,
                    tokens: t,
                  ),
                ),
              ),
            ),
            Positioned(
              left: 10,
              right: 10,
              top: NodeBlueprintDef.padTop + NodeBlueprintDef.headerH - 2,
              bottom: 6,
              child: const IgnorePointer(
                child: WellBox(child: SizedBox.expand()),
              ),
            ),
            Positioned(
              left: 0,
              right: 0,
              top: 0,
              height: NodeBlueprintDef.padTop + NodeBlueprintDef.headerH,
              child: DragZone(
                menuBuilder: () => _menu(node, scope.graph),
                child: Padding(
                  padding: EdgeInsets.fromLTRB(
                    11,
                    def.subtitleKey == null ? 9 : 5,
                    11,
                    0,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      NodeHeader(
                        def.title,
                        bullet: def.category.tint,
                        height: 18,
                      ),
                      if (def.subtitleKey != null)
                        Padding(
                          padding: const EdgeInsets.only(left: 11.5, top: 1),
                          child: Text(
                            '${node.data[def.subtitleKey!]}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 10,
                              height: 1.2,
                              color: t.textTertiary,
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
            for (var i = 0; i < def.fields.length; i++)
              Positioned(
                left: 17,
                right: 17,
                top:
                    NodeBlueprintDef.padTop +
                    NodeBlueprintDef.headerH +
                    i * NodeBlueprintDef.fieldH,
                height: NodeBlueprintDef.fieldH,
                child: BpFieldRow(def: def, field: def.fields[i], node: node),
              ),
            for (final p in def.buildPorts())
              // A Positioned with only one horizontal edge gets unbounded
              // width from a Stack, which breaks Flexible rows: pin both.
              Positioned(
                left: p.dir == PortDir.input ? 18 : w / 2 - 4,
                right: p.dir == PortDir.input ? w / 2 + 4 : 18,
                top: p.dy - 9,
                height: 18,
                child: _PinLabel(
                  spec: p,
                  node: node,
                  def: def,
                  graph: scope.graph,
                ),
              ),
          ],
        ),
      ),
    );
  }

  static List<MenuEntry> _menu(GraphNode node, GraphController g) => const [
    MenuEntry('选中'),
    MenuEntry('复制节点'),
    MenuEntry('断开全部连线'),
    MenuEntry('删除', danger: true),
  ];
}

class _PinLabel extends StatelessWidget {
  const _PinLabel({
    required this.spec,
    required this.node,
    required this.def,
    required this.graph,
  });

  final PortSpec spec;
  final GraphNode node;
  final NodeBlueprintDef def;
  final GraphController graph;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final wired = GraphLabel.wiredOf(context, node.id, spec.id);
    // Same voice as PortRow on the four image cards: one size, one weight,
    // and the wired/unired pair carries the emphasis instead of colour.
    final label = Text(
      spec.label,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      // The same style the width maths measured with, so the label can never
      // come out wider than the room it was given.
      style: pinLabelStyle.copyWith(
        color: wired ? t.textSecondary : t.textTertiary,
      ),
    );
    if (spec.dir == PortDir.output) {
      return Align(alignment: Alignment.centerRight, child: label);
    }

    // An unwired input with a default value gets Unreal's inline editor.
    final key = spec.valueKey;
    if (key == null || wired || !node.data.containsKey(key)) return label;
    return Row(
      children: [
        Flexible(child: label),
        const SizedBox(width: 6),
        PinValueEditor(node: node, spec: spec),
      ],
    );
  }
}

/// The small in-pin editor: a checkbox for bools, a numeric box otherwise.
/// Both are fixed-width so the shrink-wrapped label can never overflow.
class PinValueEditor extends StatelessWidget {
  const PinValueEditor({super.key, required this.node, required this.spec});

  final GraphNode node;
  final PortSpec spec;

  @override
  Widget build(BuildContext context) {
    final key = spec.valueKey!;
    if (spec.kind == PortKind.boolean) {
      return PinBoolBox(
        node: node,
        valueKey: key,
        on: GraphController.truthy(node.data[key]),
      );
    }
    return SizedBox(
      width: pinEditorWidth(node.data[key]),
      child: MiniField(
        controller: _nodeCtl(node, key),
        syncText: displayValue(node.data[key]),
        height: 17,
        fontSize: 10.5,
        radius: 6,
        onSubmitted: (v) {
          final parsed = num.tryParse(v.trim());
          node.set(key, parsed ?? v.trim());
        },
      ),
    );
  }
}

/// The compact sibling of [InlineField]: same carved well and the same blue
/// focus ring, shrunk to fit a pin row. Every text editor in the graph goes
/// through it so a pin default and a generator parameter cannot drift apart.
class MiniField extends StatefulWidget {
  const MiniField({
    super.key,
    required this.controller,
    required this.onSubmitted,
    required this.height,
    required this.fontSize,
    required this.radius,
    this.syncText,
  });

  final TextEditingController controller;
  final ValueChanged<String> onSubmitted;
  final double height;
  final double fontSize;
  final double radius;

  /// The value the field should show when the graph writes it from elsewhere —
  /// a Set node, a preset. Without this the cached controller keeps whatever
  /// was typed first and the field lies about the pin's value.
  final String? syncText;

  @override
  State<MiniField> createState() => _MiniFieldState();
}

class _MiniFieldState extends State<MiniField> {
  late final FocusNode _focusNode = FocusNode()
    ..addListener(() => setState(() => _focus = _focusNode.hasFocus));
  bool _focus = false;

  @override
  void didUpdateWidget(MiniField old) {
    super.didUpdateWidget(old);
    final next = widget.syncText;
    if (_focus || next == null || next == old.syncText) return;
    if (widget.controller.text != next) widget.controller.text = next;
  }

  @override
  void dispose() {
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    // A natural line box plus equal top and bottom padding centres the value.
    // Stretching the line height instead leaves the baseline high in the box.
    const lineHeight = 1.4;
    final v = math.max(0.0, (widget.height - widget.fontSize * lineHeight) / 2);
    return SizedBox(
      height: widget.height,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: t.wellDeep,
          borderRadius: BorderRadius.circular(widget.radius),
          border: Border.all(
            color: _focus
                ? t.edgeActive.withAlpha(120)
                : Colors.black.withAlpha(110),
          ),
          boxShadow: t.inset,
        ),
        child: Padding(
          padding: EdgeInsets.symmetric(
            horizontal: widget.height > 19 ? 7 : 5,
            vertical: v,
          ),
          child: singleLineBox(
            TextField(
              controller: widget.controller,
              focusNode: _focusNode,
              textAlignVertical: TextAlignVertical.center,
              style: TextStyle(
                fontSize: widget.fontSize,
                height: lineHeight,
                color: t.textPrimary,
              ),
              strutStyle: fieldStrut(fontSize: widget.fontSize),
              cursorColor: t.accent,
              cursorWidth: 1.3,
              decoration: InputDecoration(
                isDense: true,
                contentPadding: EdgeInsets.zero,
                border: InputBorder.none,
              ),
              onSubmitted: widget.onSubmitted,
            ),
            fontSize: 11.5,
          ),
        ),
      ),
    );
  }
}

/// Keeps one [TextEditingController] per data key alive on the node so editing
/// a field never loses the caret when the graph repaints.
TextEditingController _nodeCtl(GraphNode node, String key) {
  final id = 'ctl-$key';
  final existing = node.data[id];
  if (existing is TextEditingController) return existing;
  final created = TextEditingController(text: node.data[key]?.toString() ?? '');
  node.data[id] = created;
  return created;
}

/// The boolean pin's editor, drawn rather than taken from the Material kit so
/// it sits in the same embossed family as the fields around it.
class PinBoolBox extends StatelessWidget {
  const PinBoolBox({
    super.key,
    required this.node,
    required this.valueKey,
    required this.on,
  });

  final GraphNode node;
  final String valueKey;
  final bool on;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => node.set(valueKey, !on),
      child: SizedBox(
        width: 16,
        height: 16,
        child: Center(
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 110),
            width: 14,
            height: 14,
            decoration: BoxDecoration(
              color: on ? t.accent : t.wellDeep,
              borderRadius: BorderRadius.circular(4),
              border: Border.fromBorderSide(
                BorderSide(color: on ? t.accent : Colors.black.withAlpha(120)),
              ),
              boxShadow: on
                  ? [
                      BoxShadow(
                        color: t.accent.withAlpha(70),
                        blurRadius: 8,
                        spreadRadius: -1,
                      ),
                    ]
                  : t.inset,
            ),
            child: on
                ? CustomPaint(painter: CheckPainter(color: t.textOnAccent))
                : null,
          ),
        ),
      ),
    );
  }
}

class CheckPainter extends CustomPainter {
  const CheckPainter({required this.color});

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final path = Path()
      ..moveTo(size.width * .22, size.height * .53)
      ..lineTo(size.width * .42, size.height * .73)
      ..lineTo(size.width * .80, size.height * .28);
    canvas.drawPath(
      path,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.9
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round
        ..color = color,
    );
  }

  @override
  bool shouldRepaint(CheckPainter old) => old.color != color;
}

/// A labelled value line inside a Blueprint node body (variable name, loop
/// count, event name).
class BpFieldRow extends StatelessWidget {
  const BpFieldRow({
    super.key,
    required this.def,
    required this.field,
    required this.node,
  });

  final NodeBlueprintDef def;
  final BpField field;
  final GraphNode node;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      children: [
        SizedBox(
          width: 44,
          child: Text(
            field.label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: 11,
              height: 1,
              color: t.textTertiary,
              letterSpacing: .1,
            ),
          ),
        ),
        const SizedBox(width: 6),
        Expanded(
          child: MiniField(
            controller: _nodeCtl(node, field.key),
            syncText: displayValue(node.data[field.key]),
            height: 21,
            fontSize: 11,
            radius: AppTokens.radiusField - 3,
            onSubmitted: (v) {
              final parsed = num.tryParse(v.trim());
              node.set(field.key, switch (field.kind) {
                BpFieldKind.whole => (parsed?.toInt() ?? 0),
                BpFieldKind.decimal => (parsed?.toDouble() ?? 0.0),
                _ => v,
              });
            },
          ),
        ),
      ],
    );
  }
}

/// The bare circular relay Unreal (and every shader editor) uses to bend a
/// wire. It inherits the colour of whatever flows through it.
class RerouteNodeView extends StatelessWidget {
  const RerouteNodeView({super.key});

  static const size = 26.0;

  @override
  Widget build(BuildContext context) {
    final scope = NodeScope.of(context);
    final node = scope.node;
    final t = AppScope.of(context);
    final selected = scope.graph.selection.contains(node.id);

    PortRef? incoming;
    for (final e in scope.graph.edges) {
      if (e.to.nodeId == node.id) incoming = e.from;
    }
    final source = incoming == null ? null : scope.graph.specOf(incoming);
    final tint = source == null
        ? t.portColor(PortKind.wildcard)
        : t.portColor(source.kind);

    return SizedBox(
      width: size,
      height: size,
      child: DragZone(
        menuBuilder: () => const [MenuEntry('断开穿过它的连线', danger: true)],
        child: Center(
          child: Container(
            width: 17,
            height: 17,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: t.wellDeep,
              border: Border.all(
                color: selected
                    ? t.edgeActive
                    : tint.withAlpha(selected ? 255 : 190),
                width: 2.4,
              ),
              boxShadow: t.raised(depth: .6),
            ),
            // Same ring-plus-core read as a port socket, so a relay looks like
            // a socket you can park a wire on.
            child: Center(
              child: Container(
                width: 5.4,
                height: 5.4,
                decoration: BoxDecoration(color: tint, shape: BoxShape.circle),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// The translucent grouping box. Only its title bar is draggable so the body
/// never swallows canvas panning or wire clicks underneath it.
class CommentNodeView extends StatelessWidget {
  const CommentNodeView({super.key});

  @override
  Widget build(BuildContext context) {
    final scope = NodeScope.of(context);
    final node = scope.node;
    final t = AppScope.of(context);
    final tint = switch (node.read<String>('tone', 'gray')) {
      'green' => const Color(0xFF3E6C4E),
      'blue' => const Color(0xFF3D5A80),
      'orange' => const Color(0xFF7A4A2A),
      'purple' => const Color(0xFF5A3D7A),
      _ => const Color(0xFF4A4A52),
    };

    return SizedBox(
      width: node.size.width,
      height: node.size.height,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Positioned.fill(
            child: IgnorePointer(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: tint.withAlpha(t.isDark ? 42 : 30),
                  borderRadius: BorderRadius.circular(AppTokens.radiusNode),
                  border: Border.all(color: tint.withAlpha(120)),
                ),
              ),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            top: 0,
            height: 26,
            child: DragZone(
              menuBuilder: () => const [
                MenuEntry('换色：绿'),
                MenuEntry('换色：蓝'),
                MenuEntry('换色：橙'),
                MenuEntry('换色：紫'),
                MenuEntry('删除', danger: true),
              ],
              child: Padding(
                padding: const EdgeInsets.fromLTRB(10, 4, 10, 0),
                child: Row(
                  children: [
                    Expanded(
                      child: SizedBox(
                        height: 19,
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            color: t.well.withAlpha(t.isDark ? 200 : 235),
                            borderRadius: BorderRadius.circular(
                              AppTokens.radiusField - 2,
                            ),
                            border: Border.fromBorderSide(
                              BorderSide(color: tint.withAlpha(150)),
                            ),
                            boxShadow: t.inset,
                          ),
                          child: Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 7),
                            child: Row(
                              children: [
                                Dot(color: tint, size: 4.5, glow: true),
                                const SizedBox(width: 7),
                                Expanded(
                                  child: singleLineBox(
                                    TextField(
                                      controller: _nodeCtl(node, 'body'),
                                      textAlignVertical:
                                          TextAlignVertical.center,
                                      style: TextStyle(
                                        fontSize: 11.5,
                                        height: fieldStrutHeight,
                                        color: t.textSecondary,
                                        fontWeight: FontWeight.w600,
                                        letterSpacing: .1,
                                      ),
                                      strutStyle: fieldStrut(fontSize: 11.5),
                                      cursorColor: t.accent,
                                      cursorWidth: 1.2,
                                      decoration: const InputDecoration(
                                        isDense: true,
                                        contentPadding: EdgeInsets.zero,
                                        border: InputBorder.none,
                                      ),
                                      onSubmitted: (v) => node.set('body', v),
                                    ),
                                    fontSize: 11.5,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// The per-category light leak that stands in for Unreal's saturated title
/// bar: a Blueprint card is told apart the same way the Image Generator
/// already is — by the colour bleeding out of its top-right corner.
class CategoryLeakPainter extends CustomPainter {
  const CategoryLeakPainter({required this.tint, required this.tokens});

  final Color tint;
  final AppTokens tokens;

  @override
  void paint(Canvas canvas, Size size) {
    final a = tokens.isDark ? 1.0 : 0.5;
    void blob(Offset center, double r, int alpha) {
      final paint = Paint()
        ..shader = RadialGradient(
          colors: [tint.withAlpha(alpha), tint.withAlpha(0)],
          stops: const [0, 1],
        ).createShader(Rect.fromCircle(center: center, radius: r));
      canvas.drawCircle(center, r, paint);
    }

    blob(
      Offset(size.width * .78, size.height * .01),
      size.width * .40,
      (86 * a).round(),
    );
    blob(
      Offset(size.width * .99, size.height * .10),
      size.width * .30,
      (60 * a).round(),
    );
  }

  @override
  bool shouldRepaint(CategoryLeakPainter old) =>
      old.tint != tint || old.tokens != tokens;
}
