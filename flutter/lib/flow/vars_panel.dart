import 'dart:async';

import 'package:flutter/material.dart';

import '../app/field_metrics.dart';
import '../app/tokens.dart';
import '../chrome/nav_rail.dart';
import '../chrome/primitives.dart';
import 'flow_editor.dart';

/// 事件流抽屉里的变量表：左栏是模块登记的系统变量（只读，名字跟着模块自己的
/// 配置页走），右栏是用户在这里登记的临时变量（可改名、可换向、可删）。
/// 点左边的柄按当前页建该变量的读数 / 写入卡。
class VarsPanel extends StatefulWidget {
  const VarsPanel({
    super.key,
    required this.flow,
    required this.onSpawn,
    this.matches,
  });

  final FlowEditor flow;
  final void Function(String defKey) onSpawn;
  final bool Function(String text)? matches;

  @override
  State<VarsPanel> createState() => _VarsPanelState();
}

class _VarsPanelState extends State<VarsPanel> {
  Timer? _timer;
  bool _adding = false;

  @override
  void initState() {
    super.initState();
    // 实时值只有引擎知道，面板开着的时候按秒取一份轻量的两栏清单。
    _refresh();
    _timer = Timer.periodic(
      const Duration(milliseconds: 1200),
      (_) => _refresh(),
    );
  }

  void _refresh() => unawaited(widget.flow.refreshVars());

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  bool _shown(Map<String, Object?> row) {
    final matches = widget.matches;
    if (matches == null) return true;
    return matches('${row['name'] ?? ''}') || matches('${row['label'] ?? ''}');
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: widget.flow,
      builder: (context, _) {
        final system = [
          for (final r in widget.flow.systemVars)
            if (_shown(r)) r,
        ];
        final free = [
          for (final r in widget.flow.freeVars)
            if (_shown(r)) r,
        ];
        return ListView(
          padding: const EdgeInsets.fromLTRB(8, 2, 8, 8),
          children: [
            SectionLabel('系统变量 · 模块登记（${system.length}）'),
            const SizedBox(height: 4),
            if (system.isNotEmpty)
              Padding(
                padding: const EdgeInsets.fromLTRB(4, 0, 4, 4),
                child: TinyLabel(
                  '这一栏只读：名字与读写由模块自己的配置页决定。',
                  color: t.textTertiary,
                  size: 9,
                ),
              ),
            if (system.isEmpty)
              const Padding(
                padding: EdgeInsets.fromLTRB(4, 2, 4, 6),
                child: EmptyNote('（模块未启动 / 设备未连接）'),
              ),
            for (final r in system)
              // 系统变量只读：名字跟着模块自己的配置页走，这里给改名框只会让人
              // 以为改得动，实际还得回模块那一栏。
              _VarTile(key: ValueKey('var-sys-${r['name']}'), row: r, onSpawn: _spawn),
            const SizedBox(height: 8),
            Padding(
              padding: const EdgeInsets.fromLTRB(0, 0, 0, 4),
              child: Row(
                children: [
                  Expanded(
                    child: SectionLabel('临时变量 · 自己登记（${free.length}）'),
                  ),
                  IconSquare(
                    key: const ValueKey('var-add'),
                    icon: _adding ? Icons.close_rounded : Icons.add_rounded,
                    size: 22,
                    iconSize: 13,
                    active: _adding,
                    onTap: () => setState(() => _adding = !_adding),
                  ),
                ],
              ),
            ),
            if (_adding)
              Padding(
                padding: const EdgeInsets.fromLTRB(2, 0, 2, 6),
                child: _NewVarField(
                  onSubmit: (name) {
                    if (name.trim().isNotEmpty) {
                      unawaited(widget.flow.addVar(name.trim()));
                    }
                    setState(() => _adding = false);
                  },
                  onCancel: () => setState(() => _adding = false),
                ),
              ),
            if (free.isEmpty && !_adding)
              const Padding(
                padding: EdgeInsets.fromLTRB(4, 2, 4, 6),
                child: EmptyNote('（点右上角「＋」登记一个变量名）'),
              ),
            for (final r in free)
              _VarTile(
                key: ValueKey('var-${r['name']}'),
                row: r,
                onSpawn: _spawn,
                onRename: (next) =>
                    unawaited(widget.flow.renameVar('${r['name']}', next)),
                onCycleDir: () => unawaited(widget.flow.cycleVarDir(r)),
                onDelete: () =>
                    unawaited(widget.flow.removeVar('${r['name']}')),
              ),
            if (system.isEmpty && free.isEmpty && !_adding)
              Padding(
                padding: const EdgeInsets.fromLTRB(4, 6, 4, 2),
                child: TinyLabel(
                  '模块参数在模块自己的配置页登记，登记好就出现在上面这一栏；'
                  '这里点「＋」只加自己的临时变量。',
                  color: t.textTertiary,
                ),
              ),
          ],
        );
      },
    );
  }

  void _spawn(Map<String, Object?> row) {
    final key = '${row['card'] ?? ''}';
    if (key.isEmpty) {
      widget.flow.engine.say('这个变量还没有可建的卡片');
      return;
    }
    widget.onSpawn(key);
  }
}

