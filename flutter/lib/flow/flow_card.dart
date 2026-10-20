import 'dart:math' as math;

import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../app/field_metrics.dart';
import '../app/tokens.dart';
import '../chrome/nav_rail.dart';
import '../chrome/primitives.dart';
import '../graph/graph_model.dart';
import '../graph/node_chrome.dart';
import 'flow_editor.dart';
import 'flow_models.dart';

/// 把编辑器递到每张卡片手里，避免逐层传回调。
class FlowScope extends InheritedWidget {
  const FlowScope({super.key, required this.editor, required super.child});

  final FlowEditor editor;

  static FlowScope? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<FlowScope>();

  @override
  bool updateShouldNotify(FlowScope old) => old.editor != editor;
}

/// 引擎目录里那些分类色，画布上只用来点一颗头牌圆点。
const Map<String, Color> flowCategoryColors = {
  '核心写入': Color(0xFF2563EB),
  '核心读出': Color(0xFF0D9488),
  '模块事件': Color(0xFFD97706),
  '模块参数': Color(0xFFB45309),
  '流程控制': Color(0xFFDC2626),
  '数学': Color(0xFF7C3AED),
  '范围': Color(0xFF8B5CF6),
  '逻辑': Color(0xFFE11D48),
  '常数': Color(0xFF059669),
  '变量': Color(0xFF0EA5A0),
  // 与 dglab/event_flow.py 的 CATEGORY_COLORS 一一对应：变量表拖出来的卡片是这一类。
  '临时变量': Color(0xFF0891B2),
  '表达式': Color(0xFF4F46E5),
  '实用': Color(0xFF64748B),
  '失效': Color(0xFF71717A),
};

/// 类型色当标题色用要先调对比度：深色下提亮一档、浅色下压深一档，
/// 直接拿饱和色写字在两种主题下都不够读。
Color _titleIn(Color tint, AppTokens t) => t.isDark
    ? Color.lerp(tint, Colors.white, .42)!
    : Color.lerp(tint, Colors.black, .18)!;

/// 一张事件流卡片：标题、左右引脚标签、下面的参数行。
class FlowNodeView extends StatelessWidget {
  const FlowNodeView({super.key});