/// 一行变量：建卡手柄 + 名称（可改名时是输入框）+ 来源 / 类型 + 实时值 + 读写 + 删除。
class _VarTile extends StatefulWidget {
  const _VarTile({
    super.key,
    required this.row,
    required this.onSpawn,
    this.onRename,
    this.onCycleDir,
    this.onDelete,
  });

  final Map<String, Object?> row;
  final void Function(Map<String, Object?> row) onSpawn;
  final void Function(String next)? onRename;
  final VoidCallback? onCycleDir;
  final VoidCallback? onDelete;

  @override
  State<_VarTile> createState() => _VarTileState();
}

class _VarTileState extends State<_VarTile> {
  late final TextEditingController _ctl = TextEditingController(text: _name);
  final FocusNode _focus = FocusNode();

  String get _name => '${widget.row['name'] ?? ''}';
  bool get _editable => widget.onRename != null;

  @override
  void initState() {
    super.initState();
    _focus.addListener(() {
      if (!_focus.hasFocus) _commit();
    });
  }

  @override
  void didUpdateWidget(_VarTile old) {
    super.didUpdateWidget(old);
    // 引擎回传同一次改名的结果时不要覆盖正在输入的内容。
    if ('${old.row['name'] ?? ''}' != _name && !_focus.hasFocus) {
      _ctl.text = _name;
    }
  }