  @override
  Widget build(BuildContext context) {
    final scope = NodeScope.of(context);
    final node = scope.node;
    final flow = FlowScope.maybeOf(context)?.editor;
    final t = AppScope.of(context);
    final def = flow?.defOf(node);
    final selected = scope.graph.selection.contains(node.id);
    // 灰显：执行流走不到的卡片按「失效」那一类的中性灰画，内容整体压暗一层，
    // 只有卡片上那行红字保持全亮——不然看不出它是被谁顶掉的。
    final idle = flow?.state.idle.contains(node.id) ?? false;
    final note = flow?.state.errors[node.id];
    final tint = def == null
        ? null
        : (idle
              ? flowCategoryColors['失效']
              : (flowCategoryColors[def.category] ?? t.accent));
    final title = '${node.data['title'] ?? node.title}';
    final pinRows = math.max(
      ((def?.execIn ?? false) ? 1 : 0) + (def?.inputs.length ?? 0),
      def?.outputs.length ?? 0,
    );

    return NodeCard(
      width: node.size.width,
      height: node.size.height,
      selected: selected,
      padding: EdgeInsets.zero,
      glow: tint,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppTokens.radiusNode),
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            Positioned(
              left: 9,
              right: 9,
              top: flowHeaderHeight + 4,
              bottom: 7,
              child: const IgnorePointer(
                child: WellBox(child: SizedBox.expand()),
              ),
            ),
            Positioned(
              left: 0,
              right: 0,
              top: 0,
              height: flowHeaderHeight + 6,
              child: DragZone(
                menuBuilder: () => [
                  const MenuEntry('选中'),
                  const MenuEntry('复制并另起一张'),
                  MenuEntry('删除', danger: true),
                ],
                // 类型色落在牌头上：一条本色顶边 + 同色淡底 + 同色标题，
                // 一眼能分「写入 / 读出 / 逻辑 / 数学」，不用逐个点开看。
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    color: tint?.withAlpha(t.isDark ? 44 : 26),
                    border: tint == null
                        ? null
                        : Border(top: BorderSide(color: tint, width: 2.5)),
                    borderRadius: const BorderRadius.vertical(
                      top: Radius.circular(AppTokens.radiusNode),
                    ),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(11, 4, 11, 0),
                    child: NodeHeader(
                      title,
                      bullet: tint ?? t.textSecondary,
                      titleColor: tint == null ? null : _titleIn(tint, t),
                      height: flowHeaderHeight - 4,
                      trailing: _HeadTail(def: def, node: node, editor: flow),
                    ),
                  ),
                ),
              ),
            ),
            for (final p in node.ports)
              // 镜像页（输入页）引脚换边，标签跟着换：不然标签留在出脚那侧，
              // 看上去像另一张卡的入脚。
              Positioned(
                left: (p.dir == PortDir.input) != node.mirror ? 17 : null,
                right: (p.dir == PortDir.input) != node.mirror ? null : 17,
                top: p.dy - flowRowHeight / 2,
                height: flowRowHeight,
                child: _PinText(
                  spec: p,
                  nodeId: node.id,
                  alignLeft: (p.dir == PortDir.input) != node.mirror,
                  mirror: node.mirror,
                ),
              ),
            if (def != null)
              for (var i = 0; i < def.fields.length; i++)
                Positioned(
                  left: 15,
                  right: 15,
                  top: flowHeaderHeight + 10 + (pinRows + i) * flowRowHeight,
                  height: flowRowHeight,
                  child: FlowFieldRow(
                    def: def,
                    field: def.fields[i],
                    node: node,
                  ),
                ),
            // 失效的那张蒙一层本色：内容退到底板上，但卡片仍能选中、拖动、删掉。
            if (idle)
              Positioned.fill(
                child: IgnorePointer(
                  child: DecoratedBox(
                    key: ValueKey('idle-veil-${node.id}'),
                    decoration: BoxDecoration(
                      color: t.surface.withAlpha(150),
                      borderRadius: BorderRadius.circular(AppTokens.radiusNode),
                    ),
                  ),
                ),
              ),
            if (note != null)
              // 标签压在整张卡的正中（上下左右都居中）：卡片矮、内容挤，放在边上
              // 不是被裁就是被邻卡挡住。带一块底板是让它压在引脚与字段上也能读清；
              // 不吃事件，底下那一格的参数照常能改。完整那句只进引擎日志。
              Positioned.fill(
                child: IgnorePointer(
                  child: Center(
                    child: Container(
                      margin: const EdgeInsets.symmetric(horizontal: 10),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 11,
                        vertical: 5,
                      ),
                      decoration: BoxDecoration(
                        color: t.surface,
                        borderRadius: BorderRadius.circular(AppTokens.radiusPill),
                        border: Border.all(color: t.portNegative.withAlpha(90)),
                        boxShadow: t.raised(depth: .9),
                      ),
                      child: Text(
                        note.split('：').first,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: 13,
                          height: 1.15,
                          letterSpacing: .5,
                          fontWeight: FontWeight.w700,
                          color: t.portNegative,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// 卡片头部右侧：这一拍的实时值 + 分类标签。
///
/// 读数卡一直显示自己读到的值；写入卡只在输入引脚接了线时才显示——没接线就没有
/// 「正在传出去的东西」，显示 0 会让人以为模块在写零。
class _HeadTail extends StatelessWidget {
  const _HeadTail({required this.def, required this.node, this.editor});

  final FlowDef? def;
  final GraphNode node;
  final FlowEditor? editor;

  String? get _live {
    final raw = node.data['live'];
    if (raw is! List || raw.isEmpty) return null;
    final key = '${node.data['defKey']}';
    final read = key.contains('.read.');
    final write = key.contains('.write.');
    if (!read && !write) return null;
    if (write) {
      final wired =
          editor?.state.wires.any(
            (w) => w.dstNode == node.id && w.dstIndex == 0,
          ) ??
          false;
      if (!wired) return null;
    }
    return '${raw.first}';
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final live = _live;
    final category = def?.category ?? '';
    if (live == null) {
      return category.isEmpty
          ? const SizedBox.shrink()
          : Text(
              category,
              style: TextStyle(
                fontSize: 8.5,
                height: 1,
                color: t.textTertiary,
                letterSpacing: .6,
              ),
            );
    }
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          height: 15,
          padding: const EdgeInsets.symmetric(horizontal: 5),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: t.wellDeep,
            borderRadius: BorderRadius.circular(4),
            border: Border.all(color: Colors.black.withAlpha(90)),
          ),
          child: Text(
            live,
            style: TextStyle(
              fontFamily: 'Consolas',
              fontSize: 9.5,
              height: 1,
              color: t.textSecondary,
            ),
          ),
        ),
        if (category.isNotEmpty) ...[
          const SizedBox(width: 5),
          Text(
            category,
            style: TextStyle(
              fontSize: 8.5,
              height: 1,
              color: t.textTertiary,
              letterSpacing: .6,
            ),
          ),
        ],
      ],
    );
  }
}

/// 执行箭头的朝向：只有执行引脚画箭头，方向就是这一页数据走的方向。
///
/// 输出页从左往右（▶），输入页整页镜像、从右往左（◀）。数据引脚不画箭头——
/// 数据线本身没有方向性，画了反而每列两种朝向混在一起看不出所以然。
bool execArrowPointsRight({required bool mirror}) => !mirror;

/// 引脚标签（执行引脚额外带一颗方向箭头）。
///
/// 执行口要标出流向；数据口只写名字，读入还是写出看它贴着卡片哪条边。
class _PinText extends StatelessWidget {
  const _PinText({
    required this.spec,
    required this.nodeId,
    required this.alignLeft,
    required this.mirror,
  });

  final PortSpec spec;
  final String nodeId;
  final bool alignLeft;
  final bool mirror;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final wired = GraphLabel.wiredOf(context, nodeId, spec.id);
    final color = wired ? t.textSecondary : t.textTertiary;
    final label = Text(
      spec.label,
      style: TextStyle(
        fontSize: 10.5,
        height: 1,
        letterSpacing: .1,
        fontWeight: FontWeight.w600,
        color: color,
      ),
    );
    if (!spec.isExec) {
      return SizedBox(
        height: flowRowHeight,
        child: Align(
          alignment: alignLeft ? Alignment.centerLeft : Alignment.centerRight,
          child: label,
        ),
      );
    }
    final arrow = PinArrow(
      key: ValueKey('pin-arrow-${spec.id}-$nodeId'),
      pointsRight: execArrowPointsRight(mirror: mirror),
      color: color,
    );
    return SizedBox(
      height: flowRowHeight,
      child: Align(
        alignment: alignLeft ? Alignment.centerLeft : Alignment.centerRight,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: alignLeft
              ? [arrow, const SizedBox(width: 2), label]
              : [label, const SizedBox(width: 2), arrow],
        ),
      ),
    );
  }
}

/// 引脚方向箭头：一颗实心三角，指向数据真正走的那一边。
///
/// 单独做成组件是为了让测试能直接读「这一颗朝哪边」，不用去解变换矩阵。
class PinArrow extends StatelessWidget {
  const PinArrow({
    super.key,
    required this.pointsRight,
    required this.color,
    this.size = 11,
  });

  final bool pointsRight;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Transform.rotate(
      angle: pointsRight ? 0 : math.pi,
      child: Icon(Icons.play_arrow_rounded, size: size, color: color),
    );
  }
}

/// 一行参数：左边标签，右边按类型给的编辑控件。
class FlowFieldRow extends StatelessWidget {
  const FlowFieldRow({
    super.key,
    required this.def,
    required this.field,
    required this.node,
  });

  final FlowDef def;
  final FlowField field;
  final GraphNode node;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final flow = FlowScope.maybeOf(context)?.editor;
    final raw = node.data['params'];
    final params = raw is Map
        ? Map<String, Object?>.from(raw)
        : const <String, Object?>{};
    final value = params.containsKey(field.id)
        ? params[field.id]
        : field.defaultValue;

    return Row(
      children: [
        SizedBox(
          width: 74,
          child: Text(
            field.label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: 10,
              height: 1,
              color: t.textTertiary,
              letterSpacing: .1,
            ),
          ),
        ),
        const SizedBox(width: 6),
        Expanded(
          child: Align(
            alignment: Alignment.centerRight,
            child: switch (field.type) {
              'bool' => SwitchPill(
                on: value == true,
                onTap: () =>
                    flow?.setParam(node.id, field.id, !(value == true)),
              ),
              'int' => _IntControl(field: field, value: value, node: node),
              'float' => _IntControl(field: field, value: value, node: node),
              'choice' => PickerField(
                width: 108,
                compact: true,
                value: _choiceLabel(field, value),
                options: _labelsOf(field),
                onChanged: (label) {
                  final i = _labelsOf(field).indexOf(label);
                  flow?.setParam(
                    node.id,
                    field.id,
                    i >= 0 ? field.choices[i] : label,
                  );
                },
              ),
              _ => _TextControl(field: field, value: value, node: node),
            },
          ),
        ),
      ],
    );
  }

  static List<String> _labelsOf(FlowField f) =>
      f.labels.isNotEmpty ? f.labels : f.choices;

  static String _choiceLabel(FlowField f, Object? value) {
    final i = f.choices.indexOf('$value');
    return i >= 0 && _labelsOf(f).length > i ? _labelsOf(f)[i] : '$value';
  }
}