  @override
  void dispose() {
    _ctl.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _commit() {
    final next = _ctl.text.trim();
    if (next.isNotEmpty && next != _name) {
      widget.onRename?.call(next);
      return;
    }
    // 改名没成（空名或同名）要把框收回原值，但此刻焦点回调还在派发，
    // 直接改文本会撞上输入系统的编辑状态更新，留到下一帧。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && !_focus.hasFocus) _ctl.text = _name;
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final row = widget.row;
    final value = flowValueText(row['value']);
    final dir = '${row['dir'] ?? 'in'}';
    final sub = [
      _moduleTag(row),
      _typeLabel('${row['type'] ?? ''}'),
      if ('${row['label'] ?? ''}'.isNotEmpty) '${row['label']}',
    ].where((s) => s.isNotEmpty).join(' · ');
    return Container(
      height: 38,
      margin: const EdgeInsets.only(bottom: 3),
      padding: const EdgeInsets.symmetric(horizontal: 4),
      decoration: BoxDecoration(
        color: t.well,
        borderRadius: BorderRadius.circular(AppTokens.radiusField),
        border: Border.all(color: t.hairline),
      ),
      child: Row(
        children: [
          // 拖到画布上就在落点建这张变量的读数 / 写入卡；点一下则放在视野正中。
          _DraggableGrip(
            card: '${row['card'] ?? ''}',
            label: _name,
            onTap: () => widget.onSpawn(row),
          ),
          const SizedBox(width: 3),
          Expanded(
            // 改名框和来源 / 类型那一行一起给：模块登记的系统变量既要能改名，
            // 也要看得出自哪个模块、什么类型——只剩一个输入框就读不懂这一行。
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (_editable)
                  SizedBox(
                    height: 21,
                    child: singleLineBox(
                      TextField(
                        key: ValueKey('var-rename-$_name'),
                        controller: _ctl,
                        focusNode: _focus,
                        maxLines: 1,
                        textAlignVertical: TextAlignVertical.center,
                        onTap: () => _focus.requestFocus(),
                        onSubmitted: (_) => _commit(),
                        style: TextStyle(
                          fontFamily: 'Consolas',
                          fontSize: 11,
                          height: fieldStrutHeight,
                          color: t.textPrimary,
                        ),
                        strutStyle: fieldStrut(
                          fontSize: 11,
                          fontFamily: 'Consolas',
                        ),
                        decoration: InputDecoration(
                          isDense: true,
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 4,
                          ),
                          filled: true,
                          fillColor: t.wellDeep,
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(5),
                            borderSide: BorderSide(color: t.hairline),
                          ),
                          enabledBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(5),
                            borderSide: BorderSide(color: t.hairline),
                          ),
                          focusedBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(5),
                            borderSide: BorderSide(color: t.edgeActive),
                          ),
                        ),
                      ),
                      fontSize: 11,
                    ),
                  )
                else
                  Text(
                    _name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontFamily: 'Consolas',
                      fontSize: 11,
                      height: 1.2,
                      color: t.textSecondary,
                    ),
                  ),
                if (sub.isNotEmpty)
                  Text(
                    sub,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 9,
                      height: 1.2,
                      color: t.textTertiary,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 4),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 92),
            child: Text(
              value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.right,
              style: TextStyle(
                fontFamily: 'Consolas',
                fontSize: 10.5,
                height: 1.2,
                color: row['value'] == null ? t.textTertiary : t.textPrimary,
              ),
            ),
          ),
          const SizedBox(width: 4),
          _DirChip(
            key: ValueKey('var-dir-$_name'),
            dir: dir,
            toggled: widget.onCycleDir != null,
            onTap: widget.onCycleDir ?? () {},
          ),
          const SizedBox(width: 3),
          SizedBox(
            width: 22,
            height: 22,
            child: widget.onDelete == null
                ? const SizedBox.shrink()
                : IconSquare(
                    key: ValueKey('var-del-$_name'),
                    icon: Icons.delete_outline_rounded,
                    size: 22,
                    iconSize: 13,
                    tint: t.portNegative,
                    onTap: widget.onDelete!,
                  ),
          ),
        ],
      ),
    );
  }
}

/// 变量行的手柄：按住可以拖到画布建卡，单击则在视野正中建。
///
/// Draggable 用的是平移识别器，点击仍会落到 onTap，两者不冲突。
class _DraggableGrip extends StatelessWidget {
  const _DraggableGrip({
    required this.card,
    required this.label,
    required this.onTap,
  });

  final String card;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final grip = IconSquare(
      key: ValueKey('var-spawn-$label'),
      icon: Icons.drag_indicator_rounded,
      size: 22,
      iconSize: 13,
      onTap: onTap,
    );
    if (card.isEmpty) return grip;
    return Draggable<String>(
      data: card,
      dragAnchorStrategy: pointerDragAnchorStrategy,
      feedback: Material(
        color: Colors.transparent,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
          decoration: BoxDecoration(
            color: t.surfaceRaised,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: t.pillBorder),
            boxShadow: t.raised(depth: 1.2),
          ),
          child: Text(
            '$label → 卡片',
            style: TextStyle(fontSize: 10.5, height: 1, color: t.textSecondary),
          ),
        ),
      ),
      child: grip,
    );
  }
}

/// 读写方向胶囊：点一下在 读 → 写 → 读写 之间循环（模块登记的行不可点）。
class _DirChip extends StatelessWidget {
  const _DirChip({
    super.key,
    required this.dir,
    required this.toggled,
    required this.onTap,
  });

  final String dir;
  final bool toggled;
  final VoidCallback onTap;