class _IntControl extends StatelessWidget {
  const _IntControl({
    required this.field,
    required this.value,
    required this.node,
  });

  final FlowField field;
  final Object? value;
  final GraphNode node;

  @override
  Widget build(BuildContext context) {
    final flow = FlowScope.maybeOf(context)?.editor;
    final numeric = value is num
        ? (value as num).toDouble()
        : (field.defaultValue is num
              ? (field.defaultValue as num).toDouble()
              : 0);
    final isFloat = field.type == 'float';
    final step = isFloat ? 0.1 : 1.0;
    final low = (field.min ?? 0).toDouble();
    final high = (field.max ?? 9999).toDouble();
    return _MiniStepper(
      text: isFloat ? numeric.toStringAsFixed(2) : numeric.round().toString(),
      onCommit: (raw) {
        final parsed = num.tryParse(raw);
        if (parsed == null) return;
        final next = (isFloat ? parsed.toDouble() : parsed.round().toDouble())
            .clamp(low, high);
        flow?.setParam(
          node.id,
          field.id,
          isFloat ? double.parse(next.toStringAsFixed(3)) : next.round(),
        );
      },
      onStep: (d) {
        final next = (numeric + d * step).clamp(low, high);
        flow?.setParam(
          node.id,
          field.id,
          isFloat ? double.parse(next.toStringAsFixed(3)) : next.round(),
        );
      },
    );
  }
}

/// 文本 / 变量名参数：边打字边提交，卡片不会因为回传重建而丢焦点。
class _TextControl extends StatefulWidget {
  const _TextControl({
    required this.field,
    required this.value,
    required this.node,
  });

  final FlowField field;
  final Object? value;
  final GraphNode node;

  @override
  State<_TextControl> createState() => _TextControlState();
}

class _TextControlState extends State<_TextControl> {
  late final TextEditingController _ctl = TextEditingController(
    text: '${widget.value ?? ''}',
  );
  final FocusNode _focus = FocusNode();

  @override
  void didUpdateWidget(_TextControl old) {
    super.didUpdateWidget(old);
    // 引擎回传同一次编辑的结果时不要覆盖用户正在输入的内容。
    if ('${old.value ?? ''}' != '${widget.value ?? ''}' && !_focus.hasFocus) {
      _ctl.text = '${widget.value ?? ''}';
    }
  }

  @override
  void dispose() {
    _ctl.dispose();
    _focus.dispose();
    super.dispose();
  }

  /// 变量名一栏的联想：只在用户开始打字后列候选（和旧界面一样不做「一聚焦就弹」），
  /// 候选来自变量表两栏，命中变量名或备注即可。
  void _suggest(String text) {
    if (widget.field.pool != 'var') return;
    final flow = FlowScope.maybeOf(context)?.editor;
    if (flow == null || text.trim().isEmpty) return;
    final q = text.trim().toLowerCase();
    final hits = <(String, String)>[
      for (final r in [...flow.systemVars, ...flow.freeVars])
        if ('${r['name']}'.toLowerCase().contains(q) ||
            '${r['label']}'.toLowerCase().contains(q))
          ('${r['name']}', '${r['source'] ?? ''}'),
    ];
    if (hits.isEmpty) return;
    final box = context.findRenderObject();
    if (box is! RenderBox || !box.hasSize) return;
    PopoverScope.of(context)?.show(
      PopoverSpec(
        title: '变量',
        anchorRect: box.localToGlobal(Offset.zero) & box.size,
        width: 210,
        entries: [
          for (final h in hits.take(12))
            MenuEntry(h.$1, hint: h.$2, selected: h.$1 == text),
        ],
        onPick: (i) {
          final name = hits[i].$1;
          _ctl.text = name;
          flow.setParam(widget.node.id, widget.field.id, name);
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final flow = FlowScope.maybeOf(context)?.editor;
    final t = AppScope.of(context);
    return SizedBox(
      width: 118,
      height: 18,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: t.wellDeep,
          borderRadius: BorderRadius.circular(5),
          border: Border.all(color: Colors.black.withAlpha(110)),
          boxShadow: t.inset,
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 5),
          child: singleLineBox(
            TextField(
              controller: _ctl,
              focusNode: _focus,
              maxLines: 1,
              textAlign: TextAlign.right,
              textAlignVertical: TextAlignVertical.center,
              onChanged: (v) {
                flow?.setParam(widget.node.id, widget.field.id, v);
                _suggest(v);
              },
              style: TextStyle(
                fontFamily: 'Consolas',
                fontSize: 10.5,
                height: fieldStrutHeight,
                color: t.textSecondary,
              ),
              strutStyle: fieldStrut(fontSize: 10.5, fontFamily: 'Consolas'),
              decoration: InputDecoration(
                isDense: true,
                contentPadding: EdgeInsets.zero,
                border: InputBorder.none,
                hintText: widget.field.label,
                hintStyle: fieldHintStyle(
                  t.textTertiary,
                  fontSize: 10.5,
                  fontFamily: 'Consolas',
                ),
              ),
            ),
            fontSize: 10.5,
          ),
        ),
      ),
    );
  }
}