  static const _marks = {'in': '读', 'out': '写', 'inout': '读写'};

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return GestureDetector(
      onTap: toggled ? onTap : null,
      child: Container(
        height: 18,
        padding: const EdgeInsets.symmetric(horizontal: 6),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: toggled ? t.surfaceRaised : Colors.transparent,
          borderRadius: BorderRadius.circular(9),
          border: Border.all(
            color: toggled ? t.pillBorder : Colors.transparent,
          ),
        ),
        child: Text(
          _marks[dir] ?? dir,
          style: TextStyle(
            fontSize: 9.5,
            height: 1,
            color: toggled ? t.accent : t.textTertiary,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }
}

/// 「＋ 新增」展开出来的内联输入框：回车登记，Esc 取消。
class _NewVarField extends StatefulWidget {
  const _NewVarField({required this.onSubmit, required this.onCancel});

  final void Function(String name) onSubmit;
  final VoidCallback onCancel;

  @override
  State<_NewVarField> createState() => _NewVarFieldState();
}

class _NewVarFieldState extends State<_NewVarField> {
  final TextEditingController _ctl = TextEditingController();
  final FocusNode _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _focus.requestFocus());
  }

  @override
  void dispose() {
    _ctl.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      children: [
        Expanded(
          child: SizedBox(
            height: 26,
            child: singleLineBox(
              TextField(
                key: const ValueKey('var-new-name'),
                controller: _ctl,
                focusNode: _focus,
                maxLines: 1,
                textAlignVertical: TextAlignVertical.center,
                onSubmitted: widget.onSubmit,
                style: TextStyle(
                  fontFamily: 'Consolas',
                  fontSize: 11,
                  height: fieldStrutHeight,
                  color: t.textPrimary,
                ),
                strutStyle: fieldStrut(fontSize: 11, fontFamily: 'Consolas'),
                decoration: InputDecoration(
                  isDense: true,
                  contentPadding: const EdgeInsets.symmetric(horizontal: 7),
                  filled: true,
                  fillColor: t.wellDeep,
                  hintText: '变量名，如 combo',
                  hintStyle: fieldHintStyle(
                    t.textTertiary,
                    fontSize: 11,
                    fontFamily: 'Consolas',
                  ),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(6),
                    borderSide: BorderSide(color: t.hairline),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(6),
                    borderSide: BorderSide(color: t.hairline),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(6),
                    borderSide: BorderSide(color: t.edgeActive),
                  ),
                ),
              ),
              fontSize: 11,
            ),
          ),
        ),
        const SizedBox(width: 4),
        IconSquare(
          key: const ValueKey('var-add-ok'),
          icon: Icons.check_rounded,
          size: 26,
          iconSize: 14,
          onTap: () => widget.onSubmit(_ctl.text),
        ),
        const SizedBox(width: 3),
        IconSquare(
          key: const ValueKey('var-add-cancel'),
          icon: Icons.close_rounded,
          size: 26,
          iconSize: 14,
          onTap: widget.onCancel,
        ),
      ],
    );
  }
}

/// 引擎发来的原始值 → 面板上的实时值文本，规则与引擎的 value_to_text 一致。
String flowValueText(Object? value) {
  if (value == null) return '—';
  if (value is bool) return value ? '真' : '假';
  if (value is num) {
    if (value is int) return '$value';
    final near = value.round();
    if ((value - near).abs() < 1e-6 && value.abs() < 1e12) return '$near';
    return value
        .toStringAsFixed(3)
        .replaceAll(RegExp(r'0+$'), '')
        .replaceAll(RegExp(r'\.$'), '');
  }
  return '$value';
}

String _moduleTag(Map<String, Object?> row) {
  final head = '${row['source'] ?? ''}'.split('（').first.trim();
  return head.isEmpty || head == '核心' || head == '临时变量' ? '' : head;
}

const _typeLabels = {
  'exec': '执行',
  'float': '数值',
  'int': '整数',
  'bool': '布尔',
  'str': '文本',
  'any': '通用',
};

String _typeLabel(String type) =>
    _typeLabels[type.toLowerCase()] ?? (type.isEmpty ? '' : type);