/// 卡片里的数值格：左右翻手步进，中间可以直接敲。
class _MiniStepper extends StatefulWidget {
  const _MiniStepper({required this.text, required this.onStep, this.onCommit});

  final String text;
  final ValueChanged<int> onStep;

  /// 交回来的是用户敲进去的原始串（已去空格）；由 [_IntControl] 负责解析与钳位。
  final ValueChanged<String>? onCommit;

  @override
  State<_MiniStepper> createState() => _MiniStepperState();
}

class _MiniStepperState extends State<_MiniStepper> {
  late final TextEditingController _ctl = TextEditingController(
    text: widget.text,
  );
  final FocusNode _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _focus.addListener(() {
      if (!_focus.hasFocus) _commit();
    });
  }

  @override
  void didUpdateWidget(_MiniStepper old) {
    super.didUpdateWidget(old);
    // 引擎回传同一个值时不要覆盖正在敲的内容。
    if (old.text != widget.text && !_focus.hasFocus) _ctl.text = widget.text;
  }

  @override
  void dispose() {
    _ctl.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _commit() {
    final raw = _ctl.text.trim();
    if (raw.isEmpty || raw == widget.text) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && !_focus.hasFocus) _ctl.text = widget.text;
      });
      return;
    }
    widget.onCommit?.call(raw);
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconSquare(
          icon: Icons.chevron_left_rounded,
          size: 20,
          iconSize: 12,
          onTap: () => widget.onStep(-1),
        ),
        Container(
          // TextField 要有界宽度：按当前值算格子，和 stepperWidthFor 的估算一致。
          width: math.max(44.0, measureText(widget.text) + 12),
          margin: const EdgeInsets.symmetric(horizontal: 2),
          padding: const EdgeInsets.symmetric(horizontal: 5),
          height: 18,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: t.wellDeep,
            borderRadius: BorderRadius.circular(5),
            border: Border.all(color: Colors.black.withAlpha(110)),
            boxShadow: t.inset,
          ),
          child: singleLineBox(
            TextField(
              controller: _ctl,
              focusNode: _focus,
              maxLines: 1,
              textAlign: TextAlign.center,
              textAlignVertical: TextAlignVertical.center,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
                signed: true,
              ),
              inputFormatters: [
                FilteringTextInputFormatter.allow(RegExp(r'[0-9.\-]')),
              ],
              onSubmitted: (_) => _commit(),
              style: TextStyle(
                fontFamily: 'Consolas',
                fontSize: 10.5,
                height: fieldStrutHeight,
                color: t.textSecondary,
              ),
              strutStyle: fieldStrut(fontSize: 10.5, fontFamily: 'Consolas'),
              decoration: const InputDecoration(
                isDense: true,
                contentPadding: EdgeInsets.zero,
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
              ),
            ),
            fontSize: 10.5,
          ),
        ),
        IconSquare(
          icon: Icons.chevron_right_rounded,
          size: 20,
          iconSize: 12,
          onTap: () => widget.onStep(1),
        ),
      ],
    );
  }
}

/// 没有事件流编辑器时原样返回，图像工作流那套画布不必感知这层。
class FlowScopeWrap extends StatelessWidget {
  const FlowScopeWrap({super.key, required this.editor, required this.child});

  final FlowEditor? editor;
  final Widget child;

  @override
  Widget build(BuildContext context) =>
      editor == null ? child : FlowScope(editor: editor!, child: child);
}
